"""Sync wrappers around the async DB session.

Celery runs with the default ``prefork`` pool, which means each task executes
in a fresh OS process; that maps cleanly onto ``asyncio.run`` per task. We
deliberately *do not* keep a long-lived engine because forks share file
descriptors with the parent which breaks asyncpg.

Pattern of choice: **sync function wraps an async coroutine via asyncio.run**.
The coroutine opens an AsyncSession from a module-local engine lazily created
on first use. Each task thus pays one round trip to set up + tear down the
engine (~few ms with asyncpg's connection pool); this is acceptable for the
~seconds-to-minutes tasks we run.

Alternative considered: ``anyio.from_thread.run``. Rejected because it
requires an outer event loop, which prefork workers don't have.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, TypeVar
from uuid import UUID

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared_py.enums import ClipStage, JobStatus

T = TypeVar("T")


_DEFAULT_DSN = "postgresql+asyncpg://factory:factory@postgres:5432/factory"


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN)


def run_async(coro_factory: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Synchronously run an async function that needs a DB session.

    ``coro_factory`` is a callable that accepts an open :class:`AsyncSession`
    and returns a coroutine. We build and tear down the engine per call so the
    sync wrapper is safe inside Celery's fork-based prefork pool.
    """

    async def _body() -> T:
        async with _session() as session:
            try:
                result = await coro_factory(session)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise

    # ``asyncio.run`` creates+closes a fresh loop each call.
    return asyncio.run(_body())


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    """Per-call engine + session. See module docstring for rationale."""
    engine = create_async_engine(
        _database_url(),
        future=True,
        echo=os.environ.get("SQL_ECHO", "0") == "1",
        # Small pool: each task only ever opens one session.
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Convenience helpers used by tasks
# ---------------------------------------------------------------------------


def mark_job_failed_sync(*, job_id: str | UUID, error: str, stage: str | None = None) -> None:
    """Set ``jobs.status = failed`` and stash the error message.

    Best-effort: if the DB is unavailable we log and return -- the on_failure
    hook should never raise (it's already inside an exception path).
    """
    from database.models import Job

    async def _body(session: AsyncSession) -> None:
        result = await session.execute(select(Job).where(Job.id == _coerce_uuid(job_id)))
        job = result.scalar_one_or_none()
        if job is None:
            logger.warning("mark_job_failed_sync: job {} not found", job_id)
            return
        job.status = JobStatus.FAILED
        job.error_message = error[:8000]
        if stage:
            job.current_stage = stage
        job.finished_at = datetime.utcnow()

    try:
        run_async(_body)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mark_job_failed_sync failed for job={}: {}", job_id, exc)


def update_job_progress_sync(
    *,
    job_id: str | UUID,
    stage: str | None = None,
    pct: float | None = None,
    status: JobStatus | None = None,
) -> None:
    """Update one or more progress fields on a job row."""
    from database.models import Job

    async def _body(session: AsyncSession) -> None:
        values: dict[str, Any] = {}
        if stage is not None:
            values["current_stage"] = stage
        if pct is not None:
            values["progress_pct"] = float(pct)
        if status is not None:
            values["status"] = status
            if status in (JobStatus.COMPLETED, JobStatus.FAILED):
                values["finished_at"] = datetime.utcnow()
        if not values:
            return
        await session.execute(
            update(Job).where(Job.id == _coerce_uuid(job_id)).values(**values)
        )

    run_async(_body)


def update_clip_status_sync(
    *,
    clip_id: str | UUID,
    status: ClipStage,
    error: str | None = None,
) -> None:
    """Update one clip's status (and optionally an error message)."""
    from database.models import Clip

    async def _body(session: AsyncSession) -> None:
        values: dict[str, Any] = {"status": status}
        await session.execute(
            update(Clip).where(Clip.id == _coerce_uuid(clip_id)).values(**values)
        )
        if error is not None:
            logger.info("clip {} status={} note={}", clip_id, status, error[:120])

    run_async(_body)


def _coerce_uuid(value: str | UUID) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))
