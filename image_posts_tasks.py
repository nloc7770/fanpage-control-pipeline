"""Image posts Celery tasks (queue=image_posts).

* ``image_posts.generate_for_pages`` -- iterate active pages with
  ``auto_generate_enabled=True`` and queue N generation tasks per page.
* ``image_posts.generate_one`` -- generate caption + image for one page,
  save to disk, insert DB row, publish SSE events.
* ``image_posts.publish_one`` -- publish a single approved image post to Facebook.
* ``image_posts.publish_scheduled_image_posts`` -- poll for due scheduled posts (cron).

NOTE: This file is shared between two agents:
  - Agent B (image gen): owns generate_for_pages, generate_one
  - Agent C (FB publisher): owns publish_one, publish_scheduled_image_posts
"""

from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared_py.enums import ApprovalStatus, FacebookPageStatus, PublishStatus
from shared_py.events import (
    ImagePostGeneratedEvent,
    ImagePostGeneratedPayload,
    ImagePostGeneratingEvent,
    ImagePostGeneratingPayload,
    ImagePostPendingReviewEvent,
    ImagePostPendingReviewPayload,
)
from task_queue import BaseTask

from apps.workers._app import celery
from apps.workers.db_ctx import run_async
from apps.workers.event_publisher import publish_sync


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEFAULT_POSTS_PER_PAGE = 2
_IMAGE_WIDTH = 1200
_IMAGE_HEIGHT = 628
_ASPECT_RATIO = "1.91:1"


def _posts_per_page() -> int:
    try:
        return int(os.environ.get("IMAGE_POSTS_PER_PAGE_PER_DAY", str(_DEFAULT_POSTS_PER_PAGE)))
    except (ValueError, TypeError):
        return _DEFAULT_POSTS_PER_PAGE


def _storage_root() -> Path:
    base = os.environ.get("STORAGE_LOCAL_PATH", "_storage_data")
    return Path(base)


# ---------------------------------------------------------------------------
# image_posts.generate_for_pages
# ---------------------------------------------------------------------------


@celery.task(
    name="image_posts.generate_for_pages",
    base=BaseTask,
    bind=True,
    queue="image_posts",
)
def generate_for_pages(self: BaseTask) -> dict[str, Any]:
    """Iterate active pages with auto_generate_enabled=True and queue image post tasks."""
    setattr(self, "stage_name", "image_posts")

    pages = _load_active_pages()
    if not pages:
        logger.info("image_posts.generate_for_pages: no active pages found")
        return {"queued": 0}

    n_per_page = _posts_per_page()
    queued = 0

    for page in pages:
        page_id = page["id"]
        # Honor per-page daily_image_post_target column; fall back to env or 3.
        daily_target = page.get("daily_image_post_target") or 0
        if daily_target <= 0:
            daily_target = n_per_page
        n = max(1, int(daily_target))

        for _ in range(n):
            celery.send_task(
                "image_posts.generate_one",
                kwargs={"page_id": page_id},
                queue="image_posts",
            )
            queued += 1

        logger.info(
            "image_posts.generate_for_pages: queued {} tasks for page={}",
            n,
            page_id,
        )

    return {"queued": queued, "page_count": len(pages)}


# ---------------------------------------------------------------------------
# image_posts.generate_one
# ---------------------------------------------------------------------------


@celery.task(
    name="image_posts.generate_one",
    base=BaseTask,
    bind=True,
    queue="image_posts",
)
def generate_one(
    self: BaseTask,
    page_id: str,
    source_topic: str | None = None,
) -> dict[str, Any]:
    """Generate one image post for a Facebook page.

    Steps:
    1. Load page data.
    2. Derive topic from niche + content_keywords if not provided.
    3. Generate caption via LLM.
    4. Generate image via pollinations.ai.
    5. Save image to disk.
    6. Insert image_posts row.
    7. Publish SSE events: generating → generated → pending_review.
    """
    setattr(self, "stage_name", "image_posts")

    page = _load_page(page_id)
    if page is None:
        logger.warning("image_posts.generate_one: page {} not found", page_id)
        return {"page_id": page_id, "skipped": True}

    niche: str = page.get("niche") or "lifestyle"
    language: str = page.get("language") or "vi"
    keywords: list[str] = page.get("content_keywords") or []

    # Derive topic if not supplied.
    if not source_topic:
        if keywords:
            source_topic = random.choice(keywords)
        else:
            source_topic = niche

    post_id = uuid4()
    post_id_str = str(post_id)
    page_uuid = UUID(page_id)

    # --- SSE: generating ---
    publish_sync(
        page_id,
        ImagePostGeneratingEvent(
            job_id=post_id,
            payload=ImagePostGeneratingPayload(
                image_post_id=post_id,
                page_id=page_uuid,
                source_topic=source_topic,
            ),
        ),
    )

    # --- Caption generation ---
    from services.image_gen.caption_generator import generate_caption

    caption_data = asyncio.run(
        generate_caption(topic=source_topic, niche=niche, language=language)
    )
    caption: str = str(caption_data.get("caption") or "")
    hashtags: list[str] = list(caption_data.get("hashtags") or [])

    # --- Image generation ---
    from services.image_gen.generator import generate_image, humanize_prompt

    image_prompt = humanize_prompt(source_topic, niche, language)
    image_bytes = asyncio.run(
        generate_image(image_prompt, width=_IMAGE_WIDTH, height=_IMAGE_HEIGHT)
    )

    # --- Save image to disk ---
    image_dir = _storage_root() / "image_posts" / page_id
    image_dir.mkdir(parents=True, exist_ok=True)
    image_path = image_dir / f"{post_id_str}.jpg"
    image_path.write_bytes(image_bytes)
    relative_path = str(image_path.relative_to(_storage_root()))

    logger.info(
        "image_posts.generate_one: saved image to {} ({} bytes)",
        image_path,
        len(image_bytes),
    )

    # --- Insert DB row ---
    generation_metadata: dict[str, Any] = {
        "image_prompt": image_prompt,
        "image_width": _IMAGE_WIDTH,
        "image_height": _IMAGE_HEIGHT,
        "niche": niche,
        "language": language,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    _insert_image_post(
        post_id=post_id,
        page_id=page_id,
        source_topic=source_topic,
        caption=caption,
        hashtags=hashtags,
        image_path=relative_path,
        generation_metadata=generation_metadata,
    )

    # --- SSE: generated ---
    publish_sync(
        page_id,
        ImagePostGeneratedEvent(
            job_id=post_id,
            payload=ImagePostGeneratedPayload(
                image_post_id=post_id,
                page_id=page_uuid,
                image_count=1,
            ),
        ),
    )

    # --- SSE: pending_review ---
    publish_sync(
        page_id,
        ImagePostPendingReviewEvent(
            job_id=post_id,
            payload=ImagePostPendingReviewPayload(
                image_post_id=post_id,
                page_id=page_uuid,
            ),
        ),
    )

    logger.info(
        "image_posts.generate_one: created post={} page={} topic={!r}",
        post_id_str,
        page_id,
        source_topic,
    )

    return {
        "image_post_id": post_id_str,
        "page_id": page_id,
        "source_topic": source_topic,
        "image_path": relative_path,
    }


# ---------------------------------------------------------------------------
# DB helpers (generation side)
# ---------------------------------------------------------------------------


def _load_active_pages() -> list[dict[str, Any]]:
    """Return all active pages with auto_generate_enabled=True."""
    from database.models import FacebookPage

    async def _body(session: AsyncSession) -> list[dict[str, Any]]:
        q = await session.execute(
            select(FacebookPage).where(
                FacebookPage.auto_generate_enabled.is_(True),
                FacebookPage.status == FacebookPageStatus.ACTIVE,
            )
        )
        rows = q.scalars().all()
        return [
            {
                "id": str(r.id),
                "niche": r.niche,
                "language": r.language or "vi",
                "content_keywords": list(r.content_keywords or []),
                "daily_reel_target": r.daily_reel_target,
                "daily_image_post_target": getattr(r, "daily_image_post_target", None),
            }
            for r in rows
        ]

    return run_async(_body)


def _load_page(page_id: str) -> dict[str, Any] | None:
    """Load a single page by id."""
    from database.models import FacebookPage

    async def _body(session: AsyncSession) -> dict[str, Any] | None:
        q = await session.execute(
            select(FacebookPage).where(FacebookPage.id == UUID(page_id))
        )
        row = q.scalar_one_or_none()
        if row is None:
            return None
        return {
            "id": str(row.id),
            "niche": row.niche,
            "language": row.language or "vi",
            "content_keywords": list(row.content_keywords or []),
            "daily_reel_target": row.daily_reel_target,
        }

    return run_async(_body)


def _insert_image_post(
    *,
    post_id: UUID,
    page_id: str,
    source_topic: str,
    caption: str,
    hashtags: list[str],
    image_path: str,
    generation_metadata: dict[str, Any],
) -> None:
    """Insert a new image_posts row."""
    from database.models import ImagePost

    async def _body(session: AsyncSession) -> None:
        row = ImagePost(
            id=post_id,
            page_id=UUID(page_id),
            source_topic=source_topic,
            caption=caption or None,
            hashtags=hashtags,
            image_paths=[image_path],
            image_count=1,
            aspect_ratio=_ASPECT_RATIO,
            approval_status=ApprovalStatus.PENDING,
            publish_status=PublishStatus.DRAFT,
            generation_metadata=generation_metadata,
        )
        session.add(row)
        await session.flush()

    run_async(_body)


# ---------------------------------------------------------------------------
# image_posts.publish_one
# ---------------------------------------------------------------------------


@celery.task(
    name="image_posts.publish_one",
    base=BaseTask,
    bind=True,
    queue="image_posts",
)
def publish_one(self: BaseTask, image_post_id: str) -> dict[str, Any]:
    """Publish a single approved image post to Facebook."""
    logger.info("image_posts.publish_one: image_post_id={}", image_post_id)

    try:
        facebook_post_id = asyncio.run(_run_publish(UUID(image_post_id)))
    except Exception as exc:
        logger.error(
            "image_posts.publish_one: FAILED id={} err={}", image_post_id, exc
        )
        raise

    return {"facebook_post_id": facebook_post_id, "image_post_id": image_post_id}


# ---------------------------------------------------------------------------
# image_posts.publish_scheduled_image_posts
# ---------------------------------------------------------------------------


@celery.task(
    name="image_posts.publish_scheduled_image_posts",
    base=BaseTask,
    bind=True,
    queue="image_posts",
)
def publish_scheduled_image_posts(self: BaseTask) -> dict[str, Any]:
    """Poll for due scheduled image posts and enqueue publish tasks.

    Intended to be called every minute via Celery Beat (cron).
    TODO: register in celery beat schedule in worker_app.py / docker-compose.
    """
    from database.models import ImagePost

    daily_limit = int(os.environ.get("FACEBOOK_DAILY_LIMIT_PER_PAGE", "10"))
    min_delay_s = int(os.environ.get("FACEBOOK_MIN_DELAY_BETWEEN_POSTS_S", "1800"))

    now = datetime.now(tz=timezone.utc)

    async def _body(session: AsyncSession) -> list[dict[str, Any]]:
        stmt = (
            select(ImagePost)
            .where(
                and_(
                    ImagePost.approval_status == ApprovalStatus.APPROVED,
                    ImagePost.publish_status == PublishStatus.SCHEDULED,
                    ImagePost.scheduled_at <= now,
                )
            )
            .order_by(ImagePost.scheduled_at)
        )
        result = await session.execute(stmt)
        posts = result.scalars().all()

        enqueued: list[dict[str, Any]] = []
        for post in posts:
            rate_err = await _check_rate_limits(
                session, post, daily_limit, min_delay_s, now
            )
            if rate_err:
                post.publish_status = PublishStatus.FAILED
                post.error_message = rate_err
                logger.warning(
                    "publish_scheduled_image_posts: rate_limit id={} reason={}",
                    post.id,
                    rate_err,
                )
                continue

            post.publish_status = PublishStatus.PUBLISHING
            enqueued.append(
                {"image_post_id": str(post.id), "page_id": str(post.page_id)}
            )

        return enqueued

    enqueued = run_async(_body)

    for item in enqueued:
        celery.send_task(
            "image_posts.publish_one",
            kwargs={"image_post_id": item["image_post_id"]},
            queue="image_posts",
        )
        logger.info(
            "publish_scheduled_image_posts: enqueued id={}", item["image_post_id"]
        )

    return {"enqueued": len(enqueued)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _check_rate_limits(
    session: AsyncSession,
    post: Any,
    daily_limit: int,
    min_delay_s: int,
    now: datetime,
) -> str | None:
    """Return an error string if rate limits are exceeded, else None."""
    from database.models import ImagePost

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Daily limit check
    count_result = await session.execute(
        select(func.count(ImagePost.id)).where(
            and_(
                ImagePost.page_id == post.page_id,
                ImagePost.publish_status == PublishStatus.PUBLISHED,
                ImagePost.published_at >= today_start,
            )
        )
    )
    daily_count = count_result.scalar_one() or 0
    if daily_count >= daily_limit:
        return f"rate_limit:daily_limit_reached ({daily_count}/{daily_limit})"

    # Min delay check: find last published image post for this page
    last_result = await session.execute(
        select(ImagePost.published_at)
        .where(
            and_(
                ImagePost.page_id == post.page_id,
                ImagePost.publish_status == PublishStatus.PUBLISHED,
                ImagePost.published_at.isnot(None),
            )
        )
        .order_by(ImagePost.published_at.desc())
        .limit(1)
    )
    last_published_at = last_result.scalar_one_or_none()
    if last_published_at is not None:
        if last_published_at.tzinfo is None:
            last_published_at = last_published_at.replace(tzinfo=timezone.utc)
        elapsed = (now - last_published_at).total_seconds()
        if elapsed < min_delay_s:
            remaining = int(min_delay_s - elapsed)
            return f"rate_limit:min_delay_not_elapsed (wait {remaining}s)"

    return None


async def _run_publish(image_post_id: UUID) -> str:
    from services.facebook.image_publisher import publish_image_post

    return await publish_image_post(image_post_id)
