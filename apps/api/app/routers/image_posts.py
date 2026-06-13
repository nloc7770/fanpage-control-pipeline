"""Image posts router: list, detail, patch, approve, reject, schedule, regenerate."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import CeleryDep, SessionDep
from app.errors import NotFoundError, ValidationError
from database.models import ImagePost
from shared_py.enums import ApprovalStatus, PublishStatus
from shared_py.schemas import ImagePostDTO, ListImagePostsResponse

router = APIRouter(tags=["image-posts"])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class PatchImagePostRequest(BaseModel):
    caption: str | None = None
    hashtags: list[str] | None = None
    scheduled_at: datetime | None = None


class ApproveImagePostRequest(BaseModel):
    publish_now: bool = False
    scheduled_at: datetime | None = None


class RejectImagePostRequest(BaseModel):
    reason: str | None = None


class ScheduleImagePostRequest(BaseModel):
    scheduled_at: datetime


class GenerateImagePostRequest(BaseModel):
    topic: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_dto(row: ImagePost) -> ImagePostDTO:
    return ImagePostDTO.model_validate(row)


async def _get_post_or_404(session: AsyncSession, post_id: UUID) -> ImagePost:
    q = await session.execute(select(ImagePost).where(ImagePost.id == post_id))
    post = q.scalar_one_or_none()
    if post is None:
        raise NotFoundError(f"Image post {post_id} not found")
    return post


# ---------------------------------------------------------------------------
# GET /image-posts
# ---------------------------------------------------------------------------


@router.get("/image-posts", response_model=ListImagePostsResponse)
async def list_image_posts(
    session: SessionDep,
    page_id: Annotated[UUID | None, Query()] = None,
    approval_status: Annotated[ApprovalStatus | None, Query()] = None,
    publish_status: Annotated[PublishStatus | None, Query()] = None,
    from_date: Annotated[datetime | None, Query()] = None,
    to_date: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ListImagePostsResponse:
    """Paginated list of image posts with optional filters."""
    stmt = select(ImagePost)
    count_stmt = select(func.count()).select_from(ImagePost)

    filters = []
    if page_id is not None:
        filters.append(ImagePost.page_id == page_id)
    if approval_status is not None:
        filters.append(ImagePost.approval_status == approval_status)
    if publish_status is not None:
        filters.append(ImagePost.publish_status == publish_status)
    if from_date is not None:
        filters.append(ImagePost.created_at >= from_date)
    if to_date is not None:
        filters.append(ImagePost.created_at <= to_date)

    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)

    stmt = stmt.order_by(ImagePost.created_at.desc()).limit(limit).offset(offset)

    rows_q = await session.execute(stmt)
    rows = rows_q.scalars().all()

    total_q = await session.execute(count_stmt)
    total = total_q.scalar_one()

    return ListImagePostsResponse(
        posts=[_to_dto(r) for r in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /image-posts/{id}
# ---------------------------------------------------------------------------


@router.get("/image-posts/{post_id}", response_model=ImagePostDTO)
async def get_image_post(
    post_id: UUID,
    session: SessionDep,
) -> ImagePostDTO:
    """Single image post detail."""
    post = await _get_post_or_404(session, post_id)
    return _to_dto(post)


# ---------------------------------------------------------------------------
# PATCH /image-posts/{id}
# ---------------------------------------------------------------------------


@router.patch("/image-posts/{post_id}", response_model=ImagePostDTO)
async def patch_image_post(
    post_id: UUID,
    body: PatchImagePostRequest,
    session: SessionDep,
) -> ImagePostDTO:
    """Update caption, hashtags, or scheduled_at (only when not published)."""
    post = await _get_post_or_404(session, post_id)

    if post.publish_status == PublishStatus.PUBLISHED:
        raise ValidationError("Cannot edit a published image post")

    if body.caption is not None:
        post.caption = body.caption
    if body.hashtags is not None:
        post.hashtags = body.hashtags
    if body.scheduled_at is not None:
        post.scheduled_at = body.scheduled_at

    await session.flush()
    return _to_dto(post)


# ---------------------------------------------------------------------------
# POST /image-posts/{id}/regenerate-image
# ---------------------------------------------------------------------------


@router.post(
    "/image-posts/{post_id}/regenerate-image",
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_image(
    post_id: UUID,
    session: SessionDep,
    celery: CeleryDep,
) -> dict[str, Any]:
    """Enqueue image regeneration for an existing post (keeps caption)."""
    post = await _get_post_or_404(session, post_id)

    if post.publish_status == PublishStatus.PUBLISHED:
        raise ValidationError("Cannot regenerate image for a published post")

    celery.send_task(
        "image_posts.regenerate_image",
        kwargs={"image_post_id": str(post_id)},
        queue="image_posts",
    )
    logger.info("regenerate_image: enqueued for post={}", post_id)
    return {"status": "queued", "image_post_id": str(post_id)}


# ---------------------------------------------------------------------------
# POST /image-posts/{id}/regenerate-caption
# ---------------------------------------------------------------------------


@router.post(
    "/image-posts/{post_id}/regenerate-caption",
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_caption(
    post_id: UUID,
    session: SessionDep,
    celery: CeleryDep,
) -> dict[str, Any]:
    """Enqueue caption regeneration for an existing post (keeps image)."""
    post = await _get_post_or_404(session, post_id)

    if post.publish_status == PublishStatus.PUBLISHED:
        raise ValidationError("Cannot regenerate caption for a published post")

    celery.send_task(
        "image_posts.regenerate_caption",
        kwargs={"image_post_id": str(post_id)},
        queue="image_posts",
    )
    logger.info("regenerate_caption: enqueued for post={}", post_id)
    return {"status": "queued", "image_post_id": str(post_id)}


# ---------------------------------------------------------------------------
# POST /image-posts/{id}/approve
# ---------------------------------------------------------------------------


@router.post("/image-posts/{post_id}/approve", response_model=ImagePostDTO)
async def approve_image_post(
    post_id: UUID,
    body: ApproveImagePostRequest,
    session: SessionDep,
    celery: CeleryDep,
) -> ImagePostDTO:
    """Approve an image post.

    - ``publish_now=true``: immediately enqueue image_posts.publish_one.
    - ``scheduled_at`` set: set publish_status=scheduled.
    - Otherwise: leave as draft.
    """
    post = await _get_post_or_404(session, post_id)

    if post.approval_status == ApprovalStatus.APPROVED:
        raise ValidationError("Image post is already approved")
    if post.publish_status == PublishStatus.PUBLISHED:
        raise ValidationError("Cannot approve a published image post")

    post.approval_status = ApprovalStatus.APPROVED
    post.approved_at = datetime.utcnow()

    if body.scheduled_at is not None:
        post.publish_status = PublishStatus.SCHEDULED
        post.scheduled_at = body.scheduled_at
    elif body.publish_now:
        post.publish_status = PublishStatus.PUBLISHING
        await session.flush()
        celery.send_task(
            "image_posts.publish_one",
            kwargs={"image_post_id": str(post_id)},
            queue="image_posts",
        )
        logger.info("approve_image_post: enqueued publish for post={}", post_id)
    # else: leave publish_status as DRAFT

    await session.flush()
    return _to_dto(post)


# ---------------------------------------------------------------------------
# POST /image-posts/{id}/reject
# ---------------------------------------------------------------------------


@router.post("/image-posts/{post_id}/reject", response_model=ImagePostDTO)
async def reject_image_post(
    post_id: UUID,
    body: RejectImagePostRequest,
    session: SessionDep,
) -> ImagePostDTO:
    """Reject an image post with an optional reason."""
    post = await _get_post_or_404(session, post_id)

    if post.publish_status == PublishStatus.PUBLISHED:
        raise ValidationError("Cannot reject a published image post")

    post.approval_status = ApprovalStatus.REJECTED
    if body.reason:
        post.error_message = body.reason

    await session.flush()
    return _to_dto(post)


# ---------------------------------------------------------------------------
# POST /image-posts/{id}/schedule
# ---------------------------------------------------------------------------


@router.post("/image-posts/{post_id}/schedule", response_model=ImagePostDTO)
async def schedule_image_post(
    post_id: UUID,
    body: ScheduleImagePostRequest,
    session: SessionDep,
) -> ImagePostDTO:
    """Schedule an approved image post for publishing at a specific time."""
    post = await _get_post_or_404(session, post_id)

    if post.approval_status != ApprovalStatus.APPROVED:
        raise ValidationError("Image post must be approved before scheduling")
    if post.publish_status == PublishStatus.PUBLISHED:
        raise ValidationError("Cannot schedule a published image post")

    post.publish_status = PublishStatus.SCHEDULED
    post.scheduled_at = body.scheduled_at

    await session.flush()
    return _to_dto(post)


# ---------------------------------------------------------------------------
# POST /image-posts/{id}/cancel-schedule
# ---------------------------------------------------------------------------


@router.post("/image-posts/{post_id}/cancel-schedule", response_model=ImagePostDTO)
async def cancel_schedule(
    post_id: UUID,
    session: SessionDep,
) -> ImagePostDTO:
    """Cancel a scheduled image post, reverting publish_status to draft."""
    post = await _get_post_or_404(session, post_id)

    if post.publish_status != PublishStatus.SCHEDULED:
        raise ValidationError("Image post is not scheduled")

    post.publish_status = PublishStatus.DRAFT
    post.scheduled_at = None

    await session.flush()
    return _to_dto(post)


# ---------------------------------------------------------------------------
# POST /facebook/pages/{page_id}/generate-image-post
# ---------------------------------------------------------------------------


@router.post(
    "/facebook/pages/{page_id}/generate-image-post",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_image_post_for_page(
    page_id: UUID,
    body: GenerateImagePostRequest,
    celery: CeleryDep,
) -> dict[str, Any]:
    """Manually trigger immediate image post generation for a page."""
    kwargs: dict[str, Any] = {"page_id": str(page_id)}
    if body.topic:
        kwargs["source_topic"] = body.topic

    celery.send_task(
        "image_posts.generate_one",
        kwargs=kwargs,
        queue="image_posts",
    )
    logger.info(
        "generate_image_post_for_page: enqueued page={} topic={!r}",
        page_id,
        body.topic,
    )
    return {"status": "queued", "page_id": str(page_id), "topic": body.topic}
