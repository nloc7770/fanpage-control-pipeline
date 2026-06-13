"""scripts table

Revision ID: 0004_scripts
Revises: 0003_image_posts
Create Date: 2026-06-08 00:00:00.000000

Creates the scripts table for tracking video scripts in the filming pipeline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_scripts"
down_revision: str | None = "0003_image_posts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCRIPT_STATUS_VALUES = ("unfilmed", "filmed", "published")


def upgrade() -> None:
    script_status = postgresql.ENUM(
        *SCRIPT_STATUS_VALUES,
        name="script_status",
        create_type=False,
    )
    script_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "scripts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "slug",
            sa.String(length=64),
            nullable=False,
            unique=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "status",
            script_status,
            nullable=False,
            server_default=sa.text("'unfilmed'::script_status"),
        ),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "reel_draft_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
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
            ["reel_draft_id"], ["reel_drafts.id"], ondelete="SET NULL"
        ),
    )

    op.create_index("ix_scripts_status", "scripts", ["status"])
    op.create_index("ix_scripts_slug", "scripts", ["slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_scripts_slug", table_name="scripts")
    op.drop_index("ix_scripts_status", table_name="scripts")
    op.drop_table("scripts")
    op.execute("DROP TYPE IF EXISTS script_status")
