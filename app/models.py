from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class JobStage(str, Enum):
    uploaded = "uploaded"
    metrics = "metrics"
    chart = "chart"
    narrative = "narrative"
    report = "report"
    completed = "completed"


class JobRecord(BaseModel):
    job_id: str
    status: JobStatus
    stage: JobStage
    template_filename: str
    data_filename: str
    instructions: str | None = None
    result_path: str | None = None
    error: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    next_run_at: datetime | None = None
    locked_by: str | None = None
    locked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class JobSubmitted(BaseModel):
    job_id: str
    status: JobStatus


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    stage: JobStage
    attempts: int
    max_attempts: int
    next_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    download_url: str | None = None
