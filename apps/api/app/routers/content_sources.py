"""REST routes for content_sources.

Endpoints:
- GET    /content-sources                    — list with pagination + filters
- GET    /content-sources/{id}               — single source
- POST   /content-sources/{id}/reject        — set status=rejected
- POST   /content-sources/{id}/queue         — manually queue one source
- DELETE /content-sources/{id}               — hard-delete source + cascade jobs/files
- POST   /facebook/pages/{id}/discover       — trigger discovery for one page
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Query, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import delete as sql_delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import celery_client
from app.config import get_settings
from app.deps import SessionDep
from app.errors import NotFoundError, ValidationError
from database.models import ContentSource, FacebookPage, Job, PublishJob, ReelDraft
from shared_py.enums import ContentSourceStatus, FacebookPageStatus
from shared_py.schemas import ContentSourceDTO, ListContentSourcesResponse


router = APIRouter(tags=["content-sources"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_source_or_404(session: AsyncSession, source_id: UUID) -> ContentSource:
    row = await session.get(ContentSource, source_id)
    if row is None:
        raise NotFoundError(f"ContentSource {source_id} not found")
    return row


async def _get_page_or_404(session: AsyncSession, page_id: UUID) -> FacebookPage:
    row = await session.get(FacebookPage, page_id)
    if row is None:
        raise NotFoundError(f"FacebookPage {page_id} not found")
    return row


def _to_dto(row: ContentSource) -> ContentSourceDTO:
    return ContentSourceDTO.model_validate(row)


# ---------------------------------------------------------------------------
# GET /content-sources
# ---------------------------------------------------------------------------


@router.get("/content-sources", response_model=ListContentSourcesResponse)
async def list_content_sources(
    session: SessionDep,
    page_id: Annotated[UUID | None, Query(description="Filter by Facebook page")] = None,
    status_filter: Annotated[
        ContentSourceStatus | None, Query(alias="status")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ListContentSourcesResponse:
    """List content sources with optional page_id / status filters."""
    filters = []
    if page_id is not None:
        filters.append(ContentSource.page_id == page_id)
    if status_filter is not None:
        filters.append(ContentSource.status == status_filter)

    list_stmt = (
        select(ContentSource)
        .order_by(ContentSource.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count()).select_from(ContentSource)
    for f in filters:
        list_stmt = list_stmt.where(f)
        count_stmt = count_stmt.where(f)

    items = list((await session.execute(list_stmt)).scalars().all())
    total = int((await session.execute(count_stmt)).scalar_one())

    return ListContentSourcesResponse(
        sources=[_to_dto(r) for r in items],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /content-sources/{id}
# ---------------------------------------------------------------------------


@router.get("/content-sources/{source_id}", response_model=ContentSourceDTO)
async def get_content_source(
    source_id: UUID,
    session: SessionDep,
) -> ContentSourceDTO:
    row = await _get_source_or_404(session, source_id)
    return _to_dto(row)


# ---------------------------------------------------------------------------
# POST /content-sources/{id}/reject
# ---------------------------------------------------------------------------


class RejectSourceRequest(BaseModel):
    rejection_reason: str | None = None


@router.post(
    "/content-sources/{source_id}/reject",
    response_model=ContentSourceDTO,
    status_code=status.HTTP_200_OK,
)
async def reject_content_source(
    source_id: UUID,
    session: SessionDep,
    body: RejectSourceRequest = Body(default_factory=RejectSourceRequest),
) -> ContentSourceDTO:
    """Set a content source status to rejected with an optional reason."""
    row = await _get_source_or_404(session, source_id)

    if row.status in (ContentSourceStatus.PROCESSING, ContentSourceStatus.GENERATED):
        raise ValidationError(
            f"Cannot reject a source in status '{row.status}'",
            details={"current_status": row.status},
        )

    row.status = ContentSourceStatus.REJECTED
    row.rejection_reason = body.rejection_reason
    await session.flush()
    await session.refresh(row)
    return _to_dto(row)


# ---------------------------------------------------------------------------
# POST /content-sources/{id}/queue
# ---------------------------------------------------------------------------


@router.post(
    "/content-sources/{source_id}/queue",
    response_model=ContentSourceDTO,
    status_code=status.HTTP_200_OK,
)
async def queue_content_source(
    source_id: UUID,
    session: SessionDep,
) -> ContentSourceDTO:
    """Manually queue a single discovered content source for generation."""
    row = await _get_source_or_404(session, source_id)

    if row.status != ContentSourceStatus.DISCOVERED:
        raise ValidationError(
            f"Source must be in 'discovered' status to queue; current: '{row.status}'",
            details={"current_status": row.status},
        )

    # Dispatch the Celery task for this specific page_id.
    try:
        celery_client.dispatch(
            "discovery.queue_sources_for_generation",
            args=[str(row.page_id)],
            queue="discovery",
        )
    except Exception as exc:
        logger.warning(
            "queue_content_source: celery dispatch failed source={} err={}", source_id, exc
        )

    # Optimistically mark as queued so the response reflects the intent.
    row.status = ContentSourceStatus.QUEUED
    await session.flush()
    await session.refresh(row)
    return _to_dto(row)


# ---------------------------------------------------------------------------
# DELETE /content-sources/{id}
# ---------------------------------------------------------------------------


class DeleteContentSourceResponse(BaseModel):
    source_id: str
    deleted_jobs: int
    deleted_reel_drafts: int
    deleted_publish_jobs: int
    deleted_files: int
    freed_bytes: int


def _wipe_local_dir(root: Path, job_id: UUID) -> tuple[int, int]:
    """Delete `{root}/{job_id}` recursively. Returns (files_removed, bytes_freed)."""
    job_dir = root / str(job_id)
    if not job_dir.exists():
        return 0, 0

    files_removed = 0
    bytes_freed = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(job_dir):
            for fname in filenames:
                fp = Path(dirpath) / fname
                try:
                    bytes_freed += fp.stat().st_size
                    files_removed += 1
                except OSError as exc:
                    logger.warning("delete_content_source: stat failed path={} err={}", fp, exc)
        shutil.rmtree(job_dir, ignore_errors=True)
    except Exception as exc:
        logger.warning(
            "delete_content_source: rmtree failed dir={} err={}", job_dir, exc
        )
    return files_removed, bytes_freed


def _wipe_extra_path(path_str: str, already_under: Path | None) -> tuple[int, int]:
    """Best-effort unlink of an asset path that lives outside the per-job dir."""
    try:
        p = Path(path_str)
        if not p.is_absolute() or not p.exists() or not p.is_file():
            return 0, 0
        if already_under is not None:
            try:
                p.resolve().relative_to(already_under.resolve())
                return 0, 0  # already counted by rmtree
            except ValueError:
                pass
        size = p.stat().st_size
        p.unlink()
        return 1, size
    except Exception as exc:
        logger.warning("delete_content_source: unlink failed path={} err={}", path_str, exc)
        return 0, 0


@router.delete(
    "/content-sources/{source_id}",
    response_model=DeleteContentSourceResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_content_source(
    source_id: UUID,
    session: SessionDep,
) -> DeleteContentSourceResponse:
    """Hard-delete a ContentSource and every artefact it produced.

    Cascades:
    - Jobs created from this source (linked via `Job.source_metadata->>content_source_id`)
      → cascade-deletes assets/clips/transcripts/render_tasks/etc via FK ON DELETE.
    - ReelDraft rows (FK content_source_id ON DELETE CASCADE) → cascade-deletes PublishJob.
    - On-disk files under `{STORAGE_LOCAL_PATH}/{job_id}/` for each linked job, plus any
      asset paths recorded outside that directory.

    Allowed in any source status. Returns counts of removed rows + bytes freed.
    """
    source = await _get_source_or_404(session, source_id)

    # 1. Find jobs linked through source_metadata JSONB.
    #    Use a portable accessor: prefer the PG `astext` extractor, but fall back to
    #    JSON path operator which works on SQLite (the test backend renders JSONB as JSON).
    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""
    if dialect_name == "postgresql":
        link_expr = Job.source_metadata["content_source_id"].astext
    else:
        link_expr = Job.source_metadata.op("->>")("content_source_id")

    job_rows = (
        await session.execute(select(Job.id).where(link_expr == str(source_id)))
    ).scalars().all()
    job_ids: list[UUID] = list(job_rows)

    # 2. Count related reel_drafts + publish_jobs BEFORE deleting (response counters).
    reel_draft_id_rows = (
        await session.execute(
            select(ReelDraft.id).where(ReelDraft.content_source_id == source_id)
        )
    ).scalars().all()
    deleted_reel_drafts = len(reel_draft_id_rows)
    if reel_draft_id_rows:
        deleted_publish_jobs = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PublishJob)
                    .where(PublishJob.reel_draft_id.in_(reel_draft_id_rows))
                )
            ).scalar_one()
        )
    else:
        deleted_publish_jobs = 0

    # 3. Collect extra asset paths (defense-in-depth) before DB cascade clears them.
    extra_asset_paths: list[str] = []
    if job_ids:
        from database.models import Asset  # local import to avoid widening module scope

        rows = await session.execute(
            select(Asset.path).where(Asset.job_id.in_(job_ids))
        )
        extra_asset_paths = [r for r in rows.scalars().all() if r]

    # 4. Wipe files (only when storage backend is local).
    deleted_files = 0
    freed_bytes = 0
    settings = get_settings()
    if (settings.STORAGE_BACKEND or "").lower() == "local":
        try:
            local_root = Path(settings.STORAGE_LOCAL_PATH)
        except Exception:
            local_root = Path(os.environ.get("STORAGE_LOCAL_PATH", "_storage_data"))

        for jid in job_ids:
            f, b = _wipe_local_dir(local_root, jid)
            deleted_files += f
            freed_bytes += b

        for ap in extra_asset_paths:
            f, b = _wipe_extra_path(ap, already_under=local_root)
            deleted_files += f
            freed_bytes += b

    # 5. Delete the Job rows (cascade clears assets/clips/transcripts/etc).
    if job_ids:
        await session.execute(sql_delete(Job).where(Job.id.in_(job_ids)))

    # 6. Delete the ContentSource row (cascade clears reel_drafts → publish_jobs).
    await session.delete(source)
    await session.flush()
    # Commit happens in the SessionDep dependency wrapper.

    return DeleteContentSourceResponse(
        source_id=str(source_id),
        deleted_jobs=len(job_ids),
        deleted_reel_drafts=deleted_reel_drafts,
        deleted_publish_jobs=deleted_publish_jobs,
        deleted_files=deleted_files,
        freed_bytes=freed_bytes,
    )


# ---------------------------------------------------------------------------
# POST /facebook/pages/{id}/discover
# ---------------------------------------------------------------------------


@router.post(
    "/facebook/pages/{page_id}/discover",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_discover_for_page(
    page_id: UUID,
    session: SessionDep,
) -> dict[str, Any]:
    """Immediately trigger YouTube discovery for one Facebook page (async via Celery)."""
    page = await _get_page_or_404(session, page_id)

    if page.status != FacebookPageStatus.ACTIVE:
        raise ValidationError(
            f"Page must be active to trigger discovery; current: '{page.status}'",
            details={"current_status": page.status},
        )

    try:
        task_id = celery_client.dispatch(
            "discovery.find_content_for_pages",
            args=[],
            queue="discovery",
        )
    except Exception as exc:
        logger.warning(
            "trigger_discover_for_page: celery dispatch failed page={} err={}", page_id, exc
        )
        task_id = None

    return {
        "page_id": str(page_id),
        "task_id": task_id,
        "message": "Discovery task dispatched",
    }
