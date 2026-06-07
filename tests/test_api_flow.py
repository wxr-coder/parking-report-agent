import asyncio
import json
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.config import Settings
from app.db import JobRepository
from app.main import create_app
from app.models import JobStage, JobStatus
from app.services import jobs
from app.services.jobs import (
    PUBLIC_GENERATION_ERROR,
    JobService,
    QueueFullError,
    UploadValidationError,
)


def test_index_builds_formdata_before_disabling_file_inputs() -> None:
    html = Path("app/templates/index.html").read_text(encoding="utf-8")

    form_data_index = html.index("const formData = new FormData(form);")
    disable_inputs_index = html.index("setRunning(true);", form_data_index)
    fetch_body_index = html.index("body: formData", disable_inputs_index)

    assert form_data_index < disable_inputs_index < fetch_body_index


def test_submit_status_download_with_generation_mock(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        storage_dir=tmp_path / "storage",
        database_path=tmp_path / "jobs.sqlite3",
        openai_api_key=None,
    )

    def fake_submit(self, *, template_file, data_file, instructions):
        job_id = uuid4().hex
        job_dir = self.settings.jobs_dir / job_id
        job_dir.mkdir(parents=True)
        output_path = job_dir / "generated_report.docx"
        output_path.write_bytes(b"mock docx")
        record = self.repository.create(
            job_id=job_id,
            template_filename=template_file.filename or "template.docx",
            data_filename=data_file.filename or "data.csv",
            instructions=instructions,
        )
        self.repository.update_status(
            job_id,
            JobStatus.completed,
            stage=JobStage.completed,
            result_path=str(output_path),
            error=None,
        )
        return record

    monkeypatch.setattr("app.services.jobs.JobService.submit", fake_submit)
    app = create_app(settings)

    post_body, content_type = _multipart_body(
        fields={"instructions": "test"},
        files={
            "template_file": (
                "template.docx",
                Path("sample/停车明细分析报告_模板.docx").read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            "data_file": ("data.csv", Path("sample/data.csv").read_bytes(), "text/csv"),
        },
    )
    response = _request(app, "POST", "/jobs", body=post_body, headers={"content-type": content_type})

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["status"] == "queued"
    job_id = payload["job_id"]

    status_response = _request(app, "GET", f"/jobs/{job_id}")
    assert status_response.status_code == 200
    status_payload = json.loads(status_response.body)

    assert status_payload["status"] == "completed"
    assert status_payload["stage"] == "completed"
    assert status_payload["attempts"] == 0
    assert status_payload["max_attempts"] == 3
    assert status_payload["download_url"] == f"/jobs/{job_id}/download"

    download_response = _request(app, "GET", f"/jobs/{job_id}/download")
    assert download_response.status_code == 200
    assert download_response.body == b"mock docx"
    assert (
        download_response.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_status_does_not_expose_traceback_for_failed_job(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        storage_dir=tmp_path / "storage",
        database_path=tmp_path / "jobs.sqlite3",
        openai_api_key=None,
        job_max_attempts=1,
    )
    repository = JobRepository(settings.database_path)
    repository.init()
    service = JobService(settings, repository)
    record = repository.create(
        job_id="failed-job",
        template_filename="template.docx",
        data_filename="data.csv",
        instructions=None,
        max_attempts=settings.job_max_attempts,
    )

    def fail_generation(job_id, settings, repository):
        raise RuntimeError("sensitive /srv/app/path")

    monkeypatch.setattr(jobs, "generate_report_for_job", fail_generation)
    service.process_next_job()
    app = create_app(settings)

    response = _request(app, "GET", "/jobs/failed-job")
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["status"] == "failed"
    assert payload["error"] == PUBLIC_GENERATION_ERROR
    assert payload["attempts"] == 1
    assert payload["max_attempts"] == 1
    assert "Traceback" not in payload["error"]
    assert "/srv/app/path" not in payload["error"]


def test_failed_job_retries_before_final_failure(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        storage_dir=tmp_path / "storage",
        database_path=tmp_path / "jobs.sqlite3",
        openai_api_key=None,
        job_max_attempts=2,
        job_retry_base_seconds=1,
    )
    repository = JobRepository(settings.database_path)
    repository.init()
    service = JobService(settings, repository)
    record = repository.create(
        job_id="retry-job",
        template_filename="template.docx",
        data_filename="data.csv",
        instructions=None,
        max_attempts=settings.job_max_attempts,
    )

    def fail_generation(job_id, settings, repository):
        raise RuntimeError("transient failure")

    monkeypatch.setattr(jobs, "generate_report_for_job", fail_generation)
    processed = service.process_next_job()

    assert processed is not None
    assert processed.job_id == record.job_id
    assert processed.status == JobStatus.queued
    assert processed.error == PUBLIC_GENERATION_ERROR
    assert processed.attempts == 1
    assert processed.max_attempts == 2
    assert processed.next_run_at is not None
    assert processed.locked_by is None
    assert processed.locked_at is None


def test_submit_rejects_invalid_docx_content(tmp_path: Path) -> None:
    settings = Settings(
        storage_dir=tmp_path / "storage",
        database_path=tmp_path / "jobs.sqlite3",
        openai_api_key=None,
    )
    app = create_app(settings)
    post_body, content_type = _multipart_body(
        fields={},
        files={
            "template_file": (
                "template.docx",
                b"not a docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            "data_file": ("data.csv", Path("sample/data.csv").read_bytes(), "text/csv"),
        },
    )

    response = _request(app, "POST", "/jobs", body=post_body, headers={"content-type": content_type})
    payload = json.loads(response.body)

    assert response.status_code == 400
    assert payload["detail"] == "template_file must be a valid .docx archive"


def test_submit_requires_api_key_when_configured(tmp_path: Path) -> None:
    settings = Settings(
        storage_dir=tmp_path / "storage",
        database_path=tmp_path / "jobs.sqlite3",
        openai_api_key=None,
        submit_api_key="secret",
    )
    app = create_app(settings)
    post_body, content_type = _multipart_body(
        fields={},
        files={
            "template_file": (
                "template.docx",
                Path("sample/停车明细分析报告_模板.docx").read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            "data_file": ("data.csv", Path("sample/data.csv").read_bytes(), "text/csv"),
        },
    )

    response = _request(app, "POST", "/jobs", body=post_body, headers={"content-type": content_type})
    payload = json.loads(response.body)

    assert response.status_code == 401
    assert payload["detail"] == "invalid API key"


def test_job_service_rejects_oversized_upload(tmp_path: Path) -> None:
    settings = Settings(
        storage_dir=tmp_path / "storage",
        database_path=tmp_path / "jobs.sqlite3",
        openai_api_key=None,
        max_upload_bytes=1024,
    )
    repository = JobRepository(settings.database_path)
    repository.init()
    service = JobService(settings, repository)

    try:
        service.submit(
            template_file=UploadFile(file=BytesIO(b"x" * 2048), filename="template.docx"),
            data_file=UploadFile(
                file=BytesIO(Path("sample/data.csv").read_bytes()),
                filename="data.csv",
            ),
            instructions=None,
        )
    except UploadValidationError as exc:
        assert "exceeds the configured size limit" in str(exc)
    else:
        raise AssertionError("expected oversized upload to be rejected")


def test_job_service_rejects_when_queue_is_full(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        storage_dir=tmp_path / "storage",
        database_path=tmp_path / "jobs.sqlite3",
        openai_api_key=None,
        max_pending_jobs=1,
    )
    repository = JobRepository(settings.database_path)
    repository.init()
    service = JobService(settings, repository)
    monkeypatch.setattr(service.executor, "submit", lambda *args, **kwargs: None)

    service.submit(
        template_file=UploadFile(
            file=BytesIO(Path("sample/停车明细分析报告_模板.docx").read_bytes()),
            filename="template.docx",
        ),
        data_file=UploadFile(
            file=BytesIO(Path("sample/data.csv").read_bytes()),
            filename="data.csv",
        ),
        instructions=None,
    )

    try:
        service.submit(
            template_file=UploadFile(
                file=BytesIO(Path("sample/停车明细分析报告_模板.docx").read_bytes()),
                filename="template.docx",
            ),
            data_file=UploadFile(
                file=BytesIO(Path("sample/data.csv").read_bytes()),
                filename="data.csv",
            ),
            instructions=None,
        )
    except QueueFullError:
        pass
    else:
        raise AssertionError("expected queue full rejection")


class AsgiResponse:
    def __init__(self, status_code: int, headers: dict[str, str], body: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self.body = body


def _request(app, method: str, path: str, *, body: bytes = b"", headers=None) -> AsgiResponse:
    return asyncio.run(_async_request(app, method, path, body=body, headers=headers or {}))


async def _async_request(app, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> AsgiResponse:
    sent = False
    messages = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)

    status_code = 500
    response_headers: dict[str, str] = {}
    chunks: list[bytes] = []
    for message in messages:
        if message["type"] == "http.response.start":
            status_code = message["status"]
            response_headers = {
                key.decode().lower(): value.decode()
                for key, value in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
    return AsgiResponse(status_code, response_headers, b"".join(chunks))


def _multipart_body(*, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----parking-report-agent-test-boundary"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )
    for name, (filename, content, content_type) in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
