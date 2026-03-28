"""SQLite-backed job storage for the web service."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


ACTIVE_JOB_STATUSES = ("initiated", "uploaded", "processing")
RECOVERABLE_TERMINAL_STATUSES = ("completed",)


@dataclass(slots=True)
class JobRecord:
    """Database representation of a web extraction job."""

    id: str
    client_ip_hash: str
    original_filename: str
    file_size: int
    output_format: str
    status: str
    progress_pct: int
    progress_message: str
    source_key: str
    result_key: str | None
    frame_count: int | None
    error_message: str | None
    created_at: int
    uploaded_at: int | None
    started_at: int | None
    completed_at: int | None
    expires_at: int
    download_count: int


class JobStore:
    """Encapsulate all SQLite interactions for job tracking."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        """Create the schema when needed."""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    client_ip_hash TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    output_format TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_pct INTEGER NOT NULL DEFAULT 0,
                    progress_message TEXT NOT NULL DEFAULT '',
                    source_key TEXT NOT NULL,
                    result_key TEXT,
                    frame_count INTEGER,
                    error_message TEXT,
                    created_at INTEGER NOT NULL,
                    uploaded_at INTEGER,
                    started_at INTEGER,
                    completed_at INTEGER,
                    expires_at INTEGER NOT NULL,
                    download_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_client_created ON jobs(client_ip_hash, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_expires_at ON jobs(expires_at)"
            )

    def healthcheck(self) -> None:
        """Verify the database is reachable."""
        with self._connect() as conn:
            conn.execute("SELECT 1").fetchone()

    def create_job(
        self,
        *,
        job_id: str,
        client_ip_hash: str,
        original_filename: str,
        file_size: int,
        output_format: str,
        source_key: str,
        created_at: int,
        expires_at: int,
    ) -> JobRecord:
        """Insert a newly initiated job."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id,
                    client_ip_hash,
                    original_filename,
                    file_size,
                    output_format,
                    status,
                    progress_pct,
                    progress_message,
                    source_key,
                    created_at,
                    expires_at,
                    download_count
                ) VALUES (?, ?, ?, ?, ?, 'initiated', 0, 'Awaiting upload', ?, ?, ?, 0)
                """,
                (
                    job_id,
                    client_ip_hash,
                    original_filename,
                    file_size,
                    output_format,
                    source_key,
                    created_at,
                    expires_at,
                ),
            )
        job = self.get_job(job_id)
        if job is None:
            raise RuntimeError("Failed to create job record")
        return job

    def get_job(self, job_id: str) -> JobRecord | None:
        """Fetch a job by ID."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self) -> list[JobRecord]:
        """Return all jobs, newest first."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC, id DESC").fetchall()
        return [self._row_to_job(row) for row in rows]

    def count_recent_jobs_for_ip(self, client_ip_hash: str, since_ts: int) -> int:
        """Count jobs created by an IP hash since a given timestamp."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS job_count FROM jobs WHERE client_ip_hash = ? AND created_at >= ?",
                (client_ip_hash, since_ts),
            ).fetchone()
        return int(row["job_count"]) if row else 0

    def has_active_job_for_ip(self, client_ip_hash: str) -> bool:
        """Return True when the IP already has an active initiated/uploaded/processing job."""
        placeholders = ", ".join("?" for _ in ACTIVE_JOB_STATUSES)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT 1
                FROM jobs
                WHERE client_ip_hash = ?
                  AND status IN ({placeholders})
                LIMIT 1
                """,
                (client_ip_hash, *ACTIVE_JOB_STATUSES),
            ).fetchone()
        return row is not None

    def get_recoverable_job_for_ip(self, client_ip_hash: str, now_ts: int) -> JobRecord | None:
        """Return the most relevant unexpired job for a client IP."""
        active_placeholders = ", ".join("?" for _ in ACTIVE_JOB_STATUSES)
        terminal_placeholders = ", ".join("?" for _ in RECOVERABLE_TERMINAL_STATUSES)

        with self._connect() as conn:
            active_row = conn.execute(
                f"""
                SELECT *
                FROM jobs
                WHERE client_ip_hash = ?
                  AND expires_at > ?
                  AND status IN ({active_placeholders})
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (client_ip_hash, now_ts, *ACTIVE_JOB_STATUSES),
            ).fetchone()
            if active_row is not None:
                return self._row_to_job(active_row)

            terminal_row = conn.execute(
                f"""
                SELECT *
                FROM jobs
                WHERE client_ip_hash = ?
                  AND expires_at > ?
                  AND status IN ({terminal_placeholders})
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (client_ip_hash, now_ts, *RECOVERABLE_TERMINAL_STATUSES),
            ).fetchone()

        return self._row_to_job(terminal_row) if terminal_row is not None else None

    def mark_uploaded(self, job_id: str, uploaded_at: int) -> JobRecord | None:
        """Move a job from initiated to uploaded."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'uploaded',
                    uploaded_at = ?,
                    progress_pct = 5,
                    progress_message = 'Upload complete. Waiting for worker.'
                WHERE id = ? AND status = 'initiated'
                """,
                (uploaded_at, job_id),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_job(job_id)

    def claim_next_uploaded_job(self, started_at: int) -> JobRecord | None:
        """Atomically claim the oldest uploaded job for processing."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM jobs
                WHERE status = 'uploaded'
                ORDER BY uploaded_at ASC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute(
                """
                UPDATE jobs
                SET status = 'processing',
                    started_at = ?,
                    progress_pct = 10,
                    progress_message = 'Worker claimed the job.'
                WHERE id = ? AND status = 'uploaded'
                """,
                (started_at, row["id"]),
            )
            conn.commit()
        return self.get_job(str(row["id"]))

    def update_progress(
        self,
        job_id: str,
        progress_pct: int,
        progress_message: str,
        *,
        frame_count: int | None = None,
    ) -> None:
        """Update progress for a running job."""
        with self._connect() as conn:
            if frame_count is None:
                conn.execute(
                    """
                    UPDATE jobs
                    SET progress_pct = ?, progress_message = ?
                    WHERE id = ?
                    """,
                    (progress_pct, progress_message, job_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE jobs
                    SET progress_pct = ?, progress_message = ?, frame_count = ?
                    WHERE id = ?
                    """,
                    (progress_pct, progress_message, frame_count, job_id),
                )

    def mark_completed(
        self,
        job_id: str,
        *,
        result_key: str,
        frame_count: int,
        completed_at: int,
        expires_at: int,
    ) -> None:
        """Mark a job as completed and downloadable."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'completed',
                    progress_pct = 100,
                    progress_message = 'ZIP ready for download.',
                    result_key = ?,
                    frame_count = ?,
                    error_message = NULL,
                    completed_at = ?,
                    expires_at = ?
                WHERE id = ?
                """,
                (result_key, frame_count, completed_at, expires_at, job_id),
            )

    def mark_failed(self, job_id: str, *, error_message: str, completed_at: int) -> None:
        """Mark a job as failed."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    progress_message = ?,
                    error_message = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (error_message, error_message, completed_at, job_id),
            )

    def record_download_request(self, job_id: str, *, expires_at: int) -> JobRecord | None:
        """Increment the download counter and shorten expiry after the first download link."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET download_count = download_count + 1,
                    expires_at = CASE
                        WHEN expires_at < ? THEN expires_at
                        ELSE ?
                    END
                WHERE id = ? AND status = 'completed'
                """,
                (expires_at, expires_at, job_id),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_job(job_id)

    def list_expired_jobs(self, now_ts: int) -> list[JobRecord]:
        """Return jobs whose expiry time has passed."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE expires_at <= ? ORDER BY expires_at ASC",
                (now_ts,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def delete_jobs(self, job_ids: list[str]) -> None:
        """Delete jobs from the database."""
        if not job_ids:
            return
        placeholders = ", ".join("?" for _ in job_ids)
        with self._connect() as conn:
            conn.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", tuple(job_ids))

    def delete_all_jobs(self) -> None:
        """Delete every job from the database."""
        with self._connect() as conn:
            conn.execute("DELETE FROM jobs")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=str(row["id"]),
            client_ip_hash=str(row["client_ip_hash"]),
            original_filename=str(row["original_filename"]),
            file_size=int(row["file_size"]),
            output_format=str(row["output_format"]),
            status=str(row["status"]),
            progress_pct=int(row["progress_pct"]),
            progress_message=str(row["progress_message"]),
            source_key=str(row["source_key"]),
            result_key=str(row["result_key"]) if row["result_key"] is not None else None,
            frame_count=int(row["frame_count"]) if row["frame_count"] is not None else None,
            error_message=str(row["error_message"]) if row["error_message"] is not None else None,
            created_at=int(row["created_at"]),
            uploaded_at=int(row["uploaded_at"]) if row["uploaded_at"] is not None else None,
            started_at=int(row["started_at"]) if row["started_at"] is not None else None,
            completed_at=int(row["completed_at"]) if row["completed_at"] is not None else None,
            expires_at=int(row["expires_at"]),
            download_count=int(row["download_count"]),
        )
