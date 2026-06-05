import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import JobRecord, JobStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def init(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    template_filename TEXT NOT NULL,
                    data_filename TEXT NOT NULL,
                    instructions TEXT,
                    result_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def create(
        self,
        *,
        job_id: str,
        template_filename: str,
        data_filename: str,
        instructions: str | None,
    ) -> JobRecord:
        now = utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, status, template_filename, data_filename, instructions,
                    result_path, error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    job_id,
                    JobStatus.queued.value,
                    template_filename,
                    data_filename,
                    instructions,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return self.get_required(job_id)

    def get(self, job_id: str) -> JobRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def get_required(self, job_id: str) -> JobRecord:
        record = self.get(job_id)
        if record is None:
            raise KeyError(job_id)
        return record

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result_path: str | None = None,
        error: str | None = None,
    ) -> JobRecord:
        now = utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, result_path = COALESCE(?, result_path), error = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (status.value, result_path, error, now, job_id),
            )
        return self.get_required(job_id)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> JobRecord:
        data: dict[str, Any] = dict(row)
        data["status"] = JobStatus(data["status"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return JobRecord(**data)
