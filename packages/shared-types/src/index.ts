// Shared types between frontend and API.
// Mirrors packages/shared-py/shared_py/{enums,schemas,events,llm_contracts}.py.
// Keep these two in sync.

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

export const JobStatus = {
  Queued: "queued",
  Downloading: "downloading",
  Transcribing: "transcribing",
  Analyzing: "analyzing",
  ClipPlanning: "clip_planning",
  Rendering: "rendering",
  Completed: "completed",
  Failed: "failed",
} as const;
export type JobStatus = (typeof JobStatus)[keyof typeof JobStatus];

export const ClipStage = {
  Planned: "planned",
  Rendering: "rendering",
  Rendered: "rendered",
  Failed: "failed",
} as const;
export type ClipStage = (typeof ClipStage)[keyof typeof ClipStage];

export const WorkerType = {
  Download: "download",
  WhisperX: "whisperx",
  Diarization: "diarization",
  Yolo: "yolo",
  Qwen: "qwen",
  RenderPrep: "render-prep",
  Render: "render",
} as const;
export type WorkerType = (typeof WorkerType)[keyof typeof WorkerType];

export const AssetKind = {
  SourceVideo: "source_video",
  SourceAudio: "source_audio",
  SourceThumbnail: "source_thumbnail",
  TranscriptJson: "transcript_json",
  DiarizationJson: "diarization_json",
  YoloJson: "yolo_json",
  AnalysisJson: "analysis_json",
  EditPlanJson: "edit_plan_json",
  ClipVideo: "clip_video",
  ClipThumbnail: "clip_thumbnail",
  SubtitleAss: "subtitle_ass",
} as const;
export type AssetKind = (typeof AssetKind)[keyof typeof AssetKind];

// ---------------------------------------------------------------------------
// DTOs
// ---------------------------------------------------------------------------

export interface SourceMetadata {
  title?: string;
  duration_s?: number;
  thumbnail_url?: string;
  uploader?: string;
  upload_date?: string;
  view_count?: number;
}

export interface JobDTO {
  id: string;
  source_url: string;
  status: JobStatus;
  progress_pct: number;
  current_stage: string | null;
  error_message: string | null;
  source_metadata: SourceMetadata | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface AssetDTO {
  id: string;
  job_id: string;
  kind: AssetKind;
  path: string;
  size_bytes: number | null;
  mime: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface EditingStyle {
  aggressive_pacing: boolean;
  dynamic_subtitles: boolean;
  fast_zoom_cuts: boolean;
  visual_overlays: boolean;
  pattern_interrupts: boolean;
  cinematic_sound_design: boolean;
}

export interface SubtitleStyle {
  font?: string;
  size?: number;
  primary_color?: string;
  outline_color?: string;
  outline_width?: number;
  position?: "top" | "middle" | "bottom";
  emphasis_color?: string;
  word_highlight?: boolean;
}

export interface VisualEffect {
  type: string;
  start: number;
  end: number;
  params?: Record<string, unknown>;
}

export interface PatternInterrupt {
  at: number;
  kind: string;
  params?: Record<string, unknown>;
}

export interface CropPlan {
  mode: "track_face" | "center" | "smart" | "static";
  keyframes?: Array<{ t: number; x: number; y: number; w: number; h: number }>;
}

export interface EditPlan {
  clip_index: number;
  title: string;
  hook: string;
  summary: string;
  viral_angle: string;
  editing_style: EditingStyle;
  narrative_script_vi: string;
  visual_effects: VisualEffect[];
  subtitle_style: SubtitleStyle;
  pattern_interrupts: PatternInterrupt[];
  crop_plan: CropPlan;
}

export interface ClipDetectionItem {
  clip_index: number;
  start_time: number;
  end_time: number;
  duration: number;
  virality_score: number;
  main_hook: string;
  emotional_peak: string;
  retention_reason: string;
  topics: string[];
  target_style: string;
}

export interface ClipDetectionResponse {
  clips: ClipDetectionItem[];
}

export interface ClipDTO {
  id: string;
  job_id: string;
  clip_index: number;
  start_time: number;
  end_time: number;
  duration: number;
  virality_score: number;
  main_hook: string;
  emotional_peak: string;
  retention_reason: string;
  topics: string[];
  target_style: string;
  title: string;
  narrative_script_vi: string;
  edit_plan: EditPlan | null;
  status: ClipStage;
  video_asset_id: string | null;
  thumbnail_asset_id: string | null;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// SSE event taxonomy
// ---------------------------------------------------------------------------

export const SSEEventType = {
  JobCreated: "job.created",
  JobProgress: "job.progress",
  JobStageChanged: "job.stage_changed",
  JobFailed: "job.failed",
  JobCompleted: "job.completed",
  ClipPlanned: "clip.planned",
  ClipRendering: "clip.rendering",
  ClipRendered: "clip.rendered",
  ClipFailed: "clip.failed",
  WorkerHeartbeat: "worker.heartbeat",
  // Facebook / publishing pipeline
  ContentDiscovered: "content.discovered",
  ContentQueued: "content.queued",
  ReelGenerated: "reel.generated",
  ReelPendingReview: "reel.pending_review",
  ReelApproved: "reel.approved",
  ReelRejected: "reel.rejected",
  ReelScheduled: "reel.scheduled",
  ReelPublishing: "reel.publishing",
  ReelPublished: "reel.published",
  ReelFailed: "reel.failed",
  // Image posts pipeline
  ImagePostGenerating: "image_post.generating",
  ImagePostGenerated: "image_post.generated",
  ImagePostPendingReview: "image_post.pending_review",
  ImagePostApproved: "image_post.approved",
  ImagePostScheduled: "image_post.scheduled",
  ImagePostPublishing: "image_post.publishing",
  ImagePostPublished: "image_post.published",
  ImagePostFailed: "image_post.failed",
} as const;
export type SSEEventType = (typeof SSEEventType)[keyof typeof SSEEventType];

export interface BaseSSEEvent<T extends SSEEventType, P> {
  type: T;
  job_id: string;
  ts: string;
  payload: P;
}

export type JobCreatedEvent = BaseSSEEvent<
  typeof SSEEventType.JobCreated,
  { source_url: string }
>;

export type JobProgressEvent = BaseSSEEvent<
  typeof SSEEventType.JobProgress,
  { stage: string; pct: number; message?: string }
>;

export type JobStageChangedEvent = BaseSSEEvent<
  typeof SSEEventType.JobStageChanged,
  { from: string | null; to: string }
>;

export type JobFailedEvent = BaseSSEEvent<
  typeof SSEEventType.JobFailed,
  { stage: string; error: string }
>;

export type JobCompletedEvent = BaseSSEEvent<
  typeof SSEEventType.JobCompleted,
  { clip_count: number; duration_s: number }
>;

export type ClipPlannedEvent = BaseSSEEvent<
  typeof SSEEventType.ClipPlanned,
  { clip_id: string; clip_index: number; title: string; virality_score: number }
>;

export type ClipRenderingEvent = BaseSSEEvent<
  typeof SSEEventType.ClipRendering,
  { clip_id: string; clip_index: number; pct: number }
>;

export type ClipRenderedEvent = BaseSSEEvent<
  typeof SSEEventType.ClipRendered,
  { clip_id: string; clip_index: number; asset_id: string }
>;

export type ClipFailedEvent = BaseSSEEvent<
  typeof SSEEventType.ClipFailed,
  { clip_id: string; clip_index: number; error: string }
>;

export type WorkerHeartbeatEvent = BaseSSEEvent<
  typeof SSEEventType.WorkerHeartbeat,
  { worker_id: string; worker_type: WorkerType; task: string | null }
>;

// ---------------------------------------------------------------------------
// REST request shapes
// ---------------------------------------------------------------------------

export interface CreateJobRequest {
  source_url: string;
  options?: {
    enable_diarization?: boolean;
    target_clip_count?: number;
    language_hint?: string;
  };
}

export interface ListJobsResponse {
  jobs: JobDTO[];
  total: number;
}

export interface ListClipsResponse {
  clips: ClipDTO[];
}

// ---------------------------------------------------------------------------
// Facebook / publishing enums
// ---------------------------------------------------------------------------

export const FacebookAccountStatus = {
  Active: "active",
  Disabled: "disabled",
  TokenExpired: "token_expired",
  Error: "error",
} as const;
export type FacebookAccountStatus =
  (typeof FacebookAccountStatus)[keyof typeof FacebookAccountStatus];

export const FacebookPageStatus = {
  Active: "active",
  Disabled: "disabled",
  TokenExpired: "token_expired",
  PermissionMissing: "permission_missing",
  Error: "error",
} as const;
export type FacebookPageStatus =
  (typeof FacebookPageStatus)[keyof typeof FacebookPageStatus];

export const ContentSourceStatus = {
  Discovered: "discovered",
  Queued: "queued",
  Processing: "processing",
  Generated: "generated",
  Rejected: "rejected",
  Failed: "failed",
} as const;
export type ContentSourceStatus =
  (typeof ContentSourceStatus)[keyof typeof ContentSourceStatus];

export const ApprovalStatus = {
  Pending: "pending",
  Approved: "approved",
  Rejected: "rejected",
} as const;
export type ApprovalStatus = (typeof ApprovalStatus)[keyof typeof ApprovalStatus];

export const PublishStatus = {
  Draft: "draft",
  Scheduled: "scheduled",
  Publishing: "publishing",
  Published: "published",
  Failed: "failed",
} as const;
export type PublishStatus = (typeof PublishStatus)[keyof typeof PublishStatus];

export const PublishJobStatus = {
  Queued: "queued",
  Uploading: "uploading",
  Processing: "processing",
  Published: "published",
  Failed: "failed",
  Cancelled: "cancelled",
} as const;
export type PublishJobStatus =
  (typeof PublishJobStatus)[keyof typeof PublishJobStatus];

// ---------------------------------------------------------------------------
// Facebook / publishing DTOs
// ---------------------------------------------------------------------------

export interface FacebookAccountDTO {
  id: string;
  provider_user_id: string;
  display_name: string;
  avatar_url: string | null;
  // encrypted_access_token intentionally omitted
  token_expires_at: string | null;
  status: FacebookAccountStatus;
  created_at: string;
  updated_at: string;
}

export interface FacebookPageDTO {
  id: string;
  account_id: string;
  page_id: string;
  page_name: string;
  avatar_url: string;
  // encrypted_page_access_token intentionally omitted
  permissions: Record<string, unknown>;
  niche: string | null;
  language: string | null;
  content_keywords: string[];
  blocked_keywords: string[];
  daily_reel_target: number;
  posting_time_slots: Record<string, unknown>[];
  auto_generate_enabled: boolean;
  require_manual_approval: boolean;
  status: FacebookPageStatus;
  created_at: string;
  updated_at: string;
}

export interface ContentSourceDTO {
  id: string;
  page_id: string;
  platform: string;
  source_url: string;
  source_title: string | null;
  channel_name: string | null;
  duration_seconds: number | null;
  thumbnail_url: string | null;
  detected_topic: string | null;
  status: ContentSourceStatus;
  rejection_reason: string | null;
  source_metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface ReelDraftDTO {
  id: string;
  page_id: string;
  clip_id: string | null;
  content_source_id: string | null;
  title: string | null;
  caption: string | null;
  hashtags: string[];
  suggested_post_time: string | null;
  approval_status: ApprovalStatus;
  publish_status: PublishStatus;
  facebook_video_id: string | null;
  facebook_post_id: string | null;
  error_message: string | null;
  created_at: string;
  approved_at: string | null;
  scheduled_at: string | null;
  published_at: string | null;
  video_asset_id: string | null;
  thumbnail_asset_id: string | null;
}

export interface ImagePostDTO {
  id: string;
  page_id: string;
  source_topic: string | null;
  caption: string | null;
  hashtags: string[];
  image_paths: string[];
  image_count: number;
  aspect_ratio: string;
  approval_status: ApprovalStatus;
  publish_status: PublishStatus;
  facebook_post_id: string | null;
  error_message: string | null;
  generation_metadata: Record<string, unknown> | null;
  created_at: string;
  approved_at: string | null;
  scheduled_at: string | null;
  published_at: string | null;
}

export interface PublishJobDTO {
  id: string;
  reel_draft_id: string;
  page_id: string;
  status: PublishJobStatus;
  scheduled_at: string | null;
  retry_count: number;
  error_message: string | null;
  created_at: string;
  published_at: string | null;
}

// ---------------------------------------------------------------------------
// Facebook SSE event types
// ---------------------------------------------------------------------------

export type ContentDiscoveredEvent = BaseSSEEvent<
  typeof SSEEventType.ContentDiscovered,
  { content_source_id: string; page_id: string; source_url: string; platform: string }
>;

export type ContentQueuedEvent = BaseSSEEvent<
  typeof SSEEventType.ContentQueued,
  { content_source_id: string; page_id: string }
>;

export type ReelGeneratedEvent = BaseSSEEvent<
  typeof SSEEventType.ReelGenerated,
  { reel_draft_id: string; page_id: string; title?: string }
>;

export type ReelPendingReviewEvent = BaseSSEEvent<
  typeof SSEEventType.ReelPendingReview,
  { reel_draft_id: string; page_id: string }
>;

export type ReelApprovedEvent = BaseSSEEvent<
  typeof SSEEventType.ReelApproved,
  { reel_draft_id: string; page_id: string; approved_by?: string }
>;

export type ReelRejectedEvent = BaseSSEEvent<
  typeof SSEEventType.ReelRejected,
  { reel_draft_id: string; page_id: string; reason?: string }
>;

export type ReelScheduledEvent = BaseSSEEvent<
  typeof SSEEventType.ReelScheduled,
  { reel_draft_id: string; page_id: string; scheduled_at: string }
>;

export type ReelPublishingEvent = BaseSSEEvent<
  typeof SSEEventType.ReelPublishing,
  { reel_draft_id: string; page_id: string; publish_job_id: string }
>;

export type ReelPublishedEvent = BaseSSEEvent<
  typeof SSEEventType.ReelPublished,
  { reel_draft_id: string; page_id: string; facebook_video_id: string; facebook_post_id?: string }
>;

export type ReelFailedEvent = BaseSSEEvent<
  typeof SSEEventType.ReelFailed,
  { reel_draft_id: string; page_id: string; error: string }
>;

// ---------------------------------------------------------------------------
// Image post SSE event types
// ---------------------------------------------------------------------------

export type ImagePostGeneratingEvent = BaseSSEEvent<
  typeof SSEEventType.ImagePostGenerating,
  { image_post_id: string; page_id: string; source_topic?: string }
>;

export type ImagePostGeneratedEvent = BaseSSEEvent<
  typeof SSEEventType.ImagePostGenerated,
  { image_post_id: string; page_id: string; image_count: number }
>;

export type ImagePostPendingReviewEvent = BaseSSEEvent<
  typeof SSEEventType.ImagePostPendingReview,
  { image_post_id: string; page_id: string }
>;

export type ImagePostApprovedEvent = BaseSSEEvent<
  typeof SSEEventType.ImagePostApproved,
  { image_post_id: string; page_id: string; approved_by?: string }
>;

export type ImagePostScheduledEvent = BaseSSEEvent<
  typeof SSEEventType.ImagePostScheduled,
  { image_post_id: string; page_id: string; scheduled_at: string }
>;

export type ImagePostPublishingEvent = BaseSSEEvent<
  typeof SSEEventType.ImagePostPublishing,
  { image_post_id: string; page_id: string }
>;

export type ImagePostPublishedEvent = BaseSSEEvent<
  typeof SSEEventType.ImagePostPublished,
  { image_post_id: string; page_id: string; facebook_post_id: string }
>;

export type ImagePostFailedEvent = BaseSSEEvent<
  typeof SSEEventType.ImagePostFailed,
  { image_post_id: string; page_id: string; error: string }
>;

export type SSEEvent =
  | JobCreatedEvent
  | JobProgressEvent
  | JobStageChangedEvent
  | JobFailedEvent
  | JobCompletedEvent
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
  | ImagePostFailedEvent;

// ---------------------------------------------------------------------------
// REST request shapes
// ---------------------------------------------------------------------------

export interface CreateJobRequest {
  source_url: string;
  options?: {
    enable_diarization?: boolean;
    target_clip_count?: number;
    language_hint?: string;
  };
}

export interface ListJobsResponse {
  jobs: JobDTO[];
  total: number;
}

export interface ListClipsResponse {
  clips: ClipDTO[];
}
