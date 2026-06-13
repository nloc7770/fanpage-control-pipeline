"""Discover router: `POST /discover`.

Thin HTTP wrapper around :func:`services.discover.runner.discover`. The
heavy lifting (yt-dlp crawl, filtering, ranking) lives in the service
module; this layer just translates request/response shapes and handles the
optional auto-submit fan-out to `/jobs`.
"""

from __future__ import annotations

import asyncio
import sys
import os
from typing import Any

from fastapi import APIRouter, Request, status
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

# The `services` package lives at the repo root, not under `apps/api`. The API
# is launched with `PYTHONPATH=.` from the repo root in production, but make
# the import robust for ad-hoc runs too.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.discover.runner import VideoCandidate, discover as _discover  # noqa: E402

from app import celery_client  # noqa: E402
from app.deps import RedisDep, SessionDep  # noqa: E402
from app.services import event_bus, job_service  # noqa: E402
from shared_py.events import JobCreatedEvent, JobCreatedPayload  # noqa: E402


router = APIRouter(prefix="/discover", tags=["discover"])


class DiscoverRequest(BaseModel):
    """Request body for `POST /discover`."""

    model_config = ConfigDict(populate_by_name=True)

    topic: str = Field(..., min_length=1, description="Search topic, e.g. 'tarpon fishing'")
    top: int = Field(default=5, ge=1, le=50)
    min_views: int = Field(default=50_000, ge=0)
    max_age_days: int | None = Field(default=180, ge=1)
    require_english: bool = Field(default=True)
    auto_submit: bool = Field(default=False, description="If true, POST each candidate to /jobs.")


class VideoCandidateDTO(BaseModel):
    """API-facing representation of a `VideoCandidate`."""

    video_id: str
    url: str
    title: str
    channel: str
    channel_id: str
    views: int
    duration_s: float
    upload_date: str
    description: str = ""
    score: float
    reasons: list[str] = []


class DiscoverResponse(BaseModel):
    topic: str
    candidates: list[VideoCandidateDTO]
    submitted_job_ids: list[str] | None = None


def _to_dto(c: VideoCandidate) -> VideoCandidateDTO:
    return VideoCandidateDTO(
        video_id=c.video_id,
        url=c.url,
        title=c.title,
        channel=c.channel,
        channel_id=c.channel_id,
        views=c.views,
        duration_s=c.duration_s,
        upload_date=c.upload_date,
        description=c.description,
        score=c.score,
        reasons=list(c.reasons),
    )


async def _create_and_dispatch_job(
    session: Any,
    redis: Any,
    session_factory: Any,
    source_url: str,
) -> str | None:
    """Replicates `POST /jobs` (sans HTTP) so auto-submit avoids a self-call.

    We deliberately mirror the side effects of `app.routers.jobs.create_job`:
    insert the row, commit, dispatch the celery download task, and publish
    the `job.created` event onto Redis so the SSE stream is consistent.
    Failures in celery / event publish are logged-and-swallowed for the same
    reason the original endpoint does it: the job exists, downstream
    failures shouldn't 500 the discover request.
    """
    job = await job_service.create_job(session, source_url)
    await session.commit()
    await session.refresh(job)

    try:
        celery_client.dispatch(
            "download.fetch_source",
            args=[str(job.id), source_url],
            queue="download",
        )
    except Exception as exc:  # pragma: no cover - infra failure path
        logger.bind(job_id=str(job.id)).warning(
            "Celery dispatch failed for download.fetch_source: {}", exc
        )

    event = JobCreatedEvent(
        job_id=job.id,
        payload=JobCreatedPayload(source_url=job.source_url),
    )
    try:
        await event_bus.publish_event(
            redis, job.id, event, session_factory=session_factory
        )
    except Exception as exc:  # pragma: no cover
        logger.bind(job_id=str(job.id)).warning("Failed to publish job.created: {}", exc)

    return str(job.id)


@router.post("", response_model=DiscoverResponse, status_code=status.HTTP_200_OK)
async def discover_endpoint(
    body: DiscoverRequest,
    session: SessionDep,
    redis: RedisDep,
    request: Request,
) -> DiscoverResponse:
    """Crawl + rank YouTube for `topic` and optionally submit the top picks.

    The crawl is synchronous yt-dlp I/O; we offload it to a thread so the
    event loop stays responsive while a request is in flight (relevant when
    multiple clients hit `/discover` concurrently).
    """
    candidates: list[VideoCandidate] = await asyncio.to_thread(
        _discover,
        body.topic,
        top=body.top,
        min_views=body.min_views,
        max_age_days=body.max_age_days,
        require_english=body.require_english,
    )

    dtos = [_to_dto(c) for c in candidates]
    submitted_ids: list[str] | None = None

    if body.auto_submit and candidates:
        submitted_ids = []
        session_factory = request.app.state.session_factory
        for cand in candidates:
            try:
                jid = await _create_and_dispatch_job(
                    session, redis, session_factory, cand.url
                )
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "auto_submit failed for {}: {}", cand.url, exc
                )
                continue
            if jid:
                submitted_ids.append(jid)

    return DiscoverResponse(
        topic=body.topic,
        candidates=dtos,
        submitted_job_ids=submitted_ids,
    )
