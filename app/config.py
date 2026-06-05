from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    storage_dir: Path = Field(default=Path("storage"), alias="STORAGE_DIR")
    database_path: Path = Field(default=Path("storage/jobs.sqlite3"), alias="DATABASE_PATH")
    openai_base_url: str = Field(default="https://ark.cn-beijing.volces.com/api/v3", alias="OPENAI_BASE_URL")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="deepseek-v4-flash", alias="OPENAI_MODEL")
    worker_count: int = Field(default=1, alias="WORKER_COUNT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def jobs_dir(self) -> Path:
        return self.storage_dir / "jobs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
