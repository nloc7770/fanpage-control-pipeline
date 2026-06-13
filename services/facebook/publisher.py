"""Facebook Reels publisher using the resumable upload API.

Public API::

    facebook_video_id = await publish_reel(page_id_db, reel_draft_id)

Flow:
  1. Load FacebookPage (decrypt token) + ReelDraft + clip video asset path.
  2. POST /{page_id}/video_reels?upload_phase=start  → upload_url + video_id
  3. POST upload_url with raw video bytes             → upload confirmed
  4. POST /{page_id}/video_reels?upload_phase=finish  → published

On any error the reel_draft.publish_status is set to ``failed`` and the
exception is re-raised so the Celery task can handle retries / SSE events.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Asset, Clip, FacebookPage, ReelDraft
from shared_py.crypto import decrypt_token, mask_token
from shared_py.enums import AssetKind, FacebookPageStatus, PublishStatus
from services.facebook.graph_client import FacebookAPIError, GraphClient


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://factory:factory@postgres:5432/factory"
    )


async def _session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        _database_url(),
        future=True,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def publish_reel(page_id_db: UUID, reel_draft_id: UUID) -> str:
    """Publish a reel draft to Facebook. Returns the facebook_video_id.

    Raises on any error after updating reel_draft.publish_status = failed.
    """
    factory = await _session_factory()
    async with factory() as session:
        # --- Load entities ---------------------------------------------------
        page_result = await session.execute(
            select(FacebookPage).where(FacebookPage.id == page_id_db)
        )
        page: FacebookPage | None = page_result.scalar_one_or_none()
        if page is None:
            raise ValueError(f"FacebookPage {page_id_db} not found")

        draft_result = await session.execute(
            select(ReelDraft).where(ReelDraft.id == reel_draft_id)
        )
        draft: ReelDraft | None = draft_result.scalar_one_or_none()
        if draft is None:
            raise ValueError(f"ReelDraft {reel_draft_id} not found")

        # --- Resolve video file path -----------------------------------------
        video_path = await _resolve_video_path(session, draft)
        if video_path is None:
            err = "No clip_video asset found for reel draft"
            await _mark_failed(session, draft, err)
            raise ValueError(err)

        # --- Decrypt page token ----------------------------------------------
        try:
            page_token = decrypt_token(page.encrypted_page_access_token)
        except Exception as exc:
            err = f"Failed to decrypt page token: {exc}"
            await _mark_failed(session, draft, err)
            raise RuntimeError(err) from exc

        logger.info(
            "publish_reel: draft={} page={} token={} video={}",
            reel_draft_id,
            page.page_id,
            mask_token(page_token),
            video_path,
        )

        # --- Build description -----------------------------------------------
        caption = draft.caption or ""
        hashtags = " ".join(f"#{h.lstrip('#')}" for h in (draft.hashtags or []))
        description = f"{caption}\n{hashtags}".strip()

        # --- Execute upload flow ---------------------------------------------
        try:
            facebook_video_id = await _upload_reel(
                page_id=page.page_id,
                page_token=page_token,
                video_path=Path(video_path),
                description=description,
            )
        except FacebookAPIError as exc:
            logger.error(
                "publish_reel: FacebookAPIError draft={} code={} msg={}",
                reel_draft_id,
                exc.code,
                exc.message,
            )
            if exc.is_token_expired():
                page.status = FacebookPageStatus.TOKEN_EXPIRED
            elif exc.is_permission_missing():
                page.status = FacebookPageStatus.PERMISSION_MISSING
            await _mark_failed(session, draft, str(exc.message))
            await session.commit()
            raise
        except Exception as exc:
            logger.exception("publish_reel: unexpected error draft={}", reel_draft_id)
            await _mark_failed(session, draft, str(exc))
            await session.commit()
            raise

        # --- Persist success -------------------------------------------------
        draft.facebook_video_id = facebook_video_id
        draft.facebook_post_id = facebook_video_id  # Graph API returns same id
        draft.publish_status = PublishStatus.PUBLISHED
        draft.published_at = datetime.now(tz=timezone.utc)
        draft.error_message = None
        await session.commit()

        logger.info(
            "publish_reel: SUCCESS draft={} facebook_video_id={}",
            reel_draft_id,
            facebook_video_id,
        )
        return facebook_video_id


async def _resolve_video_path(
    session: AsyncSession, draft: ReelDraft
) -> str | None:
    """Find the clip_video asset path for this draft."""
    if draft.clip_id is None:
        return None

    # Look for a CLIP_VIDEO asset whose job matches via the Clip row
    clip_result = await session.execute(
        select(Clip).where(Clip.id == draft.clip_id)
    )
    clip = clip_result.scalar_one_or_none()
    if clip is None:
        return None

    asset_result = await session.execute(
        select(Asset).where(
            Asset.job_id == clip.job_id,
            Asset.kind == AssetKind.CLIP_VIDEO,
        )
    )
    # There may be multiple clip videos; pick the one for this clip via metadata
    assets = asset_result.scalars().all()
    for asset in assets:
        meta = asset.asset_metadata or {}
        if str(meta.get("clip_id", "")) == str(draft.clip_id):
            return asset.path
    # Fallback: return first clip video if only one exists
    if len(assets) == 1:
        return assets[0].path
    return None


async def _upload_reel(
    *,
    page_id: str,
    page_token: str,
    video_path: Path,
    description: str,
) -> str:
    """Execute the three-phase resumable upload and return the video_id."""
    async with GraphClient(page_token) as client:
        # Phase 1: start
        start_resp = await client.post(
            f"/{page_id}/video_reels",
            params={"upload_phase": "start"},
        )
        upload_url: str = start_resp["upload_url"]
        video_id: str = str(start_resp["video_id"])
        logger.debug("upload_reel: phase=start video_id={}", video_id)

        # Phase 2: upload bytes
        video_bytes = video_path.read_bytes()
        file_size = len(video_bytes)
        upload_headers = {
            "Authorization": f"OAuth {page_token}",
            "Content-Type": "application/octet-stream",
            "offset": "0",
            "file_size": str(file_size),
        }
        await client.post_raw(
            upload_url,
            content=video_bytes,
            headers=upload_headers,
        )
        logger.debug(
            "upload_reel: phase=upload video_id={} bytes={}", video_id, file_size
        )

        # Phase 3: finish / publish
        finish_resp = await client.post(
            f"/{page_id}/video_reels",
            params={
                "upload_phase": "finish",
                "video_id": video_id,
                "video_state": "PUBLISHED",
                "description": description,
            },
        )
        logger.debug("upload_reel: phase=finish resp={}", finish_resp)

    return video_id


async def _mark_failed(
    session: AsyncSession, draft: ReelDraft, error: str
) -> None:
    draft.publish_status = PublishStatus.FAILED
    draft.error_message = error[:2000]
