"""Celery tasks for YouTube content discovery.

Queue: ``discovery``

Tasks:
- ``discovery.find_content_for_pages`` — scan all active auto-generate pages,
  run YouTube discovery, persist candidates.
- ``discovery.queue_sources_for_generation`` — promote ``discovered`` sources
  to ``queued``, create jobs, enqueue ``download.fetch_source``.
"""

from __future__ import annotations

import os
from datetime import date, timezone, datetime
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.workers._app import celery
from apps.workers.db_ctx import run_async
from shared_py.enums import ContentSourceStatus, FacebookPageStatus


# ---------------------------------------------------------------------------
# Task: find_content_for_pages
# ---------------------------------------------------------------------------


@celery.task(name="discovery.find_content_for_pages", queue="discovery")
def find_content_for_pages() -> dict[str, Any]:
    """Iterate all active auto-generate pages and run YouTube discovery for each."""
    from database.models import FacebookPage

    async def _get_pages(session: AsyncSession) -> list[FacebookPage]:
        stmt = (
            select(FacebookPage)
            .where(FacebookPage.status == FacebookPageStatus.ACTIVE)
            .where(FacebookPage.auto_generate_enabled.is_(True))
        )
        return list((await session.execute(stmt)).scalars().all())

    pages = run_async(_get_pages)
    logger.info("discovery.find_content_for_pages: found {} active pages", len(pages))

    total_inserted = 0
    errors = 0

    for page in pages:
        try:
            inserted = _run_discovery_for_page(page)
            total_inserted += inserted
        except Exception as exc:
            errors += 1
            logger.error(
                "discovery.find_content_for_pages: page={} error={}", page.id, exc
            )

    return {"pages_processed": len(pages), "total_inserted": total_inserted, "errors": errors}


def _run_discovery_for_page(page: Any) -> int:
    """Synchronous wrapper: run async discovery for one page.

    Splits the async work into two phases so nested ``asyncio.run`` doesn't
    happen: ``find_for_page`` is pure HTTP (one event loop), then
    ``queue_for_generation`` opens its own DB session via ``run_async``.

    Between phases, the ranking algorithm scores candidates by view count,
    recency, duration sweet spot, and topic match — then keeps only the top N.
    """
    import asyncio

    from services.discovery.ranking import rank_candidates
    from services.discovery.youtube import YouTubeDiscoveryService

    settings_max = int(os.environ.get("YOUTUBE_MAX_RESULTS_PER_PAGE", "10"))
    svc = YouTubeDiscoveryService()

    # Phase 1: pure-async HTTP search (no DB).
    candidates = asyncio.run(svc.find_for_page(page, max_results=settings_max))

    # Phase 2: rank and filter — score by views, recency, duration, topic match.
    # Uses page niche as the topic for title-match bonus scoring.
    topic = getattr(page, "niche", None) or ""
    candidates = rank_candidates(candidates, topic=topic)

    logger.info(
        "discovery._run_discovery_for_page: page={} raw={} after_ranking={}",
        page.id,
        settings_max,
        len(candidates),
    )

    # Phase 3: persistence — opens its own loop via run_async inside.
    return svc.queue_for_generation_sync(page, candidates)


# ---------------------------------------------------------------------------
# Task: queue_sources_for_generation
# ---------------------------------------------------------------------------


@celery.task(name="discovery.queue_sources_for_generation", queue="discovery")
def queue_sources_for_generation(page_id: str | None = None) -> dict[str, Any]:
    """Promote ``discovered`` content_sources to ``queued`` and create download jobs.

    For each eligible source:
    1. Set ``content_sources.status = 'queued'``.
    2. Create a ``jobs`` row via ``job_service.create_job``.
    3. Enqueue ``download.fetch_source``.
    4. Set ``content_sources.status = 'processing'``.
    5. Publish SSE ``content.queued``.

    The number of sources promoted per page is capped at
    ``page.daily_reel_target`` minus the count already generated today.
    """
    from database.models import ContentSource, FacebookPage, Job
    from shared_py.enums import JobStatus

    async def _get_sources(session: AsyncSession) -> list[tuple[ContentSource, FacebookPage]]:
        """Return (source, page) pairs eligible for queuing."""
        stmt = (
            select(ContentSource, FacebookPage)
            .join(FacebookPage, FacebookPage.id == ContentSource.page_id)
            .where(ContentSource.status == ContentSourceStatus.DISCOVERED)
            .where(FacebookPage.status == FacebookPageStatus.ACTIVE)
            .where(FacebookPage.auto_generate_enabled.is_(True))
        )
        if page_id:
            stmt = stmt.where(ContentSource.page_id == UUID(str(page_id)))
        stmt = stmt.order_by(ContentSource.created_at.asc())
        rows = (await session.execute(stmt)).all()
        return [(r[0], r[1]) for r in rows]

    async def _count_today_generated(session: AsyncSession, pg_id: UUID) -> int:
        """Count content_sources for this page already generated today."""
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        stmt = (
            select(func.count())
            .select_from(ContentSource)
            .where(ContentSource.page_id == pg_id)
            .where(
                ContentSource.status.in_(
                    [ContentSourceStatus.QUEUED, ContentSourceStatus.PROCESSING, ContentSourceStatus.GENERATED]
                )
            )
            .where(ContentSource.created_at >= today_start)
        )
        return int((await session.execute(stmt)).scalar_one())

    pairs = run_async(_get_sources)

    # Group by page to apply daily_reel_target cap.
    from collections import defaultdict
    by_page: dict[UUID, tuple[Any, list[Any]]] = defaultdict(lambda: (None, []))
    for source, page in pairs:
        pid = UUID(str(page.id))
        by_page[pid] = (page, by_page[pid][1] + [source])

    total_queued = 0
    total_errors = 0

    for pid, (page, sources) in by_page.items():
        try:
            today_count = run_async(
                lambda session, _pid=pid: _count_today_generated(session, _pid)
            )
            slots = max(0, (page.daily_reel_target or 3) - today_count)
            eligible = sources[:slots]

            for source in eligible:
                try:
                    _queue_one_source(source, page)
                    total_queued += 1
                except Exception as exc:
                    total_errors += 1
                    logger.error(
                        "queue_sources_for_generation: source={} error={}", source.id, exc
                    )
        except Exception as exc:
            total_errors += 1
            logger.error(
                "queue_sources_for_generation: page={} error={}", pid, exc
            )

    return {"queued": total_queued, "errors": total_errors}


def _queue_one_source(source: Any, page: Any) -> None:
    """Promote one content_source to queued, create a job, enqueue download."""
    import asyncio

    from database.models import ContentSource, Job
    from shared_py.enums import JobStatus
    from apps.workers.event_publisher import publish_sync
    from shared_py.events import ContentQueuedEvent, ContentQueuedPayload

    source_id = UUID(str(source.id))
    page_id = UUID(str(page.id))
    source_url = source.source_url

    # Build source_metadata for the job row (mirrors the spec).
    job_source_metadata: dict[str, Any] = {
        "source_type": "auto_discovery",
        "facebook_page_id": str(page_id),
        "content_source_id": str(source_id),
        "niche": page.niche,
    }

    async def _create_job_and_update(session: AsyncSession) -> str:
        # Mark source as queued.
        await session.execute(
            update(ContentSource)
            .where(ContentSource.id == source_id)
            .values(status=ContentSourceStatus.QUEUED)
        )

        # Create the job row.
        job = Job(
            source_url=source_url,
            status=JobStatus.QUEUED,
            progress_pct=0.0,
            source_metadata=job_source_metadata,
        )
        session.add(job)
        await session.flush()
        await session.refresh(job)

        # Mark source as processing.
        await session.execute(
            update(ContentSource)
            .where(ContentSource.id == source_id)
            .values(status=ContentSourceStatus.PROCESSING)
        )

        return str(job.id)

    job_id = run_async(_create_job_and_update)

    # Enqueue download task (mirrors jobs.py POST handler pattern).
    try:
        celery.send_task(
            "download.fetch_source",
            args=[job_id, source_url],
            queue="download",
        )
    except Exception as exc:
        logger.warning(
            "_queue_one_source: celery dispatch failed source={} err={}", source_id, exc
        )

    # Publish SSE content.queued (best-effort).
    try:
        event = ContentQueuedEvent(
            job_id=source_id,
            payload=ContentQueuedPayload(
                content_source_id=source_id,
                page_id=page_id,
            ),
        )
        publish_sync(str(source_id), event)
    except Exception as exc:
        logger.warning(
            "_queue_one_source: SSE publish failed source={} err={}", source_id, exc
        )

    logger.info(
        "_queue_one_source: source={} job={} url={}", source_id, job_id, source_url
    )
