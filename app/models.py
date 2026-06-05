from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class JobRecord(BaseModel):
    job_id: str
    status: JobStatus
    template_filename: str
    data_filename: str
    instructions: str | None = None
    result_path: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobSubmitted(BaseModel):
    job_id: str
    status: JobStatus


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    download_url: str | None = None
