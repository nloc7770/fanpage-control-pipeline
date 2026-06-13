"""SSE event payload shapes.

These models are serialized to JSON and emitted over the Redis pub/sub channel
`job:{job_id}`. The API SSE handler reads them and forwards them to the client.
Mirrors packages/shared-types/src/index.ts.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shared_py.enums import WorkerType


class SSEEventType(StrEnum):
    JOB_CREATED = "job.created"
    JOB_PROGRESS = "job.progress"
    JOB_STAGE_CHANGED = "job.stage_changed"
    JOB_FAILED = "job.failed"
    JOB_COMPLETED = "job.completed"
    STAGE_COMPLETE = "stage.complete"
    CLIP_PLANNED = "clip.planned"
    CLIP_RENDERING = "clip.rendering"
    CLIP_RENDERED = "clip.rendered"
    CLIP_FAILED = "clip.failed"
    WORKER_HEARTBEAT = "worker.heartbeat"
    # Facebook / publishing pipeline
    CONTENT_DISCOVERED = "content.discovered"
    CONTENT_QUEUED = "content.queued"
    REEL_GENERATED = "reel.generated"
    REEL_PENDING_REVIEW = "reel.pending_review"
    REEL_APPROVED = "reel.approved"
    REEL_REJECTED = "reel.rejected"
    REEL_SCHEDULED = "reel.scheduled"
    REEL_PUBLISHING = "reel.publishing"
    REEL_PUBLISHED = "reel.published"
    REEL_FAILED = "reel.failed"
    # Image posts pipeline
    IMAGE_POST_GENERATING = "image_post.generating"
    IMAGE_POST_GENERATED = "image_post.generated"
    IMAGE_POST_PENDING_REVIEW = "image_post.pending_review"
    IMAGE_POST_APPROVED = "image_post.approved"
    IMAGE_POST_SCHEDULED = "image_post.scheduled"
    IMAGE_POST_PUBLISHING = "image_post.publishing"
    IMAGE_POST_PUBLISHED = "image_post.published"
    IMAGE_POST_FAILED = "image_post.failed"


P = TypeVar("P", bound=BaseModel)


class BaseSSEEvent(BaseModel, Generic[P]):
    model_config = ConfigDict(populate_by_name=True)

    type: SSEEventType
    job_id: UUID
    ts: datetime = Field(default_factory=datetime.utcnow)
    payload: P


# -- Payloads --------------------------------------------------------------


class JobCreatedPayload(BaseModel):
    source_url: str


class JobProgressPayload(BaseModel):
    stage: str
    pct: float = Field(ge=0.0, le=100.0)
    message: str | None = None


class JobStageChangedPayload(BaseModel):
    from_stage: str | None = Field(default=None, alias="from")
    to: str

    model_config = ConfigDict(populate_by_name=True)


class JobFailedPayload(BaseModel):
    stage: str
    error: str


class JobCompletedPayload(BaseModel):
    clip_count: int
    duration_s: float


class StageCompletePayload(BaseModel):
    """Per-stage "what was done" facts emitted at the success boundary of a task.

    The ``stage`` field is the short stage name (``"download"``, ``"transcribe"``,
    ``"vision"``, ``"analyze"``, ``"detect_clips"``, ``"plan_edit"``, ``"render"``,
    ``"thumbnail"``) and the rest is free-form for the frontend's friendly
    step-log renderer. We don't pin the shape further so each worker can carry
    the numbers most useful for its stage without churn here -- the frontend
    code lives next to a TS mirror and is the canonical reader.
    """

    model_config = ConfigDict(extra="allow")

    stage: str
    engine: str | None = None
    elapsed_s: float | None = None


class ClipPlannedPayload(BaseModel):
    clip_id: UUID
    clip_index: int
    title: str
    virality_score: float


class ClipRenderingPayload(BaseModel):
    clip_id: UUID
    clip_index: int
    pct: float = Field(ge=0.0, le=100.0)


class ClipRenderedPayload(BaseModel):
    clip_id: UUID
    clip_index: int
    asset_id: UUID


class ClipFailedPayload(BaseModel):
    clip_id: UUID
    clip_index: int
    error: str


class WorkerHeartbeatPayload(BaseModel):
    worker_id: str
    worker_type: WorkerType
    task: str | None = None


# -- Concrete event aliases ------------------------------------------------


class JobCreatedEvent(BaseSSEEvent[JobCreatedPayload]):
    type: Literal[SSEEventType.JOB_CREATED] = SSEEventType.JOB_CREATED


class JobProgressEvent(BaseSSEEvent[JobProgressPayload]):
    type: Literal[SSEEventType.JOB_PROGRESS] = SSEEventType.JOB_PROGRESS


class JobStageChangedEvent(BaseSSEEvent[JobStageChangedPayload]):
    type: Literal[SSEEventType.JOB_STAGE_CHANGED] = SSEEventType.JOB_STAGE_CHANGED


class JobFailedEvent(BaseSSEEvent[JobFailedPayload]):
    type: Literal[SSEEventType.JOB_FAILED] = SSEEventType.JOB_FAILED


class JobCompletedEvent(BaseSSEEvent[JobCompletedPayload]):
    type: Literal[SSEEventType.JOB_COMPLETED] = SSEEventType.JOB_COMPLETED


class StageCompleteEvent(BaseSSEEvent[StageCompletePayload]):
    type: Literal[SSEEventType.STAGE_COMPLETE] = SSEEventType.STAGE_COMPLETE


class ClipPlannedEvent(BaseSSEEvent[ClipPlannedPayload]):
    type: Literal[SSEEventType.CLIP_PLANNED] = SSEEventType.CLIP_PLANNED


class ClipRenderingEvent(BaseSSEEvent[ClipRenderingPayload]):
    type: Literal[SSEEventType.CLIP_RENDERING] = SSEEventType.CLIP_RENDERING


class ClipRenderedEvent(BaseSSEEvent[ClipRenderedPayload]):
    type: Literal[SSEEventType.CLIP_RENDERED] = SSEEventType.CLIP_RENDERED


class ClipFailedEvent(BaseSSEEvent[ClipFailedPayload]):
    type: Literal[SSEEventType.CLIP_FAILED] = SSEEventType.CLIP_FAILED


class WorkerHeartbeatEvent(BaseSSEEvent[WorkerHeartbeatPayload]):
    type: Literal[SSEEventType.WORKER_HEARTBEAT] = SSEEventType.WORKER_HEARTBEAT


# -- Facebook / publishing payloads ----------------------------------------


class ContentDiscoveredPayload(BaseModel):
    content_source_id: UUID
    page_id: UUID
    source_url: str
    platform: str


class ContentQueuedPayload(BaseModel):
    content_source_id: UUID
    page_id: UUID


class ReelGeneratedPayload(BaseModel):
    reel_draft_id: UUID
    page_id: UUID
    title: str | None = None


class ReelPendingReviewPayload(BaseModel):
    reel_draft_id: UUID
    page_id: UUID


class ReelApprovedPayload(BaseModel):
    reel_draft_id: UUID
    page_id: UUID
    approved_by: str | None = None


class ReelRejectedPayload(BaseModel):
    reel_draft_id: UUID
    page_id: UUID
    reason: str | None = None


class ReelScheduledPayload(BaseModel):
    reel_draft_id: UUID
    page_id: UUID
    scheduled_at: datetime


class ReelPublishingPayload(BaseModel):
    reel_draft_id: UUID
    page_id: UUID
    publish_job_id: UUID


class ReelPublishedPayload(BaseModel):
    reel_draft_id: UUID
    page_id: UUID
    facebook_video_id: str
    facebook_post_id: str | None = None


class ReelFailedPayload(BaseModel):
    reel_draft_id: UUID
    page_id: UUID
    error: str


# -- Image post payloads ---------------------------------------------------


class ImagePostGeneratingPayload(BaseModel):
    image_post_id: UUID
    page_id: UUID
    source_topic: str | None = None


class ImagePostGeneratedPayload(BaseModel):
    image_post_id: UUID
    page_id: UUID
    image_count: int


class ImagePostPendingReviewPayload(BaseModel):
    image_post_id: UUID
    page_id: UUID


class ImagePostApprovedPayload(BaseModel):
    image_post_id: UUID
    page_id: UUID
    approved_by: str | None = None


class ImagePostScheduledPayload(BaseModel):
    image_post_id: UUID
    page_id: UUID
    scheduled_at: datetime


class ImagePostPublishingPayload(BaseModel):
    image_post_id: UUID
    page_id: UUID


class ImagePostPublishedPayload(BaseModel):
    image_post_id: UUID
    page_id: UUID
    facebook_post_id: str


class ImagePostFailedPayload(BaseModel):
    image_post_id: UUID
    page_id: UUID
    error: str


# -- Facebook event aliases ------------------------------------------------


class ContentDiscoveredEvent(BaseSSEEvent[ContentDiscoveredPayload]):
    type: Literal[SSEEventType.CONTENT_DISCOVERED] = SSEEventType.CONTENT_DISCOVERED


class ContentQueuedEvent(BaseSSEEvent[ContentQueuedPayload]):
    type: Literal[SSEEventType.CONTENT_QUEUED] = SSEEventType.CONTENT_QUEUED


class ReelGeneratedEvent(BaseSSEEvent[ReelGeneratedPayload]):
    type: Literal[SSEEventType.REEL_GENERATED] = SSEEventType.REEL_GENERATED


class ReelPendingReviewEvent(BaseSSEEvent[ReelPendingReviewPayload]):
    type: Literal[SSEEventType.REEL_PENDING_REVIEW] = SSEEventType.REEL_PENDING_REVIEW


class ReelApprovedEvent(BaseSSEEvent[ReelApprovedPayload]):
    type: Literal[SSEEventType.REEL_APPROVED] = SSEEventType.REEL_APPROVED


class ReelRejectedEvent(BaseSSEEvent[ReelRejectedPayload]):
    type: Literal[SSEEventType.REEL_REJECTED] = SSEEventType.REEL_REJECTED


class ReelScheduledEvent(BaseSSEEvent[ReelScheduledPayload]):
    type: Literal[SSEEventType.REEL_SCHEDULED] = SSEEventType.REEL_SCHEDULED


class ReelPublishingEvent(BaseSSEEvent[ReelPublishingPayload]):
    type: Literal[SSEEventType.REEL_PUBLISHING] = SSEEventType.REEL_PUBLISHING


class ReelPublishedEvent(BaseSSEEvent[ReelPublishedPayload]):
    type: Literal[SSEEventType.REEL_PUBLISHED] = SSEEventType.REEL_PUBLISHED


class ReelFailedEvent(BaseSSEEvent[ReelFailedPayload]):
    type: Literal[SSEEventType.REEL_FAILED] = SSEEventType.REEL_FAILED


# -- Image post event aliases ----------------------------------------------


class ImagePostGeneratingEvent(BaseSSEEvent[ImagePostGeneratingPayload]):
    type: Literal[SSEEventType.IMAGE_POST_GENERATING] = SSEEventType.IMAGE_POST_GENERATING


class ImagePostGeneratedEvent(BaseSSEEvent[ImagePostGeneratedPayload]):
    type: Literal[SSEEventType.IMAGE_POST_GENERATED] = SSEEventType.IMAGE_POST_GENERATED


class ImagePostPendingReviewEvent(BaseSSEEvent[ImagePostPendingReviewPayload]):
    type: Literal[SSEEventType.IMAGE_POST_PENDING_REVIEW] = SSEEventType.IMAGE_POST_PENDING_REVIEW


class ImagePostApprovedEvent(BaseSSEEvent[ImagePostApprovedPayload]):
    type: Literal[SSEEventType.IMAGE_POST_APPROVED] = SSEEventType.IMAGE_POST_APPROVED


class ImagePostScheduledEvent(BaseSSEEvent[ImagePostScheduledPayload]):
    type: Literal[SSEEventType.IMAGE_POST_SCHEDULED] = SSEEventType.IMAGE_POST_SCHEDULED


class ImagePostPublishingEvent(BaseSSEEvent[ImagePostPublishingPayload]):
    type: Literal[SSEEventType.IMAGE_POST_PUBLISHING] = SSEEventType.IMAGE_POST_PUBLISHING


class ImagePostPublishedEvent(BaseSSEEvent[ImagePostPublishedPayload]):
    type: Literal[SSEEventType.IMAGE_POST_PUBLISHED] = SSEEventType.IMAGE_POST_PUBLISHED


class ImagePostFailedEvent(BaseSSEEvent[ImagePostFailedPayload]):
    type: Literal[SSEEventType.IMAGE_POST_FAILED] = SSEEventType.IMAGE_POST_FAILED


SSEEvent = (
    JobCreatedEvent
    | JobProgressEvent
    | JobStageChangedEvent
    | JobFailedEvent
    | JobCompletedEvent
    | StageCompleteEvent
    | ClipPlannedEvent
    | ClipRenderingEvent
    | ClipRenderedEvent
    | ClipFailedEvent
    | WorkerHeartbeatEvent
    | ContentDiscoveredEvent
    | ContentQueuedEvent
    | ReelGeneratedEvent
    | ReelPendingReviewEvent
    | ReelApprovedEvent
    | ReelRejectedEvent
    | ReelScheduledEvent
    | ReelPublishingEvent
    | ReelPublishedEvent
    | ReelFailedEvent
    | ImagePostGeneratingEvent
    | ImagePostGeneratedEvent
    | ImagePostPendingReviewEvent
    | ImagePostApprovedEvent
    | ImagePostScheduledEvent
    | ImagePostPublishingEvent
    | ImagePostPublishedEvent
    | ImagePostFailedEvent
)
