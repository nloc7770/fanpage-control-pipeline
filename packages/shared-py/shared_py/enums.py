"""Canonical enums used across the API, workers and database."""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    """Lifecycle of a job, in pipeline order."""

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    CLIP_PLANNING = "clip_planning"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"


class ClipStage(StrEnum):
    """Per-clip render lifecycle."""

    PLANNED = "planned"
    RENDERING = "rendering"
    RENDERED = "rendered"
    FAILED = "failed"


class WorkerType(StrEnum):
    """Logical worker types. Maps 1:1 to Celery queue names."""

    DOWNLOAD = "download"
    WHISPERX = "whisperx"
    DIARIZATION = "diarization"
    YOLO = "yolo"
    QWEN = "qwen"
    RENDER_PREP = "render-prep"
    RENDER = "render"


class AssetKind(StrEnum):
    """Kinds of files tracked in the `assets` table."""

    SOURCE_VIDEO = "source_video"
    SOURCE_AUDIO = "source_audio"
    SOURCE_THUMBNAIL = "source_thumbnail"
    TRANSCRIPT_JSON = "transcript_json"
    DIARIZATION_JSON = "diarization_json"
    YOLO_JSON = "yolo_json"
    ANALYSIS_JSON = "analysis_json"
    EDIT_PLAN_JSON = "edit_plan_json"
    CLIP_VIDEO = "clip_video"
    CLIP_THUMBNAIL = "clip_thumbnail"
    SUBTITLE_ASS = "subtitle_ass"


# ---------------------------------------------------------------------------
# Facebook / publishing enums
# ---------------------------------------------------------------------------


class FacebookAccountStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    TOKEN_EXPIRED = "token_expired"
    ERROR = "error"


class FacebookPageStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    TOKEN_EXPIRED = "token_expired"
    PERMISSION_MISSING = "permission_missing"
    ERROR = "error"


class ContentSourceStatus(StrEnum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    PROCESSING = "processing"
    GENERATED = "generated"
    REJECTED = "rejected"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PublishStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class PublishJobStatus(StrEnum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Scripts enums
# ---------------------------------------------------------------------------


class ScriptStatus(StrEnum):
    """Status of a script in the filming pipeline."""

    UNFILMED = "unfilmed"
    FILMED = "filmed"
    PUBLISHED = "published"
