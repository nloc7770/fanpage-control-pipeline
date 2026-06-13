"""Business logic for Job CRUD and lifecycle.

These functions take an `AsyncSession` so they remain pure and testable. The
router layer is responsible for committing, dispatching Celery, and publishing
events.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Asset, Clip, Job
from shared_py.enums import JobStatus

from app.errors import ConflictError, NotFoundError


async def create_job(session: AsyncSession, source_url: str) -> Job:
    """Insert a new `Job` row in QUEUED status and return it.

    Raises `ConflictError` if the same `source_url` was queued within the
    configured duplicate window.
    """
    window = datetime.now(timezone.utc) - timedelta(seconds=60)
    stmt = (
        select(Job)
        .where(Job.source_url == source_url)
        .where(Job.status == JobStatus.QUEUED)
        .where(Job.created_at >= window)
        .limit(1)
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            "Job for this URL was queued within the last minute",
            details={"existing_job_id": str(existing.id)},
        )

    job = Job(source_url=source_url, status=JobStatus.QUEUED, progress_pct=0.0)
    session.add(job)
    await session.flush()
    await session.refresh(job)
    return job


async def get_job(session: AsyncSession, job_id: UUID) -> Job:
    """Return a `Job` or raise `NotFoundError`."""
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"Job {job_id} not found")
    return job


async def list_jobs(
    session: AsyncSession,
    *,
    limit: int = 20,
    offset: int = 0,
    statuses: list[JobStatus] | None = None,
) -> tuple[list[Job], int]:
    """Return (items, total) for a paginated job listing."""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    filters = []
    if statuses:
        filters.append(Job.status.in_(statuses))

    list_stmt = select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
    count_stmt = select(func.count()).select_from(Job)
    for f in filters:
        list_stmt = list_stmt.where(f)
        count_stmt = count_stmt.where(f)

    items = list((await session.execute(list_stmt)).scalars().all())
    total = int((await session.execute(count_stmt)).scalar_one())
    return items, total


async def get_job_clips(session: AsyncSession, job_id: UUID) -> list[Clip]:
    """Clips for a job, ordered by `clip_index`. Verifies the job exists."""
    await get_job(session, job_id)
    stmt = select(Clip).where(Clip.job_id == job_id).order_by(Clip.clip_index.asc())
    return list((await session.execute(stmt)).scalars().all())


async def count_clips(session: AsyncSession, job_id: UUID) -> int:
    stmt = select(func.count()).select_from(Clip).where(Clip.job_id == job_id)
    return int((await session.execute(stmt)).scalar_one())


async def count_assets(session: AsyncSession, job_id: UUID) -> int:
    stmt = select(func.count()).select_from(Asset).where(Asset.job_id == job_id)
    return int((await session.execute(stmt)).scalar_one())


async def get_asset(session: AsyncSession, asset_id: UUID) -> Asset:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise NotFoundError(f"Asset {asset_id} not found")
    return asset


async def list_assets_by_kind(
    session: AsyncSession, job_id: UUID, kind: str
) -> list[Asset]:
    """Return all assets matching `(job_id, kind)`, newest first.

    Used by the artifact-inspection endpoint to surface the latest persisted
    artifact (and, for kinds that produce multiple files such as
    `edit_plan_json`, the full set).
    """
    stmt = (
        select(Asset)
        .where(Asset.job_id == job_id)
        .where(Asset.kind == kind)
        .order_by(Asset.created_at.desc(), Asset.id.desc())
    )
    return list((await session.execute(stmt)).scalars().all())
