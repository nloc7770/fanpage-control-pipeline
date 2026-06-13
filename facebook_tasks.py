"""Facebook Celery tasks.

Queue: ``facebook``

Tasks:
  - ``facebook.publish_reel_draft``  — publish a single reel draft
  - ``facebook.publish_scheduled_reels`` — poll for due scheduled reels (cron)
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared_py.enums import ApprovalStatus, PublishStatus
from shared_py.events import (
    ReelFailedEvent,
    ReelFailedPayload,
    ReelPublishedEvent,
    ReelPublishedPayload,
    ReelPublishingEvent,
    ReelPublishingPayload,
)
from task_queue import BaseTask

from apps.workers._app import celery
from apps.workers.db_ctx import run_async
from apps.workers.event_publisher import publish_sync


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://factory:factory@postgres:5432/factory"
    )


# ---------------------------------------------------------------------------
# facebook.publish_reel_draft
# ---------------------------------------------------------------------------


@celery.task(
    name="facebook.publish_reel_draft",
    base=BaseTask,
    bind=True,
    queue="facebook",
)
def publish_reel_draft(self: BaseTask, reel_draft_id: str) -> dict[str, Any]:
    """Publish a single approved reel draft to Facebook."""
    from database.models import PublishJob, ReelDraft

    logger.info("facebook.publish_reel_draft: reel_draft_id={}", reel_draft_id)

    # Load draft + page_id for SSE events
    draft_info = _load_draft_info(reel_draft_id)
    if draft_info is None:
        logger.error("publish_reel_draft: draft {} not found", reel_draft_id)
        return {"error": "draft_not_found"}

    page_id_db = draft_info["page_id"]
    page_id_str = str(page_id_db)

    # Create a PublishJob row
    publish_job_id = _create_publish_job(reel_draft_id, page_id_db)

    # Emit reel.publishing SSE
    _emit_publishing(reel_draft_id, page_id_str, str(publish_job_id))

    # Run the async publisher synchronously
    try:
        facebook_video_id = asyncio.run(_run_publish(page_id_db, UUID(reel_draft_id)))
    except Exception as exc:
        logger.error(
            "publish_reel_draft: FAILED draft={} err={}", reel_draft_id, exc
        )
        _emit_failed(reel_draft_id, page_id_str, str(exc))
        _mark_publish_job_failed(str(publish_job_id), str(exc))
        raise

    # Emit reel.published SSE
    _emit_published(reel_draft_id, page_id_str, facebook_video_id)
    _mark_publish_job_done(str(publish_job_id), facebook_video_id)

    return {"facebook_video_id": facebook_video_id, "reel_draft_id": reel_draft_id}


# ---------------------------------------------------------------------------
# facebook.publish_scheduled_reels
# ---------------------------------------------------------------------------


@celery.task(
    name="facebook.publish_scheduled_reels",
    base=BaseTask,
    bind=True,
    queue="facebook",
)
def publish_scheduled_reels(self: BaseTask) -> dict[str, Any]:
    """Poll for due scheduled reels and enqueue publish tasks.

    Intended to be called every minute via Celery Beat (cron).
    TODO: register in celery beat schedule in worker_app.py / docker-compose.
    """
    from database.models import ReelDraft

    daily_limit = int(os.environ.get("FACEBOOK_DAILY_LIMIT_PER_PAGE", "10"))
    min_delay_s = int(os.environ.get("FACEBOOK_MIN_DELAY_BETWEEN_POSTS_S", "1800"))

    now = datetime.now(tz=timezone.utc)

    async def _body(session: AsyncSession) -> list[dict[str, Any]]:
        from sqlalchemy import and_

        stmt = (
            select(ReelDraft)
            .where(
                and_(
                    ReelDraft.approval_status == ApprovalStatus.APPROVED,
                    ReelDraft.publish_status == PublishStatus.SCHEDULED,
                    ReelDraft.scheduled_at <= now,
                )
            )
            .order_by(ReelDraft.scheduled_at)
        )
        result = await session.execute(stmt)
        drafts = result.scalars().all()

        enqueued: list[dict[str, Any]] = []
        for draft in drafts:
            # Rate-limit checks
            rate_err = await _check_rate_limits(
                session, draft, daily_limit, min_delay_s, now
            )
            if rate_err:
                draft.publish_status = PublishStatus.FAILED
                draft.error_message = rate_err
                logger.warning(
                    "publish_scheduled_reels: rate_limit draft={} reason={}",
                    draft.id,
                    rate_err,
                )
                continue

            draft.publish_status = PublishStatus.PUBLISHING
            enqueued.append(
                {"reel_draft_id": str(draft.id), "page_id": str(draft.page_id)}
            )

        return enqueued

    enqueued = run_async(_body)

    # Enqueue individual publish tasks outside the DB session
    for item in enqueued:
        celery.send_task(
            "facebook.publish_reel_draft",
            kwargs={"reel_draft_id": item["reel_draft_id"]},
            queue="facebook",
        )
        logger.info(
            "publish_scheduled_reels: enqueued draft={}", item["reel_draft_id"]
        )

    return {"enqueued": len(enqueued)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _check_rate_limits(
    session: AsyncSession,
    draft: Any,
    daily_limit: int,
    min_delay_s: int,
    now: datetime,
) -> str | None:
    """Return an error string if rate limits are exceeded, else None."""
    from sqlalchemy import and_, func

    from database.models import ReelDraft

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Daily limit check
    count_result = await session.execute(
        select(func.count(ReelDraft.id)).where(
            and_(
                ReelDraft.page_id == draft.page_id,
                ReelDraft.publish_status == PublishStatus.PUBLISHED,
                ReelDraft.published_at >= today_start,
            )
        )
    )
    daily_count = count_result.scalar_one() or 0
    if daily_count >= daily_limit:
        return f"rate_limit:daily_limit_reached ({daily_count}/{daily_limit})"

    # Min delay check: find last published reel for this page
    last_result = await session.execute(
        select(ReelDraft.published_at)
        .where(
            and_(
                ReelDraft.page_id == draft.page_id,
                ReelDraft.publish_status == PublishStatus.PUBLISHED,
                ReelDraft.published_at.isnot(None),
            )
        )
        .order_by(ReelDraft.published_at.desc())
        .limit(1)
    )
    last_published_at = last_result.scalar_one_or_none()
    if last_published_at is not None:
        # Ensure timezone-aware comparison
        if last_published_at.tzinfo is None:
            last_published_at = last_published_at.replace(tzinfo=timezone.utc)
        elapsed = (now - last_published_at).total_seconds()
        if elapsed < min_delay_s:
            remaining = int(min_delay_s - elapsed)
            return f"rate_limit:min_delay_not_elapsed (wait {remaining}s)"

    return None


def _load_draft_info(reel_draft_id: str) -> dict[str, Any] | None:
    from database.models import ReelDraft

    async def _body(session: AsyncSession) -> dict[str, Any] | None:
        result = await session.execute(
            select(ReelDraft).where(ReelDraft.id == UUID(reel_draft_id))
        )
        draft = result.scalar_one_or_none()
        if draft is None:
            return None
        return {"page_id": draft.page_id, "draft_id": draft.id}

    return run_async(_body)


def _create_publish_job(reel_draft_id: str, page_id: UUID) -> UUID:
    from database.models import PublishJob
    from shared_py.enums import PublishJobStatus

    async def _body(session: AsyncSession) -> UUID:
        job = PublishJob(
            reel_draft_id=UUID(reel_draft_id),
            page_id=page_id,
            status=PublishJobStatus.UPLOADING,
        )
        session.add(job)
        await session.flush()
        return job.id

    return run_async(_body)


def _mark_publish_job_done(publish_job_id: str, facebook_video_id: str) -> None:
    from database.models import PublishJob
    from shared_py.enums import PublishJobStatus

    async def _body(session: AsyncSession) -> None:
        result = await session.execute(
            select(PublishJob).where(PublishJob.id == UUID(publish_job_id))
        )
        job = result.scalar_one_or_none()
        if job:
            job.status = PublishJobStatus.PUBLISHED
            job.published_at = datetime.now(tz=timezone.utc)

    run_async(_body)


def _mark_publish_job_failed(publish_job_id: str, error: str) -> None:
    from database.models import PublishJob
    from shared_py.enums import PublishJobStatus

    async def _body(session: AsyncSession) -> None:
        result = await session.execute(
            select(PublishJob).where(PublishJob.id == UUID(publish_job_id))
        )
        job = result.scalar_one_or_none()
        if job:
            job.status = PublishJobStatus.FAILED
            job.error_message = error[:2000]

    run_async(_body)


async def _run_publish(page_id_db: UUID, reel_draft_id: UUID) -> str:
    from services.facebook.publisher import publish_reel

    return await publish_reel(page_id_db, reel_draft_id)


def _emit_publishing(
    reel_draft_id: str, page_id: str, publish_job_id: str
) -> None:
    try:
        event = ReelPublishingEvent(
            job_id=UUID(reel_draft_id),
            payload=ReelPublishingPayload(
                reel_draft_id=UUID(reel_draft_id),
                page_id=UUID(page_id),
                publish_job_id=UUID(publish_job_id),
            ),
        )
        publish_sync(reel_draft_id, event)
    except Exception as exc:
        logger.warning("_emit_publishing: SSE publish failed: {}", exc)


def _emit_published(
    reel_draft_id: str, page_id: str, facebook_video_id: str
) -> None:
    try:
        event = ReelPublishedEvent(
            job_id=UUID(reel_draft_id),
            payload=ReelPublishedPayload(
                reel_draft_id=UUID(reel_draft_id),
                page_id=UUID(page_id),
                facebook_video_id=facebook_video_id,
                facebook_post_id=facebook_video_id,
            ),
        )
        publish_sync(reel_draft_id, event)
    except Exception as exc:
        logger.warning("_emit_published: SSE publish failed: {}", exc)


def _emit_failed(reel_draft_id: str, page_id: str, error: str) -> None:
    try:
        event = ReelFailedEvent(
            job_id=UUID(reel_draft_id),
            payload=ReelFailedPayload(
                reel_draft_id=UUID(reel_draft_id),
                page_id=UUID(page_id),
                error=error[:500],
            ),
        )
        publish_sync(reel_draft_id, event)
    except Exception as exc:
        logger.warning("_emit_failed: SSE publish failed: {}", exc)
