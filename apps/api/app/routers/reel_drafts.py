"""Reel drafts router: review, approve, reject, schedule, and regenerate captions."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import CeleryDep, SessionDep
from app.errors import AppError, NotFoundError, ValidationError
from database.models import ReelDraft
from shared_py.enums import ApprovalStatus, PublishStatus
from shared_py.schemas import ListReelDraftsResponse, ReelDraftDTO

router = APIRouter(prefix="/reel-drafts", tags=["reel-drafts"])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class ApproveReelDraftRequest(BaseModel):
    publish_now: bool = False
    scheduled_at: datetime | None = None


class RejectReelDraftRequest(BaseModel):
    reason: str | None = None


class PatchReelDraftRequest(BaseModel):
    title: str | None = None
    caption: str | None = None
    hashtags: list[str] | None = None
    scheduled_at: datetime | None = None


class ScheduleReelDraftRequest(BaseModel):
    scheduled_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_manual_approval() -> bool:
    return os.environ.get("REQUIRE_MANUAL_APPROVAL", "true").lower() in ("1", "true", "yes")


def _to_dto(row: ReelDraft) -> ReelDraftDTO:
    return ReelDraftDTO.model_validate(row)


async def _get_draft_or_404(session: AsyncSession, draft_id: UUID) -> ReelDraft:
    q = await session.execute(select(ReelDraft).where(ReelDraft.id == draft_id))
    draft = q.scalar_one_or_none()
    if draft is None:
        raise NotFoundError(f"Reel draft {draft_id} not found")
    return draft


# ---------------------------------------------------------------------------
# GET /reel-drafts
# ---------------------------------------------------------------------------


@router.get("", response_model=ListReelDraftsResponse)
async def list_reel_drafts(
    session: SessionDep,
    page_id: Annotated[UUID | None, Query()] = None,
    approval_status: Annotated[ApprovalStatus | None, Query()] = None,
    publish_status: Annotated[PublishStatus | None, Query()] = None,
    from_date: Annotated[datetime | None, Query()] = None,
    to_date: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ListReelDraftsResponse:
    """Paginated list of reel drafts with optional filters."""
    stmt = select(ReelDraft)
    count_stmt = select(func.count()).select_from(ReelDraft)

    filters = []
    if page_id is not None:
        filters.append(ReelDraft.page_id == page_id)
    if approval_status is not None:
        filters.append(ReelDraft.approval_status == approval_status)
    if publish_status is not None:
        filters.append(ReelDraft.publish_status == publish_status)
    if from_date is not None:
        filters.append(ReelDraft.created_at >= from_date)
    if to_date is not None:
        filters.append(ReelDraft.created_at <= to_date)

    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)

    stmt = stmt.order_by(ReelDraft.created_at.desc()).limit(limit).offset(offset)

    rows_q = await session.execute(stmt)
    rows = rows_q.scalars().all()

    total_q = await session.execute(count_stmt)
    total = total_q.scalar_one()

    return ListReelDraftsResponse(
        drafts=[_to_dto(r) for r in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /reel-drafts/{id}
# ---------------------------------------------------------------------------


class ReelDraftDetailDTO(ReelDraftDTO):
    """Extends ReelDraftDTO with clip preview fields."""
    video_asset_id: UUID | None = None
    thumbnail_asset_id: UUID | None = None


@router.get("/{draft_id}", response_model=ReelDraftDetailDTO)
async def get_reel_draft(
    draft_id: UUID,
    session: SessionDep,
) -> ReelDraftDetailDTO:
    """Single reel draft with nested clip preview info."""
    draft = await _get_draft_or_404(session, draft_id)

    video_asset_id: UUID | None = None
    thumbnail_asset_id: UUID | None = None

    if draft.clip_id:
        from database.models import Asset, RenderTask
        from shared_py.enums import AssetKind

        # video asset: latest render task output
        rt_q = await session.execute(
            select(RenderTask)
            .where(RenderTask.clip_id == draft.clip_id)
            .order_by(RenderTask.created_at.desc())
            .limit(1)
        )
        rt = rt_q.scalar_one_or_none()
        if rt is not None and rt.output_asset_id:
            video_asset_id = rt.output_asset_id

        # thumbnail asset
        thumb_q = await session.execute(
            select(Asset).where(
                Asset.job_id.in_(
                    select(Asset.job_id).where(Asset.id == video_asset_id)
                ) if video_asset_id else Asset.id == None,  # noqa: E711
                Asset.kind == AssetKind.CLIP_THUMBNAIL,
            ).limit(1)
        )
        thumb = thumb_q.scalar_one_or_none()
        if thumb:
            thumbnail_asset_id = thumb.id

    base = _to_dto(draft)
    return ReelDraftDetailDTO(
        **base.model_dump(),
        video_asset_id=video_asset_id,
        thumbnail_asset_id=thumbnail_asset_id,
    )


# ---------------------------------------------------------------------------
# PATCH /reel-drafts/{id}
# ---------------------------------------------------------------------------


@router.patch("/{draft_id}", response_model=ReelDraftDTO)
async def patch_reel_draft(
    draft_id: UUID,
    body: PatchReelDraftRequest,
    session: SessionDep,
) -> ReelDraftDTO:
    """Update title/caption/hashtags/scheduled_at (only when not published)."""
    draft = await _get_draft_or_404(session, draft_id)

    if draft.approval_status == ApprovalStatus.APPROVED and draft.publish_status == PublishStatus.PUBLISHED:
        raise ValidationError("Cannot edit a published reel draft")

    if body.title is not None:
        draft.title = body.title
    if body.caption is not None:
        draft.caption = body.caption
    if body.hashtags is not None:
        draft.hashtags = body.hashtags
    if body.scheduled_at is not None:
        draft.scheduled_at = body.scheduled_at

    await session.flush()
    return _to_dto(draft)


# ---------------------------------------------------------------------------
# POST /reel-drafts/{id}/approve
# ---------------------------------------------------------------------------


@router.post("/{draft_id}/approve", response_model=ReelDraftDTO)
async def approve_reel_draft(
    draft_id: UUID,
    body: ApproveReelDraftRequest,
    session: SessionDep,
    celery: CeleryDep,
) -> ReelDraftDTO:
    """Approve a reel draft.

    - ``publish_now=true``: immediately enqueue facebook.publish_reel_draft.
    - ``scheduled_at`` set: set publish_status=scheduled.
    - Otherwise: leave as draft (manual publish later).

    REQUIRE_MANUAL_APPROVAL env is respected: publish_now is only honoured
    after an explicit approve action (this endpoint).
    """
    draft = await _get_draft_or_404(session, draft_id)

    if draft.approval_status == ApprovalStatus.APPROVED:
        raise ValidationError("Reel draft is already approved")
    if draft.publish_status == PublishStatus.PUBLISHED:
        raise ValidationError("Cannot approve a published reel draft")

    draft.approval_status = ApprovalStatus.APPROVED
    draft.approved_at = datetime.utcnow()

    if body.scheduled_at is not None:
        draft.publish_status = PublishStatus.SCHEDULED
        draft.scheduled_at = body.scheduled_at
    elif body.publish_now:
        # Enqueue immediate publish via Agent A's task.
        draft.publish_status = PublishStatus.PUBLISHING
        await session.flush()
        celery.send_task(
            "facebook.publish_reel_draft",
            kwargs={"reel_draft_id": str(draft_id)},
            queue="facebook",
        )
        logger.info("approve_reel_draft: enqueued publish for draft={}", draft_id)
    # else: leave publish_status as DRAFT

    await session.flush()
    return _to_dto(draft)


# ---------------------------------------------------------------------------
# POST /reel-drafts/{id}/reject
# ---------------------------------------------------------------------------


@router.post("/{draft_id}/reject", response_model=ReelDraftDTO)
async def reject_reel_draft(
    draft_id: UUID,
    body: RejectReelDraftRequest,
    session: SessionDep,
) -> ReelDraftDTO:
    """Reject a reel draft with an optional reason."""
    draft = await _get_draft_or_404(session, draft_id)

    if draft.publish_status == PublishStatus.PUBLISHED:
        raise ValidationError("Cannot reject a published reel draft")

    draft.approval_status = ApprovalStatus.REJECTED
    if body.reason:
        draft.error_message = body.reason

    await session.flush()
    return _to_dto(draft)


# ---------------------------------------------------------------------------
# POST /reel-drafts/{id}/regenerate-caption
# ---------------------------------------------------------------------------


@router.post("/{draft_id}/regenerate-caption", status_code=status.HTTP_202_ACCEPTED)
async def regenerate_caption(
    draft_id: UUID,
    session: SessionDep,
    celery: CeleryDep,
) -> dict[str, str]:
    """Enqueue caption regeneration for an existing draft."""
    # Verify draft exists.
    await _get_draft_or_404(session, draft_id)

    celery.send_task(
        "reels.generate_caption_for_draft",
        kwargs={"reel_draft_id": str(draft_id)},
        queue="reels",
    )
    logger.info("regenerate_caption: enqueued for draft={}", draft_id)
    return {"status": "queued", "reel_draft_id": str(draft_id)}


# ---------------------------------------------------------------------------
# POST /reel-drafts/{id}/schedule
# ---------------------------------------------------------------------------


@router.post("/{draft_id}/schedule", response_model=ReelDraftDTO)
async def schedule_reel_draft(
    draft_id: UUID,
    body: ScheduleReelDraftRequest,
    session: SessionDep,
) -> ReelDraftDTO:
    """Schedule an approved draft for publishing at a specific time."""
    draft = await _get_draft_or_404(session, draft_id)

    if draft.approval_status != ApprovalStatus.APPROVED:
        raise ValidationError("Reel draft must be approved before scheduling")
    if draft.publish_status == PublishStatus.PUBLISHED:
        raise ValidationError("Cannot schedule a published reel draft")

    draft.publish_status = PublishStatus.SCHEDULED
    draft.scheduled_at = body.scheduled_at

    await session.flush()
    return _to_dto(draft)


# ---------------------------------------------------------------------------
# POST /reel-drafts/{id}/cancel-schedule
# ---------------------------------------------------------------------------


@router.post("/{draft_id}/cancel-schedule", response_model=ReelDraftDTO)
async def cancel_schedule(
    draft_id: UUID,
    session: SessionDep,
) -> ReelDraftDTO:
    """Cancel a scheduled draft, reverting publish_status to draft."""
    draft = await _get_draft_or_404(session, draft_id)

    if draft.publish_status != PublishStatus.SCHEDULED:
        raise ValidationError("Reel draft is not scheduled")

    draft.publish_status = PublishStatus.DRAFT
    draft.scheduled_at = None

    await session.flush()
    return _to_dto(draft)
