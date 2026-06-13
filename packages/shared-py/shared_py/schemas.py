"""Pydantic DTOs returned by the API. Mirrors packages/shared-types/src/index.ts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shared_py.enums import AssetKind, ClipStage, JobStatus
from shared_py.llm_contracts import EditPlan


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SourceMetadata(_Base):
    title: str | None = None
    duration_s: float | None = None
    thumbnail_url: str | None = None
    uploader: str | None = None
    upload_date: str | None = None
    view_count: int | None = None


class JobDTO(_Base):
    id: UUID
    source_url: str
    status: JobStatus
    progress_pct: float
    current_stage: str | None
    error_message: str | None
    source_metadata: SourceMetadata | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class AssetDTO(_Base):
    id: UUID
    job_id: UUID
    kind: AssetKind
    path: str
    size_bytes: int | None
    mime: str | None
    metadata: dict[str, Any] | None
    created_at: datetime


class ClipDTO(_Base):
    id: UUID
    job_id: UUID
    clip_index: int
    start_time: float
    end_time: float
    duration: float
    virality_score: float
    main_hook: str | None = None
    emotional_peak: str | None = None
    retention_reason: str | None = None
    topics: list[str] = Field(default_factory=list)
    target_style: str | None = None
    title: str | None = None
    narrative_script_vi: str | None = None
    edit_plan: EditPlan | None
    status: ClipStage
    video_asset_id: UUID | None = None
    thumbnail_asset_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# REST request/response shapes
# ---------------------------------------------------------------------------


class CreateJobOptions(_Base):
    enable_diarization: bool | None = None
    target_clip_count: int | None = Field(default=None, ge=1, le=20)
    language_hint: str | None = None


class CreateJobRequest(_Base):
    source_url: str
    options: CreateJobOptions | None = None


class ListJobsResponse(_Base):
    jobs: list[JobDTO]
    total: int


class ListClipsResponse(_Base):
    clips: list[ClipDTO]


class LogDTO(_Base):
    """Structured log entry served by ``GET /jobs/{id}/logs``.

    Mirrors ``database.models.Log``. The ``stage`` field carries the SSE event
    type (e.g. ``"stage.complete"``) and ``payload`` is the per-stage facts
    dictionary the workers emit (``engine``, ``elapsed_s``, counts, etc.).
    """

    id: UUID
    stage: str | None = None
    level: str
    message: str
    payload: dict[str, Any] | None = None
    created_at: datetime


class ListLogsResponse(_Base):
    logs: list[LogDTO]
    total: int


# ---------------------------------------------------------------------------
# Facebook / publishing DTOs
# ---------------------------------------------------------------------------

from shared_py.enums import (  # noqa: E402 — after _Base definition
    ApprovalStatus,
    ContentSourceStatus,
    FacebookAccountStatus,
    FacebookPageStatus,
    PublishJobStatus,
    PublishStatus,
)


class FacebookAccountDTO(_Base):
    id: UUID
    provider_user_id: str
    display_name: str
    avatar_url: str | None = None
    # encrypted_access_token intentionally omitted
    token_expires_at: datetime | None = None
    status: FacebookAccountStatus
    created_at: datetime
    updated_at: datetime


class FacebookPageDTO(_Base):
    id: UUID
    account_id: UUID
    page_id: str
    page_name: str
    avatar_url: str
    # encrypted_page_access_token intentionally omitted
    permissions: dict[str, Any] = Field(default_factory=dict)
    niche: str | None = None
    language: str | None = None
    content_keywords: list[str] = Field(default_factory=list)
    blocked_keywords: list[str] = Field(default_factory=list)
    daily_reel_target: int = 3
    posting_time_slots: list[dict[str, Any]] = Field(default_factory=list)
    auto_generate_enabled: bool = False
    require_manual_approval: bool = True
    status: FacebookPageStatus
    created_at: datetime
    updated_at: datetime


class ContentSourceDTO(_Base):
    id: UUID
    page_id: UUID
    platform: str
    source_url: str
    source_title: str | None = None
    channel_name: str | None = None
    duration_seconds: int | None = None
    thumbnail_url: str | None = None
    detected_topic: str | None = None
    status: ContentSourceStatus
    rejection_reason: str | None = None
    source_metadata: dict[str, Any] | None = None
    created_at: datetime


class ReelDraftDTO(_Base):
    id: UUID
    page_id: UUID
    clip_id: UUID | None = None
    content_source_id: UUID | None = None
    title: str | None = None
    caption: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    suggested_post_time: datetime | None = None
    approval_status: ApprovalStatus
    publish_status: PublishStatus
    facebook_video_id: str | None = None
    facebook_post_id: str | None = None
    error_message: str | None = None
    created_at: datetime
    approved_at: datetime | None = None
    scheduled_at: datetime | None = None
    published_at: datetime | None = None


class PublishJobDTO(_Base):
    id: UUID
    reel_draft_id: UUID
    page_id: UUID
    status: PublishJobStatus
    scheduled_at: datetime | None = None
    retry_count: int = 0
    error_message: str | None = None
    created_at: datetime
    published_at: datetime | None = None


# -- Request / response shapes -----------------------------------------------


class CreateFacebookAccountRequest(_Base):
    provider_user_id: str
    display_name: str
    avatar_url: str | None = None
    access_token: str
    token_expires_at: datetime | None = None


class UpdateFacebookAccountRequest(_Base):
    display_name: str | None = None
    avatar_url: str | None = None
    access_token: str | None = None
    token_expires_at: datetime | None = None
    status: FacebookAccountStatus | None = None


class CreateFacebookPageRequest(_Base):
    account_id: UUID
    page_id: str
    page_name: str
    avatar_url: str
    page_access_token: str
    permissions: dict[str, Any] = Field(default_factory=dict)
    niche: str | None = None
    language: str | None = None


class UpdateFacebookPageRequest(_Base):
    page_name: str | None = None
    avatar_url: str | None = None
    page_access_token: str | None = None
    permissions: dict[str, Any] | None = None
    niche: str | None = None
    language: str | None = None
    content_keywords: list[str] | None = None
    blocked_keywords: list[str] | None = None
    daily_reel_target: int | None = None
    posting_time_slots: list[dict[str, Any]] | None = None
    auto_generate_enabled: bool | None = None
    require_manual_approval: bool | None = None
    status: FacebookPageStatus | None = None


class ListFacebookAccountsResponse(_Base):
    accounts: list[FacebookAccountDTO]
    total: int


class ListFacebookPagesResponse(_Base):
    pages: list[FacebookPageDTO]
    total: int


class ListContentSourcesResponse(_Base):
    sources: list[ContentSourceDTO]
    total: int


class ListReelDraftsResponse(_Base):
    drafts: list[ReelDraftDTO]
    total: int


class UpdateReelDraftRequest(_Base):
    title: str | None = None
    caption: str | None = None
    hashtags: list[str] | None = None
    suggested_post_time: datetime | None = None
    approval_status: ApprovalStatus | None = None


class ScheduleReelRequest(_Base):
    scheduled_at: datetime


class ListPublishJobsResponse(_Base):
    jobs: list[PublishJobDTO]
    total: int


# ---------------------------------------------------------------------------
# Image posts DTOs
# ---------------------------------------------------------------------------


class ImagePostDTO(_Base):
    id: UUID
    page_id: UUID
    source_topic: str | None = None
    caption: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    image_paths: list[str] = Field(default_factory=list)
    image_count: int = 1
    aspect_ratio: str = "16:9"
    approval_status: ApprovalStatus
    publish_status: PublishStatus
    facebook_post_id: str | None = None
    error_message: str | None = None
    generation_metadata: dict[str, Any] | None = None
    created_at: datetime
    approved_at: datetime | None = None
    scheduled_at: datetime | None = None
    published_at: datetime | None = None


class CreateImagePostRequest(_Base):
    page_id: UUID
    source_topic: str | None = None
    # Optional manual overrides — if omitted the gen service fills them in
    caption: str | None = None
    hashtags: list[str] | None = None
    image_count: int = Field(default=1, ge=1, le=3)
    aspect_ratio: str = "16:9"


class UpdateImagePostRequest(_Base):
    caption: str | None = None
    hashtags: list[str] | None = None
    scheduled_at: datetime | None = None


class ApproveImagePostRequest(_Base):
    publish_now: bool = False
    scheduled_at: datetime | None = None


class ListImagePostsResponse(_Base):
    posts: list[ImagePostDTO]
    total: int
