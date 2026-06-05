import logging
import shutil
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.config import Settings
from app.db import JobRepository
from app.models import JobRecord, JobStatus
from app.services.agent import build_narrative
from app.services.charts import create_payment_method_chart
from app.services.metrics import compute_metrics
from app.services.report import render_report


logger = logging.getLogger(__name__)


class JobService:
    def __init__(self, settings: Settings, repository: JobRepository) -> None:
        self.settings = settings
        self.repository = repository
        self.executor = ThreadPoolExecutor(max_workers=settings.worker_count)

    def submit(
        self,
        *,
        template_file: UploadFile,
        data_file: UploadFile,
        instructions: str | None,
    ) -> JobRecord:
        job_id = uuid4().hex
        job_dir = self.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=False)
        template_name = _safe_filename(template_file.filename or "template.docx")
        data_name = _safe_filename(data_file.filename or "data.csv")
        template_path = job_dir / template_name
        data_path = job_dir / data_name
        _save_upload(template_file, template_path)
        _save_upload(data_file, data_path)

        record = self.repository.create(
            job_id=job_id,
            template_filename=template_name,
            data_filename=data_name,
            instructions=instructions,
        )
        logger.info(
            "job_submitted",
            extra={
                "event": "job_submitted",
                "job_id": job_id,
                "template_filename": template_name,
                "data_filename": data_name,
                "instructions": instructions,
                "status": record.status.value,
            },
        )
        self.executor.submit(self.run_generation, job_id)
        return record

    def run_generation(self, job_id: str) -> None:
        try:
            self.repository.update_status(job_id, JobStatus.running, error=None)
            logger.info("job_started", extra={"event": "job_started", "job_id": job_id})
            output_path = generate_report_for_job(job_id, self.settings, self.repository)
            self.repository.update_status(
                job_id,
                JobStatus.completed,
                result_path=str(output_path),
                error=None,
            )
            logger.info(
                "job_completed",
                extra={"event": "job_completed", "job_id": job_id, "result_path": str(output_path)},
            )
        except Exception as exc:
            self.repository.update_status(
                job_id,
                JobStatus.failed,
                error=f"{exc}\n{traceback.format_exc()}",
            )
            logger.exception("job_failed", extra={"event": "job_failed", "job_id": job_id})

    def job_dir(self, job_id: str) -> Path:
        return self.settings.jobs_dir / job_id


def generate_report_for_job(job_id: str, settings: Settings, repository: JobRepository) -> Path:
    record = repository.get_required(job_id)
    job_dir = settings.jobs_dir / job_id
    template_path = job_dir / record.template_filename
    data_path = job_dir / record.data_filename
    chart_path = job_dir / "payment_methods.png"
    output_path = job_dir / "generated_report.docx"

    metrics = compute_metrics(data_path)
    create_payment_method_chart(metrics, chart_path)
    narrative = build_narrative(
        metrics,
        settings,
        job_id=job_id,
        instructions=record.instructions,
    )
    return render_report(
        template_path=template_path,
        output_path=output_path,
        metrics=metrics,
        narrative=narrative,
        chart_path=chart_path,
    )


def _save_upload(upload: UploadFile, destination: Path) -> None:
    with destination.open("wb") as file:
        shutil.copyfileobj(upload.file, file)


def _safe_filename(filename: str) -> str:
    return Path(filename).name.replace("/", "_").replace("\\", "_")
