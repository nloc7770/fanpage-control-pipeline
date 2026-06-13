"""image_posts table

Revision ID: 0003_image_posts
Revises: 0002_facebook_module
Create Date: 2026-05-23 00:00:00.000000

Creates the image_posts table for the image generation + publishing pipeline.
Reuses the approval_status and publish_status postgres ENUMs created in 0002.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_image_posts"
down_revision: str | None = "0002_facebook_module"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPROVAL_STATUS_VALUES = ("pending", "approved", "rejected")
PUBLISH_STATUS_VALUES = ("draft", "scheduled", "publishing", "published", "failed")


def upgrade() -> None:
    # Reuse ENUMs already created in 0002 — create_type=False, checkfirst=True.
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
    # These types already exist; calling create with checkfirst=True is a no-op.
    approval_status.create(op.get_bind(), checkfirst=True)
    publish_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "image_posts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_topic", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column(
            "hashtags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "image_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "image_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "aspect_ratio",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'16:9'"),
        ),
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
        sa.Column("facebook_post_id", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "generation_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
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
    )

    op.create_index(
        "ix_image_posts_page_id_approval_status",
        "image_posts",
        ["page_id", "approval_status"],
    )
    op.create_index(
        "ix_image_posts_publish_status_scheduled_at",
        "image_posts",
        ["publish_status", "scheduled_at"],
    )
    op.create_index(
        "ix_image_posts_created_at",
        "image_posts",
        ["created_at"],
        postgresql_ops={"created_at": "DESC"},
    )


def downgrade() -> None:
    op.drop_index("ix_image_posts_created_at", table_name="image_posts")
    op.drop_index(
        "ix_image_posts_publish_status_scheduled_at", table_name="image_posts"
    )
    op.drop_index(
        "ix_image_posts_page_id_approval_status", table_name="image_posts"
    )
    op.drop_table("image_posts")
    # Do NOT drop approval_status / publish_status ENUMs — they are owned by 0002.
