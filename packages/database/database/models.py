"""SQLAlchemy 2.x ORM models for Shortform Factory.

All models use UUID primary keys, `created_at`/`updated_at` timestamps where
appropriate, and indices on foreign keys plus the high-traffic `jobs.status`
and `clips.job_id` columns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_py.enums import (
    ApprovalStatus,
    AssetKind,
    ClipStage,
    ContentSourceStatus,
    FacebookAccountStatus,
    FacebookPageStatus,
    JobStatus,
    PublishJobStatus,
    PublishStatus,
)

from database.base import Base


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )


def _ts_created() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _ts_updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# Postgres enum names (must match the migration).
JOB_STATUS_ENUM = SAEnum(
    JobStatus,
    name="job_status",
    native_enum=True,
    validate_strings=True,
    values_callable=lambda E: [e.value for e in E],
)
CLIP_STAGE_ENUM = SAEnum(
    ClipStage,
    name="clip_stage",
    native_enum=True,
    validate_strings=True,
    values_callable=lambda E: [e.value for e in E],
)
ASSET_KIND_ENUM = SAEnum(
    AssetKind,
    name="asset_kind",
    native_enum=True,
    validate_strings=True,
    values_callable=lambda E: [e.value for e in E],
)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[UUID] = _uuid_pk()
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        JOB_STATUS_ENUM, nullable=False, default=JobStatus.QUEUED, index=True
    )
    progress_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assets: Mapped[list[Asset]] = relationship(back_populates="job", cascade="all, delete-orphan")
    transcript: Mapped[Transcript | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    speakers: Mapped[list[Speaker]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    analysis: Mapped[AnalysisResult | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    clips: Mapped[list[Clip]] = relationship(back_populates="job", cascade="all, delete-orphan")
    logs: Mapped[list[Log]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[UUID] = _uuid_pk()
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[AssetKind] = mapped_column(ASSET_KIND_ENUM, nullable=False, index=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    asset_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = _ts_created()

    job: Mapped[Job] = relationship(back_populates="assets")


class Transcript(Base):
    __tablename__ = "transcripts"
    __table_args__ = (
        Index("ix_transcripts_job_id_unique", "job_id", unique=True),
    )

    id: Mapped[UUID] = _uuid_pk()
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    words: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = _ts_created()

    job: Mapped[Job] = relationship(back_populates="transcript")


class Speaker(Base):
    __tablename__ = "speakers"

    id: Mapped[UUID] = _uuid_pk()
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    speaker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = _ts_created()

    job: Mapped[Job] = relationship(back_populates="speakers")


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    __table_args__ = (
        Index("ix_analysis_results_job_id_unique", "job_id", unique=True),
    )

    id: Mapped[UUID] = _uuid_pk()
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    emotional_peaks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    viral_moments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    topic_shifts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    retention_signals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts_created()

    job: Mapped[Job] = relationship(back_populates="analysis")


class Clip(Base):
    __tablename__ = "clips"
    __table_args__ = (
        Index("ix_clips_job_id", "job_id"),
        Index("ix_clips_job_id_clip_index", "job_id", "clip_index", unique=True),
    )

    id: Mapped[UUID] = _uuid_pk()
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    clip_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    duration: Mapped[float] = mapped_column(Float, nullable=False)
    virality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    main_hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    emotional_peak: Mapped[str | None] = mapped_column(Text, nullable=True)
    retention_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    topics: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    target_style: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    narrative_script_vi: Mapped[str | None] = mapped_column(Text, nullable=True)
    edit_plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[ClipStage] = mapped_column(
        CLIP_STAGE_ENUM, nullable=False, default=ClipStage.PLANNED, index=True
    )
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    job: Mapped[Job] = relationship(back_populates="clips")
    render_tasks: Mapped[list[RenderTask]] = relationship(
        back_populates="clip", cascade="all, delete-orphan"
    )
    thumbnails: Mapped[list[Thumbnail]] = relationship(
        back_populates="clip", cascade="all, delete-orphan"
    )


class RenderTask(Base):
    __tablename__ = "render_tasks"

    id: Mapped[UUID] = _uuid_pk()
    clip_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("clips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[ClipStage] = mapped_column(
        CLIP_STAGE_ENUM, nullable=False, default=ClipStage.PLANNED
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ffmpeg_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_asset_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    progress_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = _ts_created()

    clip: Mapped[Clip] = relationship(back_populates="render_tasks")


class Thumbnail(Base):
    __tablename__ = "thumbnails"

    id: Mapped[UUID] = _uuid_pk()
    clip_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("clips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    frame_timestamp: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = _ts_created()

    clip: Mapped[Clip] = relationship(back_populates="thumbnails")


class Log(Base):
    __tablename__ = "logs"
    __table_args__ = (
        Index("ix_logs_job_id_created_at", "job_id", "created_at"),
        Index("ix_logs_clip_id_created_at", "clip_id", "created_at"),
    )

    id: Mapped[UUID] = _uuid_pk()
    job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=True,
    )
    clip_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("clips.id", ondelete="CASCADE"),
        nullable=True,
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _ts_created()

    job: Mapped[Job | None] = relationship(back_populates="logs")


# Re-export JSON for tests that want to introspect without dialect specifics.
__all_json__ = (JSON, JSONB)


# ---------------------------------------------------------------------------
# Facebook / publishing models
# ---------------------------------------------------------------------------

FACEBOOK_ACCOUNT_STATUS_ENUM = SAEnum(
    FacebookAccountStatus,
    name="facebook_account_status",
    native_enum=True,
    validate_strings=True,
    values_callable=lambda E: [e.value for e in E],
)
FACEBOOK_PAGE_STATUS_ENUM = SAEnum(
    FacebookPageStatus,
    name="facebook_page_status",
    native_enum=True,
    validate_strings=True,
    values_callable=lambda E: [e.value for e in E],
)
CONTENT_SOURCE_STATUS_ENUM = SAEnum(
    ContentSourceStatus,
    name="content_source_status",
    native_enum=True,
    validate_strings=True,
    values_callable=lambda E: [e.value for e in E],
)
APPROVAL_STATUS_ENUM = SAEnum(
    ApprovalStatus,
    name="approval_status",
    native_enum=True,
    validate_strings=True,
    values_callable=lambda E: [e.value for e in E],
)
PUBLISH_STATUS_ENUM = SAEnum(
    PublishStatus,
    name="publish_status",
    native_enum=True,
    validate_strings=True,
    values_callable=lambda E: [e.value for e in E],
)
PUBLISH_JOB_STATUS_ENUM = SAEnum(
    PublishJobStatus,
    name="publish_job_status",
    native_enum=True,
    validate_strings=True,
    values_callable=lambda E: [e.value for e in E],
)


class FacebookAccount(Base):
    __tablename__ = "facebook_accounts"

    id: Mapped[UUID] = _uuid_pk()
    provider_user_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[FacebookAccountStatus] = mapped_column(
        FACEBOOK_ACCOUNT_STATUS_ENUM,
        nullable=False,
        default=FacebookAccountStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    pages: Mapped[list[FacebookPage]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class FacebookPage(Base):
    __tablename__ = "facebook_pages"
    __table_args__ = (
        Index("ix_facebook_pages_account_id", "account_id"),
        Index("ix_facebook_pages_status", "status"),
        Index("ix_facebook_pages_auto_generate_enabled", "auto_generate_enabled"),
    )

    id: Mapped[UUID] = _uuid_pk()
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("facebook_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    page_name: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_url: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_page_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    permissions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    niche: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language: Mapped[str | None] = mapped_column(String(2), nullable=True)
    content_keywords: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    blocked_keywords: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    daily_reel_target: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    daily_image_post_target: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    posting_time_slots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    auto_generate_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    require_manual_approval: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    status: Mapped[FacebookPageStatus] = mapped_column(
        FACEBOOK_PAGE_STATUS_ENUM,
        nullable=False,
        default=FacebookPageStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    account: Mapped[FacebookAccount] = relationship(back_populates="pages")
    content_sources: Mapped[list[ContentSource]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )
    reel_drafts: Mapped[list[ReelDraft]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )
    publish_jobs: Mapped[list[PublishJob]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )


class ContentSource(Base):
    __tablename__ = "content_sources"
    __table_args__ = (
        Index("ix_content_sources_page_id_status", "page_id", "status"),
        # UNIQUE constraint on (page_id, source_url)
        Index("uq_content_sources_page_id_source_url", "page_id", "source_url", unique=True),
    )

    id: Mapped[UUID] = _uuid_pk()
    page_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("facebook_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(
        String(64), nullable=False, default="youtube", server_default="'youtube'"
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_topic: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[ContentSourceStatus] = mapped_column(
        CONTENT_SOURCE_STATUS_ENUM,
        nullable=False,
        default=ContentSourceStatus.DISCOVERED,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = _ts_created()

    page: Mapped[FacebookPage] = relationship(back_populates="content_sources")
    reel_drafts: Mapped[list[ReelDraft]] = relationship(
        back_populates="content_source", cascade="all, delete-orphan"
    )


class ReelDraft(Base):
    __tablename__ = "reel_drafts"
    __table_args__ = (
        Index("ix_reel_drafts_page_id_approval_status", "page_id", "approval_status"),
        Index("ix_reel_drafts_publish_status_scheduled_at", "publish_status", "scheduled_at"),
    )

    id: Mapped[UUID] = _uuid_pk()
    page_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("facebook_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    clip_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("clips.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    content_source_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("content_sources.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    suggested_post_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        APPROVAL_STATUS_ENUM,
        nullable=False,
        default=ApprovalStatus.PENDING,
    )
    publish_status: Mapped[PublishStatus] = mapped_column(
        PUBLISH_STATUS_ENUM,
        nullable=False,
        default=PublishStatus.DRAFT,
    )
    facebook_video_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    facebook_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts_created()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    page: Mapped[FacebookPage] = relationship(back_populates="reel_drafts")
    content_source: Mapped[ContentSource | None] = relationship(back_populates="reel_drafts")
    publish_jobs: Mapped[list[PublishJob]] = relationship(
        back_populates="reel_draft", cascade="all, delete-orphan"
    )


class PublishJob(Base):
    __tablename__ = "publish_jobs"
    __table_args__ = (
        Index("ix_publish_jobs_status_scheduled_at", "status", "scheduled_at"),
    )

    id: Mapped[UUID] = _uuid_pk()
    reel_draft_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("reel_drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("facebook_pages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[PublishJobStatus] = mapped_column(
        PUBLISH_JOB_STATUS_ENUM,
        nullable=False,
        default=PublishJobStatus.QUEUED,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts_created()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reel_draft: Mapped[ReelDraft] = relationship(back_populates="publish_jobs")
    page: Mapped[FacebookPage] = relationship(back_populates="publish_jobs")


class ImagePost(Base):
    __tablename__ = "image_posts"
    __table_args__ = (
        Index("ix_image_posts_page_id_approval_status", "page_id", "approval_status"),
        Index("ix_image_posts_publish_status_scheduled_at", "publish_status", "scheduled_at"),
        Index("ix_image_posts_created_at", "created_at"),
    )

    id: Mapped[UUID] = _uuid_pk()
    page_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("facebook_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    image_paths: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    image_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    aspect_ratio: Mapped[str] = mapped_column(
        String(16), nullable=False, default="16:9", server_default="'16:9'"
    )
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        APPROVAL_STATUS_ENUM,
        nullable=False,
        default=ApprovalStatus.PENDING,
    )
    publish_status: Mapped[PublishStatus] = mapped_column(
        PUBLISH_STATUS_ENUM,
        nullable=False,
        default=PublishStatus.DRAFT,
    )
    facebook_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _ts_created()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    page: Mapped[FacebookPage] = relationship("FacebookPage")


# ---------------------------------------------------------------------------
# Scripts model
# ---------------------------------------------------------------------------

SCRIPT_STATUS_ENUM = SAEnum(
    "unfilmed",
    "filmed",
    "published",
    name="script_status",
    native_enum=True,
    validate_strings=True,
)


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        SCRIPT_STATUS_ENUM, nullable=False, default="unfilmed", server_default="unfilmed"
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reel_draft_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("reel_drafts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()
