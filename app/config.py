from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    storage_dir: Path = Field(default=Path("storage"), alias="STORAGE_DIR")
    database_path: Path = Field(default=Path("storage/jobs.sqlite3"), alias="DATABASE_PATH")
    openai_base_url: str = Field(default="https://ark.cn-beijing.volces.com/api/v3", alias="OPENAI_BASE_URL")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="deepseek-v4-flash", alias="OPENAI_MODEL")
    submit_api_key: str | None = Field(default=None, alias="SUBMIT_API_KEY")
    worker_count: int = Field(default=1, ge=1, le=8, alias="WORKER_COUNT")
    max_pending_jobs: int = Field(default=8, ge=1, le=128, alias="MAX_PENDING_JOBS")
    job_max_attempts: int = Field(default=3, ge=1, le=10, alias="JOB_MAX_ATTEMPTS")
    job_retry_base_seconds: int = Field(default=5, ge=1, le=3600, alias="JOB_RETRY_BASE_SECONDS")
    job_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=30.0, alias="JOB_POLL_INTERVAL_SECONDS")
    job_lock_timeout_seconds: int = Field(default=300, ge=30, le=86400, alias="JOB_LOCK_TIMEOUT_SECONDS")
    max_upload_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
        alias="MAX_UPLOAD_BYTES",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        alias="LOG_LEVEL",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def jobs_dir(self) -> Path:
        return self.storage_dir / "jobs"

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


@lru_cache
def get_settings() -> Settings:
    return Settings()
