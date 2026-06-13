"""Facebook image post publisher.

Public API::

    facebook_post_id = await publish_image_post(image_post_id)

Flow (single image):
  1. Load ImagePost + FacebookPage, decrypt token.
  2. Read image bytes from image_paths[0].
  3. POST /{page_id}/photos  (multipart source + message + published=true).
  4. Persist facebook_post_id, publish_status=PUBLISHED, published_at.

Flow (multiple images):
  1-2. Same load + decrypt.
  3. For each path: POST /{page_id}/photos with published=false → photo_id list.
  4. POST /{page_id}/feed with message + attached_media=[{media_fbid: ...}, ...].
  5. Persist.

On any error: publish_status=FAILED, error_message set, exception re-raised.
SSE events emitted at start (image_post.publishing), success (image_post.published),
and failure (image_post.failed).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import FacebookPage, ImagePost
from shared_py.crypto import decrypt_token, mask_token
from shared_py.enums import FacebookPageStatus, PublishStatus
from services.facebook.graph_client import FacebookAPIError


_GRAPH_BASE = "https://graph.facebook.com"
_DEFAULT_VERSION = "v22.0"


def _graph_version() -> str:
    return os.environ.get("FACEBOOK_GRAPH_API_VERSION", _DEFAULT_VERSION)


def _resolve_image_path(path: str) -> Path:
    """Resolve an image_paths entry against STORAGE_LOCAL_PATH.

    Paths stored in image_posts.image_paths are relative to the storage root
    (e.g. 'image_posts/<page_id>/<post_id>.jpg'). Absolute paths are returned
    unchanged so manually-inserted absolute entries keep working.
    """
    if os.path.isabs(path):
        return Path(path)
    root = Path(os.environ.get("STORAGE_LOCAL_PATH", "/data/storage"))
    return root / path


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


def _build_caption(caption: str | None, hashtags: list[str]) -> str:
    """Combine caption and hashtags, skipping tags already present inline."""
    base = caption or ""
    if not hashtags:
        return base.strip()
    # Normalise: strip leading # for comparison
    existing = {h.lstrip("#").lower() for h in base.split() if h.startswith("#")}
    new_tags = [
        f"#{h.lstrip('#')}"
        for h in hashtags
        if h.lstrip("#").lower() not in existing
    ]
    if not new_tags:
        return base.strip()
    return f"{base}\n\n{' '.join(new_tags)}".strip()


def _check_graph_error(data: dict[str, Any]) -> None:
    """Raise FacebookAPIError if the response body contains an error object."""
    err = data.get("error")
    if not err:
        return
    code = int(err.get("code", 0))
    subcode = int(err.get("error_subcode", 0))
    msg = err.get("message", "Unknown Facebook API error")
    raise FacebookAPIError(msg, code=code, subcode=subcode)


async def _upload_photo(
    *,
    client: httpx.AsyncClient,
    page_id: str,
    page_token: str,
    image_bytes: bytes,
    filename: str,
    message: str | None = None,
    published: bool = True,
) -> dict[str, Any]:
    """POST a single photo to /{page_id}/photos and return the response dict."""
    version = _graph_version()
    url = f"{_GRAPH_BASE}/{version}/{page_id}/photos"
    form_data: dict[str, str] = {
        "access_token": page_token,
        "published": "true" if published else "false",
    }
    if message is not None:
        form_data["message"] = message

    resp = await client.post(
        url,
        data=form_data,
        files={"source": (filename, image_bytes, "image/jpeg")},
    )
    resp.raise_for_status()
    body: dict[str, Any] = resp.json()
    _check_graph_error(body)
    return body


async def _create_multi_photo_post(
    *,
    client: httpx.AsyncClient,
    page_id: str,
    page_token: str,
    photo_ids: list[str],
    message: str,
) -> dict[str, Any]:
    """POST to /{page_id}/feed with attached_media and return the response dict."""
    version = _graph_version()
    url = f"{_GRAPH_BASE}/{version}/{page_id}/feed"
    attached = [{"media_fbid": pid} for pid in photo_ids]
    import json as _json

    form_data: dict[str, str] = {
        "access_token": page_token,
        "message": message,
        "attached_media": _json.dumps(attached),
    }
    resp = await client.post(url, data=form_data)
    resp.raise_for_status()
    body: dict[str, Any] = resp.json()
    _check_graph_error(body)
    return body


async def publish_image_post(image_post_id: UUID) -> str:
    """Publish an ImagePost to Facebook. Returns the facebook_post_id.

    Raises on any error after updating image_post.publish_status = FAILED.
    SSE events are emitted for publishing / published / failed transitions.
    """
    # Lazy import to avoid circular deps at module load time
    from apps.workers.event_publisher import publish_sync
    from shared_py.events import (
        ImagePostFailedEvent,
        ImagePostFailedPayload,
        ImagePostPublishedEvent,
        ImagePostPublishedPayload,
        ImagePostPublishingEvent,
        ImagePostPublishingPayload,
    )

    factory = await _session_factory()
    async with factory() as session:
        # --- Load entities ---------------------------------------------------
        post_result = await session.execute(
            select(ImagePost).where(ImagePost.id == image_post_id)
        )
        image_post: ImagePost | None = post_result.scalar_one_or_none()
        if image_post is None:
            raise ValueError(f"ImagePost {image_post_id} not found")

        page_result = await session.execute(
            select(FacebookPage).where(FacebookPage.id == image_post.page_id)
        )
        page: FacebookPage | None = page_result.scalar_one_or_none()
        if page is None:
            raise ValueError(f"FacebookPage {image_post.page_id} not found")

        page_id_str = str(image_post_id)
        fb_page_id = page.page_id

        # --- Emit image_post.publishing SSE ----------------------------------
        try:
            event = ImagePostPublishingEvent(
                job_id=image_post_id,
                payload=ImagePostPublishingPayload(
                    image_post_id=image_post_id,
                    page_id=image_post.page_id,
                ),
            )
            publish_sync(page_id_str, event)
        except Exception as exc:
            logger.warning("publish_image_post: SSE publishing emit failed: {}", exc)

        # --- Validate image paths --------------------------------------------
        if not image_post.image_paths:
            err = "No image_paths on ImagePost"
            await _mark_failed(session, image_post, err)
            await session.commit()
            _emit_failed(publish_sync, image_post_id, image_post.page_id, err)
            raise ValueError(err)

        # --- Decrypt page token ----------------------------------------------
        try:
            page_token = decrypt_token(page.encrypted_page_access_token)
        except Exception as exc:
            err = f"Failed to decrypt page token: {exc}"
            await _mark_failed(session, image_post, err)
            await session.commit()
            _emit_failed(publish_sync, image_post_id, image_post.page_id, err)
            raise RuntimeError(err) from exc

        logger.info(
            "publish_image_post: id={} page={} token={} images={}",
            image_post_id,
            fb_page_id,
            mask_token(page_token),
            len(image_post.image_paths),
        )

        # --- Build caption ---------------------------------------------------
        message = _build_caption(image_post.caption, image_post.hashtags or [])

        # --- Execute upload --------------------------------------------------
        try:
            facebook_post_id = await _do_publish(
                fb_page_id=fb_page_id,
                page_token=page_token,
                image_paths=image_post.image_paths,
                image_count=image_post.image_count,
                message=message,
            )
        except FacebookAPIError as exc:
            logger.error(
                "publish_image_post: FacebookAPIError id={} code={} msg={}",
                image_post_id,
                exc.code,
                exc.message,
            )
            if exc.is_token_expired():
                page.status = FacebookPageStatus.TOKEN_EXPIRED
            elif exc.is_permission_missing():
                page.status = FacebookPageStatus.PERMISSION_MISSING
            await _mark_failed(session, image_post, str(exc.message))
            await session.commit()
            _emit_failed(publish_sync, image_post_id, image_post.page_id, str(exc.message))
            raise
        except Exception as exc:
            logger.exception(
                "publish_image_post: unexpected error id={}", image_post_id
            )
            await _mark_failed(session, image_post, str(exc))
            await session.commit()
            _emit_failed(publish_sync, image_post_id, image_post.page_id, str(exc))
            raise

        # --- Persist success -------------------------------------------------
        image_post.facebook_post_id = facebook_post_id
        image_post.publish_status = PublishStatus.PUBLISHED
        image_post.published_at = datetime.now(tz=timezone.utc)
        image_post.error_message = None
        await session.commit()

        logger.info(
            "publish_image_post: SUCCESS id={} facebook_post_id={}",
            image_post_id,
            facebook_post_id,
        )

        # --- Emit image_post.published SSE -----------------------------------
        try:
            pub_event = ImagePostPublishedEvent(
                job_id=image_post_id,
                payload=ImagePostPublishedPayload(
                    image_post_id=image_post_id,
                    page_id=image_post.page_id,
                    facebook_post_id=facebook_post_id,
                ),
            )
            publish_sync(page_id_str, pub_event)
        except Exception as exc:
            logger.warning("publish_image_post: SSE published emit failed: {}", exc)

        return facebook_post_id


async def _do_publish(
    *,
    fb_page_id: str,
    page_token: str,
    image_paths: list[str],
    image_count: int,
    message: str,
) -> str:
    """Execute the actual Graph API calls. Returns the facebook_post_id."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        if image_count <= 1:
            # Single image: direct photo post
            resolved = _resolve_image_path(image_paths[0])
            image_bytes = resolved.read_bytes()
            filename = resolved.name
            resp = await _upload_photo(
                client=client,
                page_id=fb_page_id,
                page_token=page_token,
                image_bytes=image_bytes,
                filename=filename,
                message=message,
                published=True,
            )
            # Graph API returns {"id": "...", "post_id": "..."} for photos
            post_id = resp.get("post_id") or resp.get("id")
            if not post_id:
                raise ValueError(f"No post_id in Graph API response: {resp}")
            return str(post_id)
        else:
            # Multiple images: upload each unpublished, then create combined post
            photo_ids: list[str] = []
            for path in image_paths:
                resolved = _resolve_image_path(path)
                image_bytes = resolved.read_bytes()
                filename = resolved.name
                resp = await _upload_photo(
                    client=client,
                    page_id=fb_page_id,
                    page_token=page_token,
                    image_bytes=image_bytes,
                    filename=filename,
                    published=False,
                )
                photo_id = resp.get("id")
                if not photo_id:
                    raise ValueError(f"No id in unpublished photo response: {resp}")
                photo_ids.append(str(photo_id))
                logger.debug(
                    "_do_publish: uploaded unpublished photo id={}", photo_id
                )

            feed_resp = await _create_multi_photo_post(
                client=client,
                page_id=fb_page_id,
                page_token=page_token,
                photo_ids=photo_ids,
                message=message,
            )
            post_id = feed_resp.get("id")
            if not post_id:
                raise ValueError(f"No id in feed post response: {feed_resp}")
            return str(post_id)


async def _mark_failed(
    session: AsyncSession, image_post: ImagePost, error: str
) -> None:
    image_post.publish_status = PublishStatus.FAILED
    image_post.error_message = error[:2000]


def _emit_failed(
    publish_sync_fn: Any,
    image_post_id: UUID,
    page_id: UUID,
    error: str,
) -> None:
    from shared_py.events import ImagePostFailedEvent, ImagePostFailedPayload

    try:
        event = ImagePostFailedEvent(
            job_id=image_post_id,
            payload=ImagePostFailedPayload(
                image_post_id=image_post_id,
                page_id=page_id,
                error=error[:500],
            ),
        )
        publish_sync_fn(str(image_post_id), event)
    except Exception as exc:
        logger.warning("_emit_failed: SSE publish failed: {}", exc)
