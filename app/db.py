import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.models import JobRecord, JobStage, JobStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class QueueCapacityError(RuntimeError):
    pass


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
                    stage TEXT NOT NULL DEFAULT 'uploaded',
                    template_filename TEXT NOT NULL,
                    data_filename TEXT NOT NULL,
                    instructions TEXT,
                    result_path TEXT,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    next_run_at TEXT,
                    locked_by TEXT,
                    locked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "stage", "TEXT NOT NULL DEFAULT 'uploaded'")
            self._ensure_column(conn, "attempts", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "max_attempts", "INTEGER NOT NULL DEFAULT 3")
            self._ensure_column(conn, "next_run_at", "TEXT")
            self._ensure_column(conn, "locked_by", "TEXT")
            self._ensure_column(conn, "locked_at", "TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_queue
                ON jobs (status, next_run_at, created_at)
                """
            )

    def create(
        self,
        *,
        job_id: str,
        template_filename: str,
        data_filename: str,
        instructions: str | None,
        max_attempts: int = 3,
        max_active_jobs: int | None = None,
    ) -> JobRecord:
        now = utcnow()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if max_active_jobs is not None:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM jobs WHERE status IN (?, ?)",
                    (JobStatus.queued.value, JobStatus.running.value),
                ).fetchone()
                if int(row["count"]) >= max_active_jobs:
                    conn.rollback()
                    raise QueueCapacityError("job queue is full")
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, status, stage, template_filename, data_filename, instructions,
                    result_path, error, attempts, max_attempts, next_run_at, locked_by,
                    locked_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 0, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    job_id,
                    JobStatus.queued.value,
                    JobStage.uploaded.value,
                    template_filename,
                    data_filename,
                    instructions,
                    max_attempts,
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            conn.commit()
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
        stage: JobStage | None = None,
        result_path: str | None = None,
        error: str | None = None,
    ) -> JobRecord:
        now = utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    stage = COALESCE(?, stage),
                    result_path = COALESCE(?, result_path),
                    error = ?,
                    locked_by = CASE WHEN ? IN ('queued', 'completed', 'failed') THEN NULL ELSE locked_by END,
                    locked_at = CASE WHEN ? IN ('queued', 'completed', 'failed') THEN NULL ELSE locked_at END,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (
                    status.value,
                    stage.value if stage else None,
                    result_path,
                    error,
                    status.value,
                    status.value,
                    now,
                    job_id,
                ),
            )
        return self.get_required(job_id)

    def update_stage(self, job_id: str, stage: JobStage) -> JobRecord:
        now = utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET stage = ?, updated_at = ? WHERE job_id = ?",
                (stage.value, now, job_id),
            )
        return self.get_required(job_id)

    def count_active(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE status IN (?, ?)",
                (JobStatus.queued.value, JobStatus.running.value),
            ).fetchone()
        return int(row["count"])

    def claim_next(self, worker_id: str) -> JobRecord | None:
        now = utcnow().isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT job_id
                FROM jobs
                WHERE status = ?
                  AND (next_run_at IS NULL OR next_run_at <= ?)
                ORDER BY created_at
                LIMIT 1
                """,
                (JobStatus.queued.value, now),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            job_id = row["job_id"]
            conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    attempts = attempts + 1,
                    error = NULL,
                    next_run_at = NULL,
                    locked_by = ?,
                    locked_at = ?,
                    updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (
                    JobStatus.running.value,
                    worker_id,
                    now,
                    now,
                    job_id,
                    JobStatus.queued.value,
                ),
            )
            conn.commit()
        return self.get_required(job_id)

    def retry_or_fail(
        self,
        job_id: str,
        *,
        public_error: str,
        retry_base_seconds: int,
    ) -> JobRecord:
        record = self.get_required(job_id)
        now = utcnow()
        if record.attempts >= record.max_attempts:
            return self.update_status(job_id, JobStatus.failed, error=public_error)

        delay_seconds = retry_base_seconds * (2 ** max(0, record.attempts - 1))
        next_run_at = (now + timedelta(seconds=delay_seconds)).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    error = ?,
                    next_run_at = ?,
                    locked_by = NULL,
                    locked_at = NULL,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (JobStatus.queued.value, public_error, next_run_at, now.isoformat(), job_id),
            )
        return self.get_required(job_id)

    def recover_stale_running(self, *, lock_timeout_seconds: int) -> int:
        cutoff = (utcnow() - timedelta(seconds=lock_timeout_seconds)).isoformat()
        now = utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    next_run_at = ?,
                    locked_by = NULL,
                    locked_at = NULL,
                    updated_at = ?
                WHERE status = ?
                  AND (locked_at IS NULL OR locked_at <= ?)
                """,
                (JobStatus.queued.value, now, now, JobStatus.running.value, cutoff),
            )
            return cursor.rowcount

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, name: str, definition: str) -> None:
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> JobRecord:
        data: dict[str, Any] = dict(row)
        data["status"] = JobStatus(data["status"])
        data["stage"] = JobStage(data["stage"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        data["next_run_at"] = _parse_optional_datetime(data["next_run_at"])
        data["locked_at"] = _parse_optional_datetime(data["locked_at"])
        return JobRecord(**data)


def _parse_optional_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
