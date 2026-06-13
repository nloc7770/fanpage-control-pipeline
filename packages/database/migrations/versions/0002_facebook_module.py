"""facebook module tables

Revision ID: 0002_facebook_module
Revises: 0001_initial
Create Date: 2026-05-23 00:00:00.000000

Creates five tables for the Facebook publishing pipeline:
  facebook_accounts, facebook_pages, content_sources, reel_drafts, publish_jobs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_facebook_module"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FACEBOOK_ACCOUNT_STATUS_VALUES = ("active", "disabled", "token_expired", "error")
FACEBOOK_PAGE_STATUS_VALUES = (
    "active",
    "disabled",
    "token_expired",
    "permission_missing",
    "error",
)
CONTENT_SOURCE_STATUS_VALUES = (
    "discovered",
    "queued",
    "processing",
    "generated",
    "rejected",
    "failed",
)
APPROVAL_STATUS_VALUES = ("pending", "approved", "rejected")
PUBLISH_STATUS_VALUES = ("draft", "scheduled", "publishing", "published", "failed")
PUBLISH_JOB_STATUS_VALUES = (
    "queued",
    "uploading",
    "processing",
    "published",
    "failed",
    "cancelled",
)


def upgrade() -> None:
    # pgcrypto already created by 0001_initial; guard anyway.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    fb_account_status = postgresql.ENUM(
        *FACEBOOK_ACCOUNT_STATUS_VALUES,
        name="facebook_account_status",
        create_type=False,
    )
    fb_page_status = postgresql.ENUM(
        *FACEBOOK_PAGE_STATUS_VALUES,
        name="facebook_page_status",
        create_type=False,
    )
    content_source_status = postgresql.ENUM(
        *CONTENT_SOURCE_STATUS_VALUES,
        name="content_source_status",
        create_type=False,
    )
    approval_status = postgresql.ENUM(
        *APPROVAL_STATUS_VALUES,
        name="approval_status",
        create_type=False,
    )
    publish_status = postgresql.ENUM(
        *PUBLISH_STATUS_VALUES,
        name="publish_status",
        create_type=False,
    )
    publish_job_status = postgresql.ENUM(
        *PUBLISH_JOB_STATUS_VALUES,
        name="publish_job_status",
        create_type=False,
    )

    fb_account_status.create(op.get_bind(), checkfirst=True)
    fb_page_status.create(op.get_bind(), checkfirst=True)
    content_source_status.create(op.get_bind(), checkfirst=True)
    approval_status.create(op.get_bind(), checkfirst=True)
    publish_status.create(op.get_bind(), checkfirst=True)
    publish_job_status.create(op.get_bind(), checkfirst=True)

    # ---- facebook_accounts -----------------------------------------------
    op.create_table(
        "facebook_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "provider_user_id",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            fb_account_status,
            nullable=False,
            server_default=sa.text("'active'::facebook_account_status"),
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
        sa.UniqueConstraint("provider_user_id", name="uq_facebook_accounts_provider_user_id"),
    )

    # ---- facebook_pages --------------------------------------------------
    op.create_table(
        "facebook_pages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", sa.String(length=128), nullable=False),
        sa.Column("page_name", sa.Text(), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=False),
        sa.Column("encrypted_page_access_token", sa.Text(), nullable=False),
        sa.Column(
            "permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("niche", sa.String(length=128), nullable=True),
        sa.Column("language", sa.String(length=2), nullable=True),
        sa.Column(
            "content_keywords",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "blocked_keywords",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "daily_reel_target",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
        sa.Column(
            "posting_time_slots",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "auto_generate_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "require_manual_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "status",
            fb_page_status,
            nullable=False,
            server_default=sa.text("'active'::facebook_page_status"),
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
        sa.ForeignKeyConstraint(
            ["account_id"], ["facebook_accounts.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("page_id", name="uq_facebook_pages_page_id"),
    )
    op.create_index("ix_facebook_pages_account_id", "facebook_pages", ["account_id"])
    op.create_index("ix_facebook_pages_status", "facebook_pages", ["status"])
    op.create_index(
        "ix_facebook_pages_auto_generate_enabled",
        "facebook_pages",
        ["auto_generate_enabled"],
    )

    # ---- content_sources -------------------------------------------------
    op.create_table(
        "content_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "platform",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'youtube'"),
        ),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_title", sa.Text(), nullable=True),
        sa.Column("channel_name", sa.Text(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("detected_topic", sa.String(length=256), nullable=True),
        sa.Column(
            "status",
            content_source_status,
            nullable=False,
            server_default=sa.text("'discovered'::content_source_status"),
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["page_id"], ["facebook_pages.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_content_sources_page_id_status",
        "content_sources",
        ["page_id", "status"],
    )
    op.create_index(
        "uq_content_sources_page_id_source_url",
        "content_sources",
        ["page_id", "source_url"],
        unique=True,
    )

    # ---- reel_drafts -----------------------------------------------------
    op.create_table(
        "reel_drafts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content_source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column(
            "hashtags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("suggested_post_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approval_status",
            approval_status,
            nullable=False,
            server_default=sa.text("'pending'::approval_status"),
        ),
        sa.Column(
            "publish_status",
            publish_status,
            nullable=False,
            server_default=sa.text("'draft'::publish_status"),
        ),
        sa.Column("facebook_video_id", sa.String(length=128), nullable=True),
        sa.Column("facebook_post_id", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["page_id"], ["facebook_pages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["clip_id"], ["clips.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["content_source_id"], ["content_sources.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_reel_drafts_page_id_approval_status",
        "reel_drafts",
        ["page_id", "approval_status"],
    )
    op.create_index(
        "ix_reel_drafts_publish_status_scheduled_at",
        "reel_drafts",
        ["publish_status", "scheduled_at"],
    )
    op.create_index("ix_reel_drafts_clip_id", "reel_drafts", ["clip_id"])
    op.create_index(
        "ix_reel_drafts_content_source_id", "reel_drafts", ["content_source_id"]
    )

    # ---- publish_jobs ----------------------------------------------------
    op.create_table(
        "publish_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("reel_draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            publish_job_status,
            nullable=False,
            server_default=sa.text("'queued'::publish_job_status"),
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retry_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["reel_draft_id"], ["reel_drafts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["page_id"], ["facebook_pages.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_publish_jobs_status_scheduled_at",
        "publish_jobs",
        ["status", "scheduled_at"],
    )
    op.create_index("ix_publish_jobs_reel_draft_id", "publish_jobs", ["reel_draft_id"])
    op.create_index("ix_publish_jobs_page_id", "publish_jobs", ["page_id"])


def downgrade() -> None:
    op.drop_index("ix_publish_jobs_page_id", table_name="publish_jobs")
    op.drop_index("ix_publish_jobs_reel_draft_id", table_name="publish_jobs")
    op.drop_index("ix_publish_jobs_status_scheduled_at", table_name="publish_jobs")
    op.drop_table("publish_jobs")

    op.drop_index("ix_reel_drafts_content_source_id", table_name="reel_drafts")
    op.drop_index("ix_reel_drafts_clip_id", table_name="reel_drafts")
    op.drop_index(
        "ix_reel_drafts_publish_status_scheduled_at", table_name="reel_drafts"
    )
    op.drop_index(
        "ix_reel_drafts_page_id_approval_status", table_name="reel_drafts"
    )
    op.drop_table("reel_drafts")

    op.drop_index(
        "uq_content_sources_page_id_source_url", table_name="content_sources"
    )
    op.drop_index("ix_content_sources_page_id_status", table_name="content_sources")
    op.drop_table("content_sources")

    op.drop_index(
        "ix_facebook_pages_auto_generate_enabled", table_name="facebook_pages"
    )
    op.drop_index("ix_facebook_pages_status", table_name="facebook_pages")
    op.drop_index("ix_facebook_pages_account_id", table_name="facebook_pages")
    op.drop_table("facebook_pages")

    op.drop_table("facebook_accounts")

    postgresql.ENUM(name="publish_job_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="publish_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="approval_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="content_source_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="facebook_page_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="facebook_account_status").drop(op.get_bind(), checkfirst=True)
