"""Reels generation Celery tasks (queue=reels).

* ``reels.generate_from_source`` -- called when a job with
  ``source_metadata.facebook_page_id`` completes; creates reel_drafts rows.
* ``reels.generate_caption_for_draft`` -- regenerate caption for an existing
  draft (manual retry from UI).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared_py.enums import ApprovalStatus, ClipStage, ContentSourceStatus, PublishStatus
from shared_py.events import (
    ReelGeneratedEvent,
    ReelGeneratedPayload,
    ReelPendingReviewEvent,
    ReelPendingReviewPayload,
)
from task_queue import BaseTask

from apps.workers._app import celery
from apps.workers.db_ctx import run_async
from apps.workers.event_publisher import publish_sync


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _publish_reel(channel_id: str, event: Any) -> None:
    """Publish a reel SSE event. Uses page:{page_id} channel."""
    publish_sync(channel_id, event)


def _next_posting_slot(
    slots: list[dict[str, Any]],
    *,
    tz_name: str = "Asia/Ho_Chi_Minh",
) -> datetime | None:
    """Return the next posting slot >= now() in the given timezone.

    ``slots`` is a list of dicts with at least ``hour`` (int) and optionally
    ``minute`` (int, default 0). Returns a timezone-aware datetime in UTC,
    or None if slots is empty or no valid slot can be parsed.
    """
    if not slots:
        return None

    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        try:
            from datetime import timezone as _tz
            tz = _tz(timedelta(hours=7))  # UTC+7 fallback
        except Exception:
            return None

    now_local = datetime.now(tz)
    today = now_local.date()

    candidates: list[datetime] = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        try:
            hour = int(slot.get("hour", 0))
            minute = int(slot.get("minute", 0))
        except (TypeError, ValueError):
            continue
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            continue
        # Try today and tomorrow.
        for day_offset in (0, 1):
            candidate_date = today + timedelta(days=day_offset)
            try:
                candidate = datetime(
                    candidate_date.year,
                    candidate_date.month,
                    candidate_date.day,
                    hour,
                    minute,
                    0,
                    tzinfo=tz,
                )
            except Exception:
                continue
            if candidate >= now_local:
                candidates.append(candidate)

    if not candidates:
        return None

    # Pick the earliest slot.
    best = min(candidates)
    # Convert to UTC for storage.
    return best.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# reels.generate_from_source
# ---------------------------------------------------------------------------


@celery.task(
    name="reels.generate_from_source",
    base=BaseTask,
    bind=True,
    queue="reels",
)
def generate_from_source(self: BaseTask, job_id: str) -> dict[str, Any]:
    """Create reel_drafts for all rendered clips in a completed job.

    Called automatically when a job with ``source_metadata.facebook_page_id``
    transitions to ``completed``.
    """
    setattr(self, "stage_name", "reels")

    # Load job + page + content_source + rendered clips.
    job_data = _load_job_data(job_id)
    if job_data is None:
        logger.warning("reels.generate_from_source: job {} not found", job_id)
        return {"job_id": job_id, "skipped": True}

    page_id: str = job_data["page_id"]
    content_source_id: str | None = job_data.get("content_source_id")
    clips: list[dict[str, Any]] = job_data["clips"]
    page: dict[str, Any] = job_data["page"]
    content_source: dict[str, Any] | None = job_data.get("content_source")

    if not clips:
        logger.info(
            "reels.generate_from_source: job={} has no rendered clips, skipping",
            job_id,
        )
        return {"job_id": job_id, "clip_count": 0}

    from services.captions.generator import generate_caption

    draft_ids: list[str] = []
    posting_slots: list[dict[str, Any]] = page.get("posting_time_slots") or []

    for clip in clips:
        clip_id = clip["id"]
        try:
            caption_data = generate_caption(clip, page, content_source)
        except Exception as exc:
            logger.error(
                "reels.generate_from_source: caption generation failed for clip={}: {}",
                clip_id,
                exc,
            )
            caption_data = {"title": clip.get("title") or "", "caption": "", "hashtags": []}

        suggested_post_time = _next_posting_slot(posting_slots)

        draft_id = _insert_reel_draft(
            page_id=page_id,
            clip_id=clip_id,
            content_source_id=content_source_id,
            title=caption_data.get("title") or "",
            caption=caption_data.get("caption") or "",
            hashtags=list(caption_data.get("hashtags") or []),
            suggested_post_time=suggested_post_time,
        )
        draft_ids.append(draft_id)

        # Publish SSE events on the page channel.
        page_uuid = UUID(page_id)
        draft_uuid = UUID(draft_id)

        _publish_reel(
            page_id,
            ReelGeneratedEvent(
                job_id=UUID(job_id),
                payload=ReelGeneratedPayload(
                    reel_draft_id=draft_uuid,
                    page_id=page_uuid,
                    title=caption_data.get("title") or None,
                ),
            ),
        )
        _publish_reel(
            page_id,
            ReelPendingReviewEvent(
                job_id=UUID(job_id),
                payload=ReelPendingReviewPayload(
                    reel_draft_id=draft_uuid,
                    page_id=page_uuid,
                ),
            ),
        )

        logger.info(
            "reels.generate_from_source: created draft={} for clip={} page={}",
            draft_id,
            clip_id,
            page_id,
        )

    # Mark content_source as generated.
    if content_source_id:
        _mark_content_source_generated(content_source_id)

    return {"job_id": job_id, "draft_ids": draft_ids, "clip_count": len(draft_ids)}


# ---------------------------------------------------------------------------
# reels.generate_caption_for_draft
# ---------------------------------------------------------------------------


@celery.task(
    name="reels.generate_caption_for_draft",
    base=BaseTask,
    bind=True,
    queue="reels",
)
def generate_caption_for_draft(
    self: BaseTask, reel_draft_id: str
) -> dict[str, Any]:
    """Regenerate caption for an existing reel draft (manual retry from UI)."""
    setattr(self, "stage_name", "reels")

    draft_data = _load_draft_data(reel_draft_id)
    if draft_data is None:
        logger.warning(
            "reels.generate_caption_for_draft: draft {} not found", reel_draft_id
        )
        return {"reel_draft_id": reel_draft_id, "skipped": True}

    clip: dict[str, Any] | None = draft_data.get("clip")
    page: dict[str, Any] = draft_data["page"]
    content_source: dict[str, Any] | None = draft_data.get("content_source")

    if clip is None:
        logger.warning(
            "reels.generate_caption_for_draft: draft {} has no clip", reel_draft_id
        )
        return {"reel_draft_id": reel_draft_id, "skipped": True}

    from services.captions.generator import generate_caption

    caption_data = generate_caption(clip, page, content_source)

    _update_draft_caption(
        reel_draft_id=reel_draft_id,
        title=caption_data.get("title") or "",
        caption=caption_data.get("caption") or "",
        hashtags=list(caption_data.get("hashtags") or []),
    )

    logger.info(
        "reels.generate_caption_for_draft: updated draft={}", reel_draft_id
    )
    return {
        "reel_draft_id": reel_draft_id,
        "title": caption_data.get("title"),
        "caption": caption_data.get("caption"),
        "hashtags": caption_data.get("hashtags"),
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _load_job_data(job_id: str) -> dict[str, Any] | None:
    """Load job, its rendered clips, the facebook page, and content_source."""
    from database.models import Clip, ContentSource, FacebookPage, Job

    async def _body(session: AsyncSession) -> dict[str, Any] | None:
        job_q = await session.execute(select(Job).where(Job.id == UUID(job_id)))
        job = job_q.scalar_one_or_none()
        if job is None:
            return None

        metadata: dict[str, Any] = job.source_metadata or {}
        page_id_str: str | None = metadata.get("facebook_page_id")
        if not page_id_str:
            return None

        try:
            page_uuid = UUID(str(page_id_str))
        except (ValueError, AttributeError):
            logger.warning("_load_job_data: invalid facebook_page_id={}", page_id_str)
            return None

        page_q = await session.execute(
            select(FacebookPage).where(FacebookPage.id == page_uuid)
        )
        page_row = page_q.scalar_one_or_none()
        if page_row is None:
            logger.warning("_load_job_data: page {} not found", page_id_str)
            return None

        page_dict: dict[str, Any] = {
            "id": str(page_row.id),
            "niche": page_row.niche,
            "language": page_row.language or "vi",
            "posting_time_slots": list(page_row.posting_time_slots or []),
            "require_manual_approval": page_row.require_manual_approval,
        }

        # Load rendered clips.
        clips_q = await session.execute(
            select(Clip).where(
                Clip.job_id == UUID(job_id),
                Clip.status == ClipStage.RENDERED,
            )
        )
        clip_rows = clips_q.scalars().all()
        clips: list[dict[str, Any]] = [
            {
                "id": str(c.id),
                "clip_index": c.clip_index,
                "title": c.title,
                "main_hook": c.main_hook,
                "topics": list(c.topics or []),
                "duration": float(c.duration),
                "start_time": float(c.start_time),
                "end_time": float(c.end_time),
            }
            for c in clip_rows
        ]

        # Load content_source if present.
        content_source_id_str: str | None = metadata.get("content_source_id")
        content_source_dict: dict[str, Any] | None = None
        if content_source_id_str:
            try:
                cs_uuid = UUID(str(content_source_id_str))
                cs_q = await session.execute(
                    select(ContentSource).where(ContentSource.id == cs_uuid)
                )
                cs_row = cs_q.scalar_one_or_none()
                if cs_row is not None:
                    content_source_dict = {
                        "id": str(cs_row.id),
                        "source_title": cs_row.source_title,
                        "channel_name": cs_row.channel_name,
                        "detected_topic": cs_row.detected_topic,
                    }
            except (ValueError, AttributeError):
                pass

        return {
            "page_id": str(page_uuid),
            "content_source_id": content_source_id_str,
            "page": page_dict,
            "content_source": content_source_dict,
            "clips": clips,
        }

    return run_async(_body)


def _insert_reel_draft(
    *,
    page_id: str,
    clip_id: str,
    content_source_id: str | None,
    title: str,
    caption: str,
    hashtags: list[str],
    suggested_post_time: datetime | None,
) -> str:
    """Insert a new reel_draft row and return its id."""
    from database.models import ReelDraft

    async def _body(session: AsyncSession) -> str:
        row = ReelDraft(
            id=uuid4(),
            page_id=UUID(page_id),
            clip_id=UUID(clip_id),
            content_source_id=UUID(content_source_id) if content_source_id else None,
            title=title or None,
            caption=caption or None,
            hashtags=hashtags,
            suggested_post_time=suggested_post_time,
            approval_status=ApprovalStatus.PENDING,
            publish_status=PublishStatus.DRAFT,
        )
        session.add(row)
        await session.flush()
        return str(row.id)

    return run_async(_body)


def _mark_content_source_generated(content_source_id: str) -> None:
    """Set content_source.status = 'generated'."""
    from database.models import ContentSource

    async def _body(session: AsyncSession) -> None:
        q = await session.execute(
            select(ContentSource).where(ContentSource.id == UUID(content_source_id))
        )
        row = q.scalar_one_or_none()
        if row is not None:
            row.status = ContentSourceStatus.GENERATED

    try:
        run_async(_body)
    except Exception as exc:
        logger.warning(
            "_mark_content_source_generated failed for {}: {}", content_source_id, exc
        )


def _load_draft_data(reel_draft_id: str) -> dict[str, Any] | None:
    """Load draft + its clip + page + content_source."""
    from database.models import Clip, ContentSource, FacebookPage, ReelDraft

    async def _body(session: AsyncSession) -> dict[str, Any] | None:
        draft_q = await session.execute(
            select(ReelDraft).where(ReelDraft.id == UUID(reel_draft_id))
        )
        draft = draft_q.scalar_one_or_none()
        if draft is None:
            return None

        page_q = await session.execute(
            select(FacebookPage).where(FacebookPage.id == draft.page_id)
        )
        page_row = page_q.scalar_one_or_none()
        if page_row is None:
            return None

        page_dict: dict[str, Any] = {
            "id": str(page_row.id),
            "niche": page_row.niche,
            "language": page_row.language or "vi",
            "posting_time_slots": list(page_row.posting_time_slots or []),
        }

        clip_dict: dict[str, Any] | None = None
        if draft.clip_id:
            clip_q = await session.execute(
                select(Clip).where(Clip.id == draft.clip_id)
            )
            clip_row = clip_q.scalar_one_or_none()
            if clip_row is not None:
                clip_dict = {
                    "id": str(clip_row.id),
                    "clip_index": clip_row.clip_index,
                    "title": clip_row.title,
                    "main_hook": clip_row.main_hook,
                    "topics": list(clip_row.topics or []),
                    "duration": float(clip_row.duration),
                }

        content_source_dict: dict[str, Any] | None = None
        if draft.content_source_id:
            cs_q = await session.execute(
                select(ContentSource).where(ContentSource.id == draft.content_source_id)
            )
            cs_row = cs_q.scalar_one_or_none()
            if cs_row is not None:
                content_source_dict = {
                    "id": str(cs_row.id),
                    "source_title": cs_row.source_title,
                    "channel_name": cs_row.channel_name,
                }

        return {
            "draft_id": reel_draft_id,
            "page": page_dict,
            "clip": clip_dict,
            "content_source": content_source_dict,
        }

    return run_async(_body)


def _update_draft_caption(
    *,
    reel_draft_id: str,
    title: str,
    caption: str,
    hashtags: list[str],
) -> None:
    from database.models import ReelDraft

    async def _body(session: AsyncSession) -> None:
        q = await session.execute(
            select(ReelDraft).where(ReelDraft.id == UUID(reel_draft_id))
        )
        draft = q.scalar_one_or_none()
        if draft is None:
            return
        draft.title = title or draft.title
        draft.caption = caption or draft.caption
        draft.hashtags = hashtags

    run_async(_body)
