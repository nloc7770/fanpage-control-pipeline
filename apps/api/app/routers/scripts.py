"""Scripts router: list, get content, update status, create draft from script."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import SessionDep, SettingsDep
from app.errors import NotFoundError, ValidationError
from database.models import Script, ReelDraft

router = APIRouter(prefix="/scripts", tags=["scripts"])

SCRIPTS_DIR = os.environ.get("SCRIPTS_DIR", "/data/storage/scripts")


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ScriptListItem(BaseModel):
    id: UUID
    slug: str
    title: str
    status: str
    duration_seconds: int | None = None
    file_path: str

    class Config:
        from_attributes = True


class ScriptDetail(ScriptListItem):
    content: str | None = None
    reel_draft_id: UUID | None = None


class ScriptListResponse(BaseModel):
    scripts: list[ScriptListItem]
    total: int


class PatchScriptRequest(BaseModel):
    status: str


class CreateDraftResponse(BaseModel):
    reel_draft_id: UUID
    title: str
    caption: str | None = None
    hashtags: list[str] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_script_or_404(session: AsyncSession, slug: str) -> Script:
    q = await session.execute(select(Script).where(Script.slug == slug))
    script = q.scalar_one_or_none()
    if script is None:
        raise NotFoundError(f"Script '{slug}' not found")
    return script


def _read_script_content(file_path: str) -> str | None:
    """Read the .md file content from the scripts directory."""
    # Use SCRIPTS_DIR as base, but file_path might be absolute
    full_path = Path(file_path)
    if not full_path.is_absolute():
        full_path = Path(SCRIPTS_DIR) / file_path

    try:
        return full_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Try just the filename in SCRIPTS_DIR
        slug_path = Path(SCRIPTS_DIR) / full_path.name
        try:
            return slug_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None


def _extract_caption_and_hashtags(content: str) -> tuple[str | None, list[str]]:
    """Extract caption and hashtags from markdown script content."""
    caption = None
    hashtags: list[str] = []

    lines = content.split("\n")
    for line in lines:
        stripped = line.strip()
        # Look for hashtags line
        if stripped.startswith("#") and not stripped.startswith("##"):
            # Could be a heading, skip markdown headings
            if stripped.startswith("# "):
                continue
        # Look for lines with hashtag patterns like #gym #fitness
        if " #" in stripped or stripped.startswith("#") and not stripped.startswith("##"):
            words = stripped.split()
            tags = [w.lstrip("#") for w in words if w.startswith("#") and len(w) > 1 and not w.startswith("##")]
            if tags:
                hashtags.extend(tags)

    # Use first non-heading, non-empty paragraph as caption
    in_content = False
    caption_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            if caption_lines:
                break
            in_content = True
            continue
        if in_content and stripped:
            caption_lines.append(stripped)
        elif in_content and not stripped and caption_lines:
            break

    if caption_lines:
        caption = " ".join(caption_lines[:3])  # First few lines as caption

    return caption, hashtags


# ---------------------------------------------------------------------------
# GET /scripts
# ---------------------------------------------------------------------------


@router.get("", response_model=ScriptListResponse)
async def list_scripts(
    session: SessionDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ScriptListResponse:
    """List all scripts with optional status filter."""
    stmt = select(Script)
    count_stmt = select(func.count()).select_from(Script)

    if status_filter is not None:
        stmt = stmt.where(Script.status == status_filter)
        count_stmt = count_stmt.where(Script.status == status_filter)

    stmt = stmt.order_by(Script.slug.asc()).limit(limit).offset(offset)

    rows_q = await session.execute(stmt)
    rows = rows_q.scalars().all()

    total_q = await session.execute(count_stmt)
    total = total_q.scalar_one()

    return ScriptListResponse(
        scripts=[ScriptListItem.model_validate(r) for r in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /scripts/{slug}
# ---------------------------------------------------------------------------


@router.get("/{slug}", response_model=ScriptDetail)
async def get_script(
    slug: str,
    session: SessionDep,
) -> ScriptDetail:
    """Get a single script with full .md content."""
    script = await _get_script_or_404(session, slug)
    content = _read_script_content(script.file_path)

    return ScriptDetail(
        id=script.id,
        slug=script.slug,
        title=script.title,
        status=script.status,
        duration_seconds=script.duration_seconds,
        file_path=script.file_path,
        content=content,
        reel_draft_id=script.reel_draft_id,
    )


# ---------------------------------------------------------------------------
# PATCH /scripts/{slug}
# ---------------------------------------------------------------------------


@router.patch("/{slug}", response_model=ScriptListItem)
async def patch_script(
    slug: str,
    body: PatchScriptRequest,
    session: SessionDep,
) -> ScriptListItem:
    """Update script status (unfilmed/filmed/published)."""
    valid_statuses = ("unfilmed", "filmed", "published")
    if body.status not in valid_statuses:
        raise ValidationError(
            f"Invalid status '{body.status}'. Must be one of: {', '.join(valid_statuses)}"
        )

    script = await _get_script_or_404(session, slug)
    script.status = body.status
    await session.flush()

    return ScriptListItem.model_validate(script)


# ---------------------------------------------------------------------------
# POST /scripts/{slug}/create-draft
# ---------------------------------------------------------------------------


@router.post("/{slug}/create-draft", response_model=CreateDraftResponse, status_code=status.HTTP_201_CREATED)
async def create_draft_from_script(
    slug: str,
    session: SessionDep,
) -> CreateDraftResponse:
    """Create a reel_draft pre-filled from script content."""
    script = await _get_script_or_404(session, slug)

    if script.reel_draft_id is not None:
        raise ValidationError(f"Script '{slug}' already has a linked reel draft")

    content = _read_script_content(script.file_path)
    caption = None
    hashtags: list[str] = []

    if content:
        caption, hashtags = _extract_caption_and_hashtags(content)

    # Create a new reel draft (page_id is nullable in our usage for scripts)
    # We need at least a page_id — for now we create without one since scripts
    # are personal content. We'll use a minimal ReelDraft.
    from database.models import ReelDraft
    from sqlalchemy import select as sa_select

    # Get first available page or create draft without page
    from database.models import FacebookPage
    page_q = await session.execute(sa_select(FacebookPage).limit(1))
    page = page_q.scalar_one_or_none()

    draft = ReelDraft(
        page_id=page.id if page else None,
        title=script.title,
        caption=caption,
        hashtags=hashtags,
    )
    session.add(draft)
    await session.flush()

    # Link the draft back to the script
    script.reel_draft_id = draft.id
    await session.flush()

    logger.info("create_draft_from_script: script={} draft={}", slug, draft.id)

    return CreateDraftResponse(
        reel_draft_id=draft.id,
        title=script.title,
        caption=caption,
        hashtags=hashtags,
    )
