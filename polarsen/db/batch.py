import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import asyncpg
from tracktolib.pg import insert_returning

from .utils import TableID

__all__ = ("JOB_TYPE_DISCUSSIONS", "JOB_TYPE_GEMINI_BATCH", "BatchJob", "BatchStatus")

BatchStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
JOB_TYPE_DISCUSSIONS = "discussions"
JOB_TYPE_GEMINI_BATCH = "gemini_batch"


@dataclass
class BatchJob(TableID):
    """Wrapper for general.batch_jobs table for batch processing."""

    job_type: str
    user_id: int
    status: BatchStatus = "pending"
    model_name: str | None = None
    model_source: str | None = None
    run_id: uuid.UUID | None = None
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    error_message: str | None = None
    progress: int = 0
    total_items: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    gemini_job_name: str | None = None  # Gemini API batch job name (e.g., "batches/xxx")

    @property
    def data(self) -> dict[str, Any]:
        """Return data dict for database insertion."""
        return super().data

    @classmethod
    def for_discussions(
        cls,
        user_id: int,
        chat_id: int,
        chat_code: str,
        total_days: int,
        model_name: str,
        model_source: str,
        from_date: dt.date | None = None,
        to_date: dt.date | None = None,
    ) -> BatchJob:
        """Create a BatchJob for discussion processing."""
        return cls(
            job_type=JOB_TYPE_DISCUSSIONS,
            user_id=user_id,
            model_name=model_name,
            model_source=model_source,
            run_id=uuid.uuid4(),
            total_items=total_days,
            meta={
                "chat_id": chat_id,
                "chat_code": chat_code,
                "from_date": from_date.isoformat() if from_date else None,
                "to_date": to_date.isoformat() if to_date else None,
                "processed_messages": 0,
                "total_discussions": 0,
            },
        )

    async def save(self, conn: asyncpg.Connection) -> int:
        """Insert a new batch job record and return the ID."""
        _data = await insert_returning(conn, "general.batch_jobs", self.data, returning=["id", "created_at"])
        if _data is None:
            raise ValueError(f"Failed to save batch job {self.job_type!r}")
        self._id = _data["id"]
        self._created_at = _data["created_at"]
        return _data["id"]

    async def start(self, conn: asyncpg.Connection) -> None:
        """Mark the job as running with start time."""
        self.status = "running"
        self.started_at = dt.datetime.now(dt.UTC)
        await conn.execute(
            """
            UPDATE general.batch_jobs
            SET status = 'running', started_at = $2
            WHERE id = $1
            """,
            self._id,
            self.started_at,
        )

    async def update_progress(
        self,
        conn: asyncpg.Connection,
        progress: int,
        processed_messages: int | None = None,
        total_discussions: int | None = None,
    ) -> None:
        """Update progress counters."""
        self.progress = progress
        if processed_messages is not None:
            self.meta["processed_messages"] = processed_messages
        if total_discussions is not None:
            self.meta["total_discussions"] = total_discussions

        await conn.execute(
            """
            UPDATE general.batch_jobs
            SET progress = $2, meta = $3
            WHERE id = $1
            """,
            self._id,
            self.progress,
            self.meta,
        )

    async def mark_completed(self, conn: asyncpg.Connection) -> None:
        """Mark batch job as completed."""
        self.status = "completed"
        self.completed_at = dt.datetime.now(dt.UTC)
        await conn.execute(
            """
            UPDATE general.batch_jobs
            SET status = 'completed', completed_at = $2
            WHERE id = $1
            """,
            self._id,
            self.completed_at,
        )

    async def mark_failed(self, conn: asyncpg.Connection, error: str) -> None:
        """Mark batch job as failed with error message."""
        self.status = "failed"
        self.error_message = error[:10000] if len(error) > 10000 else error
        self.completed_at = dt.datetime.now(dt.UTC)
        await conn.execute(
            """
            UPDATE general.batch_jobs
            SET status = 'failed', error_message = $2, completed_at = $3
            WHERE id = $1
            """,
            self._id,
            self.error_message,
            self.completed_at,
        )

    @staticmethod
    async def get_active_runs(conn: asyncpg.Connection, job_type: str) -> list[asyncpg.Record]:
        """Get all active (pending/running) batch jobs of a given type."""
        return await conn.fetch(
            """
            SELECT
                bj.*,
                c.name as chat_name,
                c.internal_code as chat_code
            FROM general.batch_jobs bj
            LEFT JOIN general.chats c ON c.id = (bj.meta->>'chat_id')::int
            WHERE bj.job_type = $1
              AND bj.status IN ('pending', 'running')
            ORDER BY bj.created_at DESC
            """,
            job_type,
        )

    @staticmethod
    async def get_recent_runs(conn: asyncpg.Connection, job_type: str, limit: int = 10) -> list[asyncpg.Record]:
        """Get recent batch jobs of a given type with chat info."""
        return await conn.fetch(
            """
            SELECT
                bj.*,
                c.name as chat_name,
                c.internal_code as chat_code
            FROM general.batch_jobs bj
            LEFT JOIN general.chats c ON c.id = (bj.meta->>'chat_id')::int
            WHERE bj.job_type = $1
            ORDER BY bj.created_at DESC
            LIMIT $2
            """,
            job_type,
            limit,
        )

    # --- Gemini Batch Job Methods ---

    @classmethod
    def for_gemini_batch(
        cls,
        user_id: int,
        chat_id: int,
        model_name: str,
        gemini_job_name: str,
        days_data: list[dict],
        run_id: uuid.UUID,
        method_id: int,
        lang: str,
        temperature: float,
        disable_thinking: bool,
    ) -> BatchJob:
        """Create a BatchJob for Gemini API batch processing."""
        return cls(
            job_type=JOB_TYPE_GEMINI_BATCH,
            gemini_job_name=gemini_job_name,
            user_id=user_id,
            model_name=model_name,
            model_source="gemini",
            run_id=run_id,
            total_items=len(days_data),
            meta={
                "chat_id": chat_id,
                "method_id": method_id,
                "lang": lang,
                "temperature": temperature,
                "disable_thinking": disable_thinking,
                "days_data": days_data,
                "processed_messages": 0,
                "total_discussions": 0,
            },
        )

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> BatchJob:
        """Reconstruct a BatchJob from a database record."""
        job = cls(
            job_type=record["job_type"],
            user_id=record["user_id"],
            status=record["status"],
            model_name=record["model_name"],
            model_source=record["model_source"],
            run_id=record["run_id"],
            started_at=record["started_at"],
            completed_at=record["completed_at"],
            error_message=record["error_message"],
            progress=record["progress"],
            total_items=record["total_items"],
            meta=record["meta"] or {},
            gemini_job_name=record["gemini_job_name"],
        )
        job._id = record["id"]
        job._created_at = record["created_at"]
        return job

    @staticmethod
    async def get_pending_gemini_jobs(conn: asyncpg.Connection) -> list[asyncpg.Record]:
        """Get all pending/running Gemini batch jobs."""
        return await conn.fetch(
            """
            SELECT bj.*
            FROM general.batch_jobs bj
            WHERE bj.job_type = $1
              AND bj.status IN ('pending', 'running')
            ORDER BY bj.created_at ASC
            """,
            JOB_TYPE_GEMINI_BATCH,
        )

    @staticmethod
    async def get_gemini_job_by_name(conn: asyncpg.Connection, gemini_job_name: str) -> asyncpg.Record | None:
        """Get a Gemini batch job by its API job name."""
        return await conn.fetchrow(
            """
            SELECT bj.*
            FROM general.batch_jobs bj
            WHERE bj.gemini_job_name = $1
            """,
            gemini_job_name,
        )
