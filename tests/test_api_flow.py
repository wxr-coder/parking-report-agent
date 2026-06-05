import asyncio
import json
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.main import create_app
from app.models import JobStatus


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
    assert status_payload["download_url"] == f"/jobs/{job_id}/download"

    download_response = _request(app, "GET", f"/jobs/{job_id}/download")
    assert download_response.status_code == 200
    assert download_response.body == b"mock docx"
    assert (
        download_response.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


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
