import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import UploadFile

from app.config import Settings
from app.db import JobRepository, QueueCapacityError
from app.models import JobRecord, JobStage, JobStatus
from app.services.agent import build_narrative
from app.services.charts import create_payment_method_chart
from app.services.metrics import compute_metrics
from app.services.report import render_report


logger = logging.getLogger(__name__)


PUBLIC_GENERATION_ERROR = "Report generation failed. Check server logs with the job_id for details."


class QueueFullError(RuntimeError):
    pass


class UploadValidationError(ValueError):
    pass


class JobService:
    def __init__(self, settings: Settings, repository: JobRepository) -> None:
        self.settings = settings
        self.repository = repository
        self.executor = ThreadPoolExecutor(max_workers=settings.worker_count)
        self._stop_event = Event()
        self._wake_event = Event()
        self._started = False

    def submit(
        self,
        *,
        template_file: UploadFile,
        data_file: UploadFile,
        instructions: str | None,
    ) -> JobRecord:
        job_id = uuid4().hex
        job_dir = self.job_dir(job_id)
        try:
            job_dir.mkdir(parents=True, exist_ok=False)
            template_name = _safe_filename(template_file.filename or "template.docx")
            data_name = _safe_filename(data_file.filename or "data.csv")
            template_path = job_dir / template_name
            data_path = job_dir / data_name
            _save_upload(template_file, template_path, max_bytes=self.settings.max_upload_bytes)
            _save_upload(data_file, data_path, max_bytes=self.settings.max_upload_bytes)
            _validate_docx(template_path)
            _validate_csv(data_path)

            record = self.repository.create(
                job_id=job_id,
                template_filename=template_name,
                data_filename=data_name,
                instructions=instructions,
                max_attempts=self.settings.job_max_attempts,
                max_active_jobs=self.settings.max_pending_jobs,
            )
            logger.info(
                "job_submitted",
                extra={
                    "event": "job_submitted",
                    "job_id": job_id,
                    "template_filename": template_name,
                    "data_filename": data_name,
                    "instructions_present": bool(instructions),
                    "status": record.status.value,
                },
            )
            self._wake_event.set()
            return record
        except QueueCapacityError as exc:
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
            raise QueueFullError("job queue is full") from exc
        except Exception:
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
            raise

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        recovered = self.repository.recover_stale_running(
            lock_timeout_seconds=self.settings.job_lock_timeout_seconds
        )
        if recovered:
            logger.info(
                "jobs_recovered",
                extra={"event": "jobs_recovered", "count": recovered},
            )
        for index in range(self.settings.worker_count):
            self.executor.submit(self._worker_loop, f"worker-{index + 1}")

    def shutdown(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def process_next_job(self, worker_id: str = "manual-worker") -> JobRecord | None:
        record = self.repository.claim_next(worker_id)
        if record is None:
            return None
        self.run_generation(record.job_id)
        return self.repository.get_required(record.job_id)

    def run_generation(self, job_id: str) -> None:
        try:
            logger.info("job_started", extra={"event": "job_started", "job_id": job_id})
            output_path = generate_report_for_job(job_id, self.settings, self.repository)
            self.repository.update_status(
                job_id,
                JobStatus.completed,
                stage=JobStage.completed,
                result_path=str(output_path),
                error=None,
            )
            logger.info(
                "job_completed",
                extra={"event": "job_completed", "job_id": job_id, "result_path": str(output_path)},
            )
        except Exception as exc:
            record = self.repository.retry_or_fail(
                job_id,
                public_error=PUBLIC_GENERATION_ERROR,
                retry_base_seconds=self.settings.job_retry_base_seconds,
            )
            logger.exception(
                "job_failed",
                extra={
                    "event": "job_failed",
                    "job_id": job_id,
                    "error_type": type(exc).__name__,
                    "attempts": record.attempts,
                    "max_attempts": record.max_attempts,
                    "retrying": record.status == JobStatus.queued,
                    "next_run_at": record.next_run_at.isoformat() if record.next_run_at else None,
                },
            )

    def job_dir(self, job_id: str) -> Path:
        return self.settings.jobs_dir / job_id

    def _worker_loop(self, worker_id: str) -> None:
        logger.info("queue_worker_started", extra={"event": "queue_worker_started", "worker_id": worker_id})
        while not self._stop_event.is_set():
            record = self.process_next_job(worker_id)
            if record is None:
                self._wake_event.wait(self.settings.job_poll_interval_seconds)
                self._wake_event.clear()
        logger.info("queue_worker_stopped", extra={"event": "queue_worker_stopped", "worker_id": worker_id})


def generate_report_for_job(job_id: str, settings: Settings, repository: JobRepository) -> Path:
    record = repository.get_required(job_id)
    job_dir = settings.jobs_dir / job_id
    template_path = job_dir / record.template_filename
    data_path = job_dir / record.data_filename
    chart_path = job_dir / "payment_methods.png"
    output_path = job_dir / "generated_report.docx"

    repository.update_stage(job_id, JobStage.metrics)
    metrics = compute_metrics(data_path)
    repository.update_stage(job_id, JobStage.chart)
    create_payment_method_chart(metrics, chart_path)
    repository.update_stage(job_id, JobStage.narrative)
    narrative = build_narrative(
        metrics,
        settings,
        job_id=job_id,
        instructions=record.instructions,
    )
    repository.update_stage(job_id, JobStage.report)
    return render_report(
        template_path=template_path,
        output_path=output_path,
        metrics=metrics,
        narrative=narrative,
        chart_path=chart_path,
    )


def _save_upload(upload: UploadFile, destination: Path, *, max_bytes: int) -> None:
    total = 0
    with destination.open("wb") as file:
        while chunk := upload.file.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise UploadValidationError(
                    f"{upload.filename or 'upload'} exceeds the configured size limit"
                )
            file.write(chunk)


def _safe_filename(filename: str) -> str:
    return Path(filename).name.replace("/", "_").replace("\\", "_")


def _validate_docx(path: Path) -> None:
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
    except BadZipFile as exc:
        raise UploadValidationError("template_file must be a valid .docx archive") from exc
    if "word/document.xml" not in names:
        raise UploadValidationError("template_file is missing a Word document body")


def _validate_csv(path: Path) -> None:
    from app.services.metrics import required_csv_columns

    try:
        import csv

        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            fieldnames = reader.fieldnames
    except UnicodeDecodeError as exc:
        raise UploadValidationError("data_file must be a UTF-8 CSV file") from exc
    missing = [column for column in required_csv_columns() if column not in (fieldnames or [])]
    if missing:
        raise UploadValidationError(f"data_file missing required columns: {', '.join(missing)}")
