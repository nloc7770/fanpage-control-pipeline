"""Shared helpers used by every task module.

Putting these here keeps each individual task file focused on its own
stage-specific logic and avoids circular imports between task modules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared_py.enums import AssetKind, JobStatus
from shared_py.events import (
    JobProgressEvent,
    JobProgressPayload,
    JobStageChangedEvent,
    JobStageChangedPayload,
)

from apps.workers.db_ctx import run_async
from apps.workers.event_publisher import publish_sync

# Progress windows per stage (mirror docs/pipeline.md).
STAGE_PROGRESS = {
    "downloading": (0.0, 15.0),
    "transcribing": (15.0, 40.0),
    "analyzing": (40.0, 55.0),
    "clip_planning": (55.0, 65.0),
    "rendering": (65.0, 100.0),
}


def stage_pct(stage: str, fraction_within_stage: float) -> float:
    """Translate a 0..1 fraction within ``stage`` into a global 0..100 pct."""
    lo, hi = STAGE_PROGRESS.get(stage, (0.0, 100.0))
    fraction_within_stage = max(0.0, min(1.0, fraction_within_stage))
    return lo + (hi - lo) * fraction_within_stage


def publish_progress(
    job_id: str | UUID,
    stage: str,
    pct: float,
    message: str | None = None,
) -> None:
    """Publish a ``job.progress`` SSE event."""
    event = JobProgressEvent(
        job_id=_uuid(job_id),
        payload=JobProgressPayload(stage=stage, pct=pct, message=message),
    )
    publish_sync(str(job_id), event)


def publish_stage_change(
    job_id: str | UUID, from_stage: str | None, to_stage: str
) -> None:
    """Publish a ``job.stage_changed`` SSE event."""
    event = JobStageChangedEvent(
        job_id=_uuid(job_id),
        payload=JobStageChangedPayload(**{"from": from_stage, "to": to_stage}),
    )
    publish_sync(str(job_id), event)


def update_job_stage(
    *,
    job_id: str | UUID,
    new_status: JobStatus,
    stage_name: str,
    pct: float | None = None,
) -> None:
    """Set ``jobs.status`` / ``current_stage`` and emit ``job.stage_changed`` + ``job.progress``.

    Idempotent: re-running with the same arguments is a no-op as far as the
    DB is concerned, and we still re-publish events (the SSE consumer
    tolerates duplicates).
    """
    from database.models import Job

    async def _body(session: AsyncSession) -> str | None:
        result = await session.execute(select(Job).where(Job.id == _uuid(job_id)))
        job = result.scalar_one_or_none()
        if job is None:
            logger.warning("update_job_stage: job {} not found", job_id)
            return None
        prev_stage = job.current_stage
        job.status = new_status
        job.current_stage = stage_name
        if pct is not None:
            job.progress_pct = float(pct)
        if new_status in (JobStatus.COMPLETED, JobStatus.FAILED):
            job.finished_at = datetime.utcnow()
        return prev_stage

    prev = run_async(_body)
    publish_stage_change(job_id, from_stage=prev, to_stage=stage_name)
    if pct is not None:
        publish_progress(job_id, stage=stage_name, pct=pct)


def insert_asset(
    *,
    job_id: str | UUID,
    kind: AssetKind,
    path: str,
    size_bytes: int | None = None,
    mime: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Insert (or upsert by (job_id, kind, path)) an ``assets`` row; return its id."""
    from database.models import Asset

    async def _body(session: AsyncSession) -> str:
        # Idempotency: if (job, kind, path) already exists, reuse it.
        result = await session.execute(
            select(Asset).where(
                Asset.job_id == _uuid(job_id),
                Asset.kind == kind,
                Asset.path == path,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return str(existing.id)
        row = Asset(
            job_id=_uuid(job_id),
            kind=kind,
            path=path,
            size_bytes=size_bytes,
            mime=mime,
            asset_metadata=metadata,
        )
        session.add(row)
        await session.flush()
        return str(row.id)

    return run_async(_body)


def update_job_source_metadata(*, job_id: str | UUID, metadata: dict[str, Any]) -> None:
    """Merge ``metadata`` into ``jobs.source_metadata`` (do NOT overwrite).

    The caller (yt-dlp) provides extractor metadata. Earlier callers (the API
    POST handler, discovery enqueue) may have stamped routing keys like
    ``facebook_page_id`` / ``content_source_id`` / ``source_type``. Those must
    survive — otherwise downstream stages (reel generation hook in
    render_tasks) lose the page linkage.
    """
    from database.models import Job

    async def _body(session: AsyncSession) -> None:
        result = await session.execute(select(Job).where(Job.id == _uuid(job_id)))
        job = result.scalar_one_or_none()
        if job is None:
            return
        existing = dict(job.source_metadata or {})
        existing.update(metadata or {})
        job.source_metadata = existing

    run_async(_body)


def _uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
