"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-21 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JOB_STATUS_VALUES = (
    "queued",
    "downloading",
    "transcribing",
    "analyzing",
    "clip_planning",
    "rendering",
    "completed",
    "failed",
)

CLIP_STAGE_VALUES = ("planned", "rendering", "rendered", "failed")

ASSET_KIND_VALUES = (
    "source_video",
    "source_audio",
    "source_thumbnail",
    "transcript_json",
    "diarization_json",
    "yolo_json",
    "analysis_json",
    "edit_plan_json",
    "clip_video",
    "clip_thumbnail",
    "subtitle_ass",
)


def upgrade() -> None:
    # Make sure gen_random_uuid() is available.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    job_status = postgresql.ENUM(*JOB_STATUS_VALUES, name="job_status", create_type=False)
    clip_stage = postgresql.ENUM(*CLIP_STAGE_VALUES, name="clip_stage", create_type=False)
    asset_kind = postgresql.ENUM(*ASSET_KIND_VALUES, name="asset_kind", create_type=False)

    job_status.create(op.get_bind(), checkfirst=True)
    clip_stage.create(op.get_bind(), checkfirst=True)
    asset_kind.create(op.get_bind(), checkfirst=True)

    # ---- jobs ------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column(
            "status",
            job_status,
            nullable=False,
            server_default=sa.text("'queued'::job_status"),
        ),
        sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("current_stage", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])

    # ---- assets ----------------------------------------------------------
    op.create_table(
        "assets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", asset_kind, nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("mime", sa.String(length=128), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_assets_job_id", "assets", ["job_id"])
    op.create_index("ix_assets_kind", "assets", ["kind"])

    # ---- transcripts -----------------------------------------------------
    op.create_table(
        "transcripts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column(
            "segments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "words",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_transcripts_job_id_unique", "transcripts", ["job_id"], unique=True)

    # ---- speakers --------------------------------------------------------
    op.create_table(
        "speakers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("speaker_id", sa.String(length=64), nullable=False),
        sa.Column(
            "timeline",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_speakers_job_id", "speakers", ["job_id"])

    # ---- analysis_results ------------------------------------------------
    op.create_table(
        "analysis_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "emotional_peaks",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "viral_moments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "topic_shifts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "retention_signals",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_analysis_results_job_id_unique", "analysis_results", ["job_id"], unique=True
    )

    # ---- clips -----------------------------------------------------------
    op.create_table(
        "clips",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clip_index", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Float(), nullable=False),
        sa.Column("end_time", sa.Float(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("virality_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("main_hook", sa.Text(), nullable=True),
        sa.Column("emotional_peak", sa.Text(), nullable=True),
        sa.Column("retention_reason", sa.Text(), nullable=True),
        sa.Column(
            "topics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("target_style", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("narrative_script_vi", sa.Text(), nullable=True),
        sa.Column("edit_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status",
            clip_stage,
            nullable=False,
            server_default=sa.text("'planned'::clip_stage"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_clips_job_id", "clips", ["job_id"])
    op.create_index(
        "ix_clips_job_id_clip_index", "clips", ["job_id", "clip_index"], unique=True
    )
    op.create_index("ix_clips_status", "clips", ["status"])

    # ---- render_tasks ----------------------------------------------------
    op.create_table(
        "render_tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            clip_stage,
            nullable=False,
            server_default=sa.text("'planned'::clip_stage"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("ffmpeg_command", sa.Text(), nullable=True),
        sa.Column("output_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["clip_id"], ["clips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["output_asset_id"], ["assets.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_render_tasks_clip_id", "render_tasks", ["clip_id"])
    op.create_index("ix_render_tasks_output_asset_id", "render_tasks", ["output_asset_id"])

    # ---- thumbnails ------------------------------------------------------
    op.create_table(
        "thumbnails",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("frame_timestamp", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["clip_id"], ["clips.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_thumbnails_clip_id", "thumbnails", ["clip_id"])

    # ---- logs ------------------------------------------------------------
    op.create_table(
        "logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="INFO"),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["clip_id"], ["clips.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_logs_job_id_created_at",
        "logs",
        ["job_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_logs_clip_id_created_at",
        "logs",
        ["clip_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_logs_clip_id_created_at", table_name="logs")
    op.drop_index("ix_logs_job_id_created_at", table_name="logs")
    op.drop_table("logs")

    op.drop_index("ix_thumbnails_clip_id", table_name="thumbnails")
    op.drop_table("thumbnails")

    op.drop_index("ix_render_tasks_output_asset_id", table_name="render_tasks")
    op.drop_index("ix_render_tasks_clip_id", table_name="render_tasks")
    op.drop_table("render_tasks")

    op.drop_index("ix_clips_status", table_name="clips")
    op.drop_index("ix_clips_job_id_clip_index", table_name="clips")
    op.drop_index("ix_clips_job_id", table_name="clips")
    op.drop_table("clips")

    op.drop_index("ix_analysis_results_job_id_unique", table_name="analysis_results")
    op.drop_table("analysis_results")

    op.drop_index("ix_speakers_job_id", table_name="speakers")
    op.drop_table("speakers")

    op.drop_index("ix_transcripts_job_id_unique", table_name="transcripts")
    op.drop_table("transcripts")

    op.drop_index("ix_assets_kind", table_name="assets")
    op.drop_index("ix_assets_job_id", table_name="assets")
    op.drop_table("assets")

    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_table("jobs")

    postgresql.ENUM(name="asset_kind").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="clip_stage").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="job_status").drop(op.get_bind(), checkfirst=True)
