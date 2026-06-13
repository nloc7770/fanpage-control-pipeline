"""Dashboard router: aggregate stats for the content pipeline."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import SessionDep
from database.models import Script, ReelDraft

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class UpcomingPost(BaseModel):
    id: UUID
    title: str | None = None
    scheduled_at: datetime | None = None


class DashboardStats(BaseModel):
    total_scripts: int
    scripts_filmed: int
    scripts_published: int
    total_reels_published: int
    total_reels_scheduled: int
    upcoming_posts: list[UpcomingPost]


# ---------------------------------------------------------------------------
# GET /dashboard/stats
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    session: SessionDep,
) -> DashboardStats:
    """Aggregate stats across scripts and reel drafts."""

    # Script counts
    total_scripts_q = await session.execute(
        select(func.count()).select_from(Script)
    )
    total_scripts = total_scripts_q.scalar_one()

    scripts_filmed_q = await session.execute(
        select(func.count()).select_from(Script).where(Script.status == "filmed")
    )
    scripts_filmed = scripts_filmed_q.scalar_one()

    scripts_published_q = await session.execute(
        select(func.count()).select_from(Script).where(Script.status == "published")
    )
    scripts_published = scripts_published_q.scalar_one()

    # Reel draft counts
    total_reels_published_q = await session.execute(
        select(func.count()).select_from(ReelDraft).where(
            ReelDraft.publish_status == "published"
        )
    )
    total_reels_published = total_reels_published_q.scalar_one()

    total_reels_scheduled_q = await session.execute(
        select(func.count()).select_from(ReelDraft).where(
            ReelDraft.publish_status == "scheduled"
        )
    )
    total_reels_scheduled = total_reels_scheduled_q.scalar_one()

    # Upcoming scheduled posts (next 5)
    upcoming_q = await session.execute(
        select(ReelDraft)
        .where(ReelDraft.publish_status == "scheduled")
        .where(ReelDraft.scheduled_at.isnot(None))
        .order_by(ReelDraft.scheduled_at.asc())
        .limit(5)
    )
    upcoming_rows = upcoming_q.scalars().all()

    upcoming_posts = [
        UpcomingPost(
            id=r.id,
            title=r.title,
            scheduled_at=r.scheduled_at,
        )
        for r in upcoming_rows
    ]

    return DashboardStats(
        total_scripts=total_scripts,
        scripts_filmed=scripts_filmed,
        scripts_published=scripts_published,
        total_reels_published=total_reels_published,
        total_reels_scheduled=total_reels_scheduled,
        upcoming_posts=upcoming_posts,
    )
