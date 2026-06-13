"""Jobs router: create, list, detail, clips, SSE event stream."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from loguru import logger
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, TypeAdapter, ValidationError as PydValidationError
from sqlalchemy import select

from app import celery_client
from app.config import get_settings
from app.deps import CeleryDep, RedisDep, SessionDep
from app.errors import AppError, NotFoundError, ValidationError
from app.services import event_bus, job_service
from app.sse import format_sse_frame, merge_with_keepalive, sse_response
from database.models import Log
from shared_py.enums import JobStatus
from shared_py.events import JobCreatedEvent, JobCreatedPayload
from shared_py.schemas import (
    CreateJobRequest,
    JobDTO,
    ListClipsResponse,
    ListJobsResponse,
)


class LogDTO(BaseModel):
    """Structured log entry returned by `GET /jobs/{id}/logs`.

    Mirrors `database.models.Log` for client consumption. Defined inline because
    the shared schema package does not export a log shape yet.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    stage: str | None
    level: str
    message: str
    payload: dict[str, Any] | None
    created_at: datetime


class ListLogsResponse(BaseModel):
    logs: list[LogDTO]
    total: int


router = APIRouter(prefix="/jobs", tags=["jobs"])

_URL_VALIDATOR: TypeAdapter[AnyHttpUrl] = TypeAdapter(AnyHttpUrl)


def _to_job_dto(job_row: object) -> JobDTO:
    return JobDTO.model_validate(job_row)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=JobDTO)
async def create_job(
    body: CreateJobRequest,
    session: SessionDep,
    redis: RedisDep,
    celery: CeleryDep,  # noqa: ARG001 - present for DI clarity; we call celery_client.dispatch
    request: Request,
) -> JobDTO:
    """Create a job, dispatch the download task, and publish `job.created`."""
    try:
        _URL_VALIDATOR.validate_python(body.source_url)
    except PydValidationError as exc:
        raise ValidationError(
            "Invalid source_url",
            details={"errors": exc.errors()},
        ) from exc

    job = await job_service.create_job(session, body.source_url)
    await session.commit()
    await session.refresh(job)

    try:
        celery_client.dispatch(
            "download.fetch_source",
            args=[str(job.id), str(body.source_url)],
            queue="download",
        )
    except Exception as exc:
        logger.bind(job_id=str(job.id)).warning(
            "Celery dispatch failed for download.fetch_source: {}", exc
        )

    event = JobCreatedEvent(
        job_id=job.id,
        payload=JobCreatedPayload(source_url=job.source_url),
    )
    try:
        await event_bus.publish_event(
            redis,
            job.id,
            event,
            session_factory=request.app.state.session_factory,
        )
    except Exception as exc:
        logger.bind(job_id=str(job.id)).warning("Failed to publish job.created: {}", exc)

    return _to_job_dto(job)


@router.get("", response_model=ListJobsResponse)
async def list_jobs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[list[JobStatus] | None, Query(alias="status")] = None,
) -> ListJobsResponse:
    items, total = await job_service.list_jobs(
        session, limit=limit, offset=offset, statuses=status_filter
    )
    return ListJobsResponse(
        jobs=[_to_job_dto(j) for j in items],
        total=total,
    )


@router.get("/{job_id}", response_model=JobDTO)
async def get_job(job_id: UUID, session: SessionDep) -> JobDTO:
    job = await job_service.get_job(session, job_id)
    return _to_job_dto(job)


@router.get("/{job_id}/clips", response_model=ListClipsResponse)
async def get_job_clips(job_id: UUID, session: SessionDep) -> ListClipsResponse:
    from shared_py.schemas import ClipDTO
    from database.models import RenderTask, Thumbnail, Asset

    clips = await job_service.get_job_clips(session, job_id)

    # Map clip_id -> output asset_id via render_tasks (the latest successful render).
    rt_rows = (
        await session.execute(
            select(RenderTask.clip_id, RenderTask.output_asset_id)
            .where(RenderTask.clip_id.in_([c.id for c in clips]))
            .where(RenderTask.output_asset_id.is_not(None))
            .order_by(RenderTask.created_at.desc())
        )
    ).all()
    video_asset_by_clip: dict[UUID, UUID] = {}
    for clip_id, asset_id in rt_rows:
        video_asset_by_clip.setdefault(clip_id, asset_id)

    # Map clip_id -> thumbnail asset_id by joining thumbnails.path to assets.path.
    thumb_rows = (
        await session.execute(
            select(Thumbnail.clip_id, Asset.id)
            .join(Asset, Asset.path == Thumbnail.path)
            .where(Thumbnail.clip_id.in_([c.id for c in clips]))
            .where(Asset.kind == "clip_thumbnail")
        )
    ).all()
    thumb_asset_by_clip: dict[UUID, UUID] = {}
    for clip_id, asset_id in thumb_rows:
        thumb_asset_by_clip.setdefault(clip_id, asset_id)

    dtos: list[ClipDTO] = []
    for c in clips:
        # `clips.edit_plan` is seeded with only `{highlight_segments: ...}` at
        # detection time and overwritten with the full EditPlan once plan_edit
        # finishes. While plan_edit is pending, the seed is not a valid EditPlan;
        # fall back to None so the clip row still serialises.
        try:
            dto = ClipDTO.model_validate(c)
        except Exception:
            from copy import copy

            stub = copy(c)
            stub.edit_plan = None
            dto = ClipDTO.model_validate(stub)
        dto = dto.model_copy(
            update={
                "video_asset_id": video_asset_by_clip.get(c.id),
                "thumbnail_asset_id": thumb_asset_by_clip.get(c.id),
            }
        )
        dtos.append(dto)
    return ListClipsResponse(clips=dtos)


@router.get("/{job_id}/logs", response_model=ListLogsResponse)
async def get_job_logs(
    job_id: UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
    levels: Annotated[
        list[str] | None,
        Query(alias="level", description="Optional level filter; defaults to info+."),
    ] = None,
) -> ListLogsResponse:
    """Return structured pipeline log entries for a job in chronological order.

    Used by the Friendly Step Log viewer on the frontend. Backed by the `logs`
    table which is populated by `event_bus.publish_event` as a side effect of
    every SSE event emitted by the workers.
    """
    # 404 if the job doesn't exist; reuses centralized error mapping.
    await job_service.get_job(session, job_id)

    default_levels = ("INFO", "WARNING", "ERROR", "CRITICAL")
    requested_levels = (
        tuple(lvl.upper() for lvl in levels) if levels else default_levels
    )

    stmt = (
        select(Log)
        .where(Log.job_id == job_id)
        .where(Log.level.in_(requested_levels))
        .order_by(Log.created_at.asc(), Log.id.asc())
        .offset(offset)
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())

    return ListLogsResponse(
        logs=[LogDTO.model_validate(r) for r in rows],
        total=len(rows),
    )


@router.get("/{job_id}/events")
async def stream_events(
    job_id: UUID,
    session: SessionDep,
    redis: RedisDep,
):
    """SSE: replay recent logs for the job, then forward Redis pub/sub events."""
    await job_service.get_job(session, job_id)

    stmt = (
        select(Log)
        .where(Log.job_id == job_id)
        .order_by(Log.created_at.desc())
        .limit(200)
    )
    recent_rows = list((await session.execute(stmt)).scalars().all())
    replay_rows = list(reversed(recent_rows))  # chrono order

    async def producer() -> AsyncIterator[bytes]:
        for row in replay_rows:
            event_type = row.stage or "log"
            data = row.payload if row.payload is not None else {"message": row.message}
            yield format_sse_frame(
                event=event_type,
                data=data,
                event_id=_event_id_for_log(row.id, row.created_at),
            )

        async for parsed in event_bus.subscribe(redis, job_id):
            event_type = str(parsed.get("type", "message"))
            try:
                body = json.dumps(parsed, default=str)
            except (TypeError, ValueError):
                body = json.dumps({"raw": str(parsed)})
            yield format_sse_frame(event=event_type, data=body)

    return sse_response(merge_with_keepalive(producer()))


def _event_id_for_log(row_id: UUID, created_at: datetime | None) -> str:
    if created_at is not None:
        return f"{int(created_at.timestamp() * 1000)}-{row_id}"
    return str(row_id)


# Kinds that contain JSON the UI is allowed to inspect. Anything else (clip
# videos, thumbnails, raw audio) is binary and must be downloaded via
# `/assets/{id}/download` instead.
_INSPECTABLE_JSON_KINDS: frozenset[str] = frozenset(
    {
        "transcript_json",
        "diarization_json",
        "yolo_json",
        "analysis_json",
        "edit_plan_json",
    }
)

# Kinds that may yield more than one asset per job and should always be
# returned as a list of `{filename, content}` envelopes.
_MULTI_FILE_KINDS: frozenset[str] = frozenset({"edit_plan_json"})


class ArtifactFile(BaseModel):
    """One artifact file: storage path stem + parsed JSON payload."""

    filename: str
    asset_id: UUID
    size_bytes: int | None = None
    created_at: datetime
    content: Any


class ArtifactResponse(BaseModel):
    """Envelope returned by `GET /jobs/{id}/artifacts/{kind}`.

    For single-file kinds (e.g. `transcript_json`) we still return a list of
    one file so the client has a single uniform shape to render.
    """

    job_id: UUID
    kind: str
    files: list[ArtifactFile]


def _resolve_artifact_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(get_settings().STORAGE_LOCAL_PATH, path)


@router.get("/{job_id}/artifacts/{kind}", response_model=ArtifactResponse)
async def get_job_artifact(
    job_id: UUID,
    kind: str,
    session: SessionDep,
) -> ArtifactResponse:
    """Return parsed JSON artifact(s) for a `(job, kind)` pair.

    - 404 when the job has no asset of this kind on disk.
    - 415 when the kind is binary (clip_video, clip_thumbnail, ...).
    - 400 when the kind is unknown.

    For `edit_plan_json` all matching assets are returned (one per clip),
    sorted by filename so the UI gets a stable ordering across reloads.
    """
    if kind not in _INSPECTABLE_JSON_KINDS:
        # Binary kinds are valid AssetKind values but not inspectable here.
        binary_kinds = {
            "source_video",
            "source_audio",
            "source_thumbnail",
            "clip_video",
            "clip_thumbnail",
            "subtitle_ass",
        }
        if kind in binary_kinds:
            raise AppError(
                f"Kind '{kind}' is binary and cannot be inspected as JSON. "
                "Use /assets/{id}/download instead.",
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                code="unsupported_media",
            )
        raise ValidationError(
            f"Unknown artifact kind '{kind}'",
            details={"allowed": sorted(_INSPECTABLE_JSON_KINDS)},
        )

    # 404 if the job doesn't exist; reuses centralized error mapping.
    await job_service.get_job(session, job_id)

    assets = await job_service.list_assets_by_kind(session, job_id, kind)
    if not assets:
        raise NotFoundError(
            f"No '{kind}' artifact found for job {job_id}",
            details={"job_id": str(job_id), "kind": kind},
        )

    # For single-file kinds, keep only the newest record.
    if kind not in _MULTI_FILE_KINDS:
        assets = assets[:1]
    else:
        # Stable, human-friendly ordering by filename (clip-0, clip-1, ...).
        assets = sorted(assets, key=lambda a: os.path.basename(a.path))

    files: list[ArtifactFile] = []
    for asset in assets:
        fs_path = _resolve_artifact_path(asset.path)
        if not os.path.exists(fs_path) or not os.path.isfile(fs_path):
            # Soft-skip rather than 500 so a partial job remains inspectable.
            logger.bind(job_id=str(job_id), kind=kind, path=asset.path).warning(
                "Artifact asset row exists but file is missing on storage"
            )
            continue
        try:
            with open(fs_path, "rb") as fh:
                raw = fh.read()
            content = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.bind(job_id=str(job_id), kind=kind, path=asset.path).warning(
                "Failed to load artifact JSON: {}", exc
            )
            continue

        files.append(
            ArtifactFile(
                filename=os.path.basename(asset.path) or str(asset.id),
                asset_id=asset.id,
                size_bytes=asset.size_bytes,
                created_at=asset.created_at,
                content=content,
            )
        )

    if not files:
        raise NotFoundError(
            f"All '{kind}' artifact files for job {job_id} are missing or unreadable",
            details={"job_id": str(job_id), "kind": kind},
        )

    return ArtifactResponse(job_id=job_id, kind=kind, files=files)
