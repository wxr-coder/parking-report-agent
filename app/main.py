from contextlib import asynccontextmanager
from pathlib import Path
from secrets import compare_digest

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.db import JobRepository
from app.logging_config import configure_logging
from app.models import JobStatus, JobStatusResponse, JobSubmitted
from app.services.jobs import JobService, QueueFullError, UploadValidationError


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)
    repository = JobRepository(app_settings.database_path)
    job_service = JobService(app_settings, repository)
    app_settings.storage_dir.mkdir(parents=True, exist_ok=True)
    app_settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    repository.init()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app_settings.storage_dir.mkdir(parents=True, exist_ok=True)
        app_settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        repository.init()
        app.state.settings = app_settings
        app.state.repository = repository
        app.state.job_service = job_service
        yield
        job_service.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Parking Report Agent", version="0.1.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.repository = repository
    app.state.job_service = job_service
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "templates"), name="static")

    def get_job_service() -> JobService:
        return app.state.job_service

    def get_repository() -> JobRepository:
        return app.state.repository

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        template_path = Path(__file__).parent / "templates" / "index.html"
        return template_path.read_text(encoding="utf-8")

    @app.post("/jobs", response_model=JobSubmitted)
    def submit_job(
        request: Request,
        template_file: UploadFile = File(...),
        data_file: UploadFile = File(...),
        instructions: str | None = Form(default=None),
        service: JobService = Depends(get_job_service),
    ) -> JobSubmitted:
        if not (template_file.filename or "").lower().endswith(".docx"):
            raise HTTPException(status_code=400, detail="template_file must be a .docx file")
        if not (data_file.filename or "").lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail="data_file must be a .csv file")
        if app_settings.submit_api_key and not compare_digest(
            request.headers.get("x-api-key", ""),
            app_settings.submit_api_key,
        ):
            raise HTTPException(status_code=401, detail="invalid API key")
        content_length = request.headers.get("content-length")
        if (
            content_length
            and content_length.isdecimal()
            and int(content_length) > app_settings.max_upload_bytes * 2
        ):
            raise HTTPException(status_code=413, detail="upload request is too large")
        try:
            record = service.submit(
                template_file=template_file,
                data_file=data_file,
                instructions=instructions,
            )
        except QueueFullError as exc:
            raise HTTPException(status_code=429, detail="job queue is full; try again later") from exc
        except UploadValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JobSubmitted(job_id=record.job_id, status=record.status)

    @app.get("/jobs/{job_id}", response_model=JobStatusResponse)
    def get_job(
        job_id: str,
        repository: JobRepository = Depends(get_repository),
    ) -> JobStatusResponse:
        record = repository.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="job not found")
        return JobStatusResponse(
            job_id=record.job_id,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            error=record.error,
            download_url=f"/jobs/{job_id}/download" if record.status == JobStatus.completed else None,
        )

    @app.get("/jobs/{job_id}/download")
    def download_job(
        job_id: str,
        repository: JobRepository = Depends(get_repository),
    ) -> FileResponse:
        record = repository.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="job not found")
        if record.status != JobStatus.completed or not record.result_path:
            raise HTTPException(status_code=409, detail="job is not completed")
        path = Path(record.result_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="generated report is missing")
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="parking_report.docx",
        )

    return app


app = create_app()
