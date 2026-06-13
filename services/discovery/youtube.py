"""YouTube discovery service.

Supports two modes controlled by ``YOUTUBE_DISCOVERY_MODE`` env var:
- ``yt_dlp`` (default): uses the yt-dlp Python API with ``default_search="ytsearch"``.
- ``api``: uses YouTube Data API v3 via httpx (requires ``YOUTUBE_API_KEY``).

Both modes call ``filters.passes`` to drop unsuitable candidates, then
``queue_for_generation`` to persist new ``content_sources`` rows and publish
SSE ``content.discovered`` events.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any
from uuid import UUID

from loguru import logger

from services.discovery import filters as _filters

if TYPE_CHECKING:
    from database.models import FacebookPage


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


class _Settings:
    """Thin wrapper around env vars so we don't depend on pydantic-settings here."""

    YOUTUBE_DISCOVERY_MODE: str = os.environ.get("YOUTUBE_DISCOVERY_MODE", "yt_dlp")
    YOUTUBE_API_KEY: str | None = os.environ.get("YOUTUBE_API_KEY")
    YOUTUBE_MAX_RESULTS_PER_PAGE: int = int(
        os.environ.get("YOUTUBE_MAX_RESULTS_PER_PAGE", "10")
    )
    YOUTUBE_MIN_DURATION_SECONDS: int = int(
        os.environ.get("YOUTUBE_MIN_DURATION_SECONDS", "180")
    )
    YOUTUBE_MAX_DURATION_SECONDS: int = int(
        os.environ.get("YOUTUBE_MAX_DURATION_SECONDS", "1800")
    )


def _get_settings() -> _Settings:
    return _Settings()


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------


def _build_search_query(page: "FacebookPage") -> str:
    """Build a YouTube search query from page niche + up to 3 content keywords."""
    parts: list[str] = []
    if page.niche:
        parts.append(page.niche.strip())
    keywords: list[str] = list(page.content_keywords or [])
    for kw in keywords[:3]:
        kw = kw.strip()
        if kw and kw.lower() not in (p.lower() for p in parts):
            parts.append(kw)
    return " ".join(parts) if parts else "viral video"


# ---------------------------------------------------------------------------
# yt-dlp mode
# ---------------------------------------------------------------------------


def _ytdlp_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """Run a yt-dlp flat search and return raw info dicts."""
    try:
        import yt_dlp  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is not installed. Install with `pip install yt-dlp`."
        ) from exc

    search_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "default_search": "ytsearch",
        "noplaylist": True,
        "playlistend": max_results,
    }

    search_query = f"ytsearch{max_results}:{query}"
    with yt_dlp.YoutubeDL(search_opts) as ydl:
        info = ydl.extract_info(search_query, download=False)

    if info is None:
        return []
    entries = info.get("entries") or []
    return list(entries)


def _ytdlp_fetch_metadata(video_id: str) -> dict[str, Any]:
    """Fetch full (non-downloading) metadata for a single video."""
    try:
        import yt_dlp  # type: ignore[import-untyped]
    except ImportError:
        return {}

    url = f"https://www.youtube.com/watch?v={video_id}"
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            meta = ydl.extract_info(url, download=False)
        return meta or {}
    except Exception as exc:
        logger.debug("_ytdlp_fetch_metadata: video_id={} err={}", video_id, exc)
        return {}


def _candidate_from_ytdlp(meta: dict[str, Any]) -> dict[str, Any]:
    """Normalise a yt-dlp info dict into our candidate shape."""
    video_id = meta.get("id") or meta.get("video_id") or ""
    url = meta.get("webpage_url") or meta.get("url") or (
        f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
    )
    duration = meta.get("duration") or 0
    try:
        duration = int(float(duration))
    except (TypeError, ValueError):
        duration = 0

    return {
        "source_url": url,
        "source_title": meta.get("title"),
        "channel_name": meta.get("uploader") or meta.get("channel"),
        "duration_seconds": duration,
        "thumbnail_url": meta.get("thumbnail"),
        "detected_topic": None,
        # Availability / filter fields
        "is_live": bool(meta.get("is_live")),
        "was_live": bool(meta.get("was_live")),
        "live_status": meta.get("live_status", ""),
        "availability": meta.get("availability", ""),
        "age_limit": meta.get("age_limit", 0),
        "description": meta.get("description", ""),
        "title": meta.get("title", ""),
        # Full raw metadata for storage
        "raw_metadata": {
            "id": video_id,
            "title": meta.get("title"),
            "uploader": meta.get("uploader"),
            "channel": meta.get("channel"),
            "duration": duration,
            "view_count": meta.get("view_count"),
            "upload_date": meta.get("upload_date"),
            "thumbnail": meta.get("thumbnail"),
            "availability": meta.get("availability"),
            "age_limit": meta.get("age_limit"),
            "is_live": meta.get("is_live"),
            "was_live": meta.get("was_live"),
            "live_status": meta.get("live_status"),
        },
    }


async def _find_ytdlp(page: "FacebookPage", max_results: int) -> list[dict[str, Any]]:
    """yt-dlp discovery: flat search then per-video metadata fetch."""
    query = _build_search_query(page)
    logger.info(
        "discovery.yt_dlp: page={} query={!r} max_results={}",
        page.id,
        query,
        max_results,
    )

    # Flat search is sync I/O; run in thread to avoid blocking the event loop.
    entries = await asyncio.to_thread(_ytdlp_search, query, max_results)

    settings = _get_settings()
    candidates: list[dict[str, Any]] = []

    for entry in entries:
        video_id = entry.get("id") or entry.get("video_id")
        if not video_id:
            continue

        # Fetch full metadata for each candidate (light extraction, no download).
        meta = await asyncio.to_thread(_ytdlp_fetch_metadata, video_id)
        if not meta:
            # Fall back to flat-search entry if full fetch fails.
            meta = entry

        candidate = _candidate_from_ytdlp(meta)
        if not candidate["source_url"]:
            continue

        ok, reason = _filters.passes(candidate, page, settings)
        if not ok:
            logger.debug(
                "discovery.yt_dlp: skip video_id={} reason={}", video_id, reason
            )
            continue

        candidates.append(candidate)

    return candidates


# ---------------------------------------------------------------------------
# YouTube Data API v3 mode
# ---------------------------------------------------------------------------


async def _find_api(page: "FacebookPage", max_results: int) -> list[dict[str, Any]]:
    """YouTube Data API v3 discovery using httpx async."""
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "httpx is required for api mode. Install with `pip install httpx`."
        ) from exc

    settings = _get_settings()
    api_key = settings.YOUTUBE_API_KEY
    if not api_key:
        raise RuntimeError(
            "YOUTUBE_API_KEY is not set; cannot use api discovery mode."
        )

    query = _build_search_query(page)
    logger.info(
        "discovery.api: page={} query={!r} max_results={}",
        page.id,
        query,
        max_results,
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: search
        search_resp = await client.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": max_results,
                "key": api_key,
                "videoEmbeddable": "true",
                "safeSearch": "moderate",
            },
        )
        search_resp.raise_for_status()
        search_data = search_resp.json()

        video_ids = [
            item["id"]["videoId"]
            for item in search_data.get("items", [])
            if item.get("id", {}).get("videoId")
        ]
        if not video_ids:
            return []

        # Step 2: fetch video details (duration, contentDetails, status)
        videos_resp = await client.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet,contentDetails,status,statistics",
                "id": ",".join(video_ids),
                "key": api_key,
            },
        )
        videos_resp.raise_for_status()
        videos_data = videos_resp.json()

    candidates: list[dict[str, Any]] = []
    for item in videos_data.get("items", []):
        video_id = item.get("id", "")
        snippet = item.get("snippet", {})
        content = item.get("contentDetails", {})
        vstatus = item.get("status", {})
        stats = item.get("statistics", {})

        # Parse ISO 8601 duration (PT#M#S)
        duration_seconds = _parse_iso8601_duration(content.get("duration", ""))

        # Detect age restriction / privacy
        privacy = vstatus.get("privacyStatus", "")
        age_restricted = vstatus.get("madeForKids") is False and content.get(
            "contentRating", {}
        ).get("ytRating") == "ytAgeRestricted"

        candidate: dict[str, Any] = {
            "source_url": f"https://www.youtube.com/watch?v={video_id}",
            "source_title": snippet.get("title"),
            "channel_name": snippet.get("channelTitle"),
            "duration_seconds": duration_seconds,
            "thumbnail_url": (
                snippet.get("thumbnails", {}).get("high", {}).get("url")
                or snippet.get("thumbnails", {}).get("default", {}).get("url")
            ),
            "detected_topic": None,
            # Filter fields
            "is_live": snippet.get("liveBroadcastContent") == "live",
            "was_live": snippet.get("liveBroadcastContent") == "completed",
            "live_status": snippet.get("liveBroadcastContent", ""),
            "availability": privacy,
            "age_limit": 18 if age_restricted else 0,
            "description": snippet.get("description", ""),
            "title": snippet.get("title", ""),
            "raw_metadata": {
                "id": video_id,
                "title": snippet.get("title"),
                "channel": snippet.get("channelTitle"),
                "duration": duration_seconds,
                "view_count": int(stats.get("viewCount", 0) or 0),
                "upload_date": snippet.get("publishedAt", "")[:10].replace("-", ""),
                "thumbnail": (
                    snippet.get("thumbnails", {}).get("high", {}).get("url")
                ),
                "privacy_status": privacy,
                "age_restricted": age_restricted,
            },
        }

        ok, reason = _filters.passes(candidate, page, settings)
        if not ok:
            logger.debug(
                "discovery.api: skip video_id={} reason={}", video_id, reason
            )
            continue

        candidates.append(candidate)

    return candidates


def _parse_iso8601_duration(duration: str) -> int:
    """Parse ISO 8601 duration string (e.g. ``PT4M13S``) to total seconds."""
    import re

    if not duration:
        return 0
    pattern = re.compile(
        r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", re.IGNORECASE
    )
    m = pattern.match(duration)
    if not m:
        return 0
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    seconds = int(m.group(4) or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------


class YouTubeDiscoveryService:
    """Discover YouTube content candidates for a Facebook page.

    Mode is selected from ``YOUTUBE_DISCOVERY_MODE`` env var:
    - ``yt_dlp`` (default): uses yt-dlp Python API.
    - ``api``: uses YouTube Data API v3 (requires ``YOUTUBE_API_KEY``).
    """

    async def find_for_page(
        self,
        page: "FacebookPage",
        max_results: int,
    ) -> list[dict[str, Any]]:
        """Return a list of candidate source dicts for ``page``.

        Each dict contains: source_url, source_title, channel_name,
        duration_seconds, thumbnail_url, detected_topic, raw_metadata.
        """
        settings = _get_settings()
        mode = settings.YOUTUBE_DISCOVERY_MODE

        if mode == "api" and settings.YOUTUBE_API_KEY:
            return await _find_api(page, max_results)
        else:
            if mode == "api" and not settings.YOUTUBE_API_KEY:
                logger.warning(
                    "discovery: YOUTUBE_DISCOVERY_MODE=api but YOUTUBE_API_KEY not set; "
                    "falling back to yt_dlp"
                )
            return await _find_ytdlp(page, max_results)

    async def queue_for_generation(
        self,
        page: "FacebookPage",
        candidates: list[dict[str, Any]],
    ) -> int:
        """Persist new content_sources rows and publish SSE events.

        Uses ON CONFLICT (page_id, source_url) DO NOTHING so re-running
        discovery for the same page is idempotent.

        Returns the count of newly inserted rows.
        """
        if not candidates:
            return 0

        from apps.workers.db_ctx import run_async
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from database.models import ContentSource
        from shared_py.enums import ContentSourceStatus
        from apps.workers.event_publisher import publish_sync
        from shared_py.events import ContentDiscoveredEvent, ContentDiscoveredPayload

        inserted_rows: list[dict[str, Any]] = []

        async def _body(session: AsyncSession) -> list[dict[str, Any]]:
            results: list[dict[str, Any]] = []
            for c in candidates:
                stmt = (
                    pg_insert(ContentSource)
                    .values(
                        page_id=page.id,
                        platform="youtube",
                        source_url=c["source_url"],
                        source_title=c.get("source_title"),
                        channel_name=c.get("channel_name"),
                        duration_seconds=c.get("duration_seconds"),
                        thumbnail_url=c.get("thumbnail_url"),
                        detected_topic=c.get("detected_topic"),
                        status=ContentSourceStatus.DISCOVERED,
                        source_metadata=c.get("raw_metadata"),
                    )
                    .on_conflict_do_nothing(
                        index_elements=["page_id", "source_url"]
                    )
                    .returning(ContentSource.id)
                )
                result = await session.execute(stmt)
                row = result.fetchone()
                if row is not None:
                    results.append(
                        {
                            "id": row[0],
                            "page_id": page.id,
                            "source_url": c["source_url"],
                        }
                    )
            return results

        inserted_rows = run_async(_body)

        # Publish SSE content.discovered per new row (best-effort).
        for row in inserted_rows:
            try:
                event = ContentDiscoveredEvent(
                    job_id=row["id"],  # reuse job_id field as entity id
                    payload=ContentDiscoveredPayload(
                        content_source_id=row["id"],
                        page_id=UUID(str(row["page_id"])),
                        source_url=row["source_url"],
                        platform="youtube",
                    ),
                )
                publish_sync(str(row["id"]), event)
            except Exception as exc:
                logger.warning(
                    "queue_for_generation: SSE publish failed for source={} err={}",
                    row.get("id"),
                    exc,
                )

        logger.info(
            "discovery.queue_for_generation: page={} candidates={} inserted={}",
            page.id,
            len(candidates),
            len(inserted_rows),
        )
        return len(inserted_rows)

    def queue_for_generation_sync(
        self,
        page: "FacebookPage",
        candidates: list[dict[str, Any]],
    ) -> int:
        """Synchronous variant for Celery tasks.

        ``queue_for_generation`` calls ``run_async`` internally which spins up a
        new event loop — that crashes when invoked from inside an active
        ``asyncio.run`` (nested loop). Celery handlers are sync, so they should
        call this method directly with the candidate list (already produced by
        the async ``find_for_page`` step).
        """
        if not candidates:
            return 0

        from apps.workers.db_ctx import run_async
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from database.models import ContentSource
        from shared_py.enums import ContentSourceStatus
        from apps.workers.event_publisher import publish_sync
        from shared_py.events import ContentDiscoveredEvent, ContentDiscoveredPayload

        async def _body(session: AsyncSession) -> list[dict[str, Any]]:
            results: list[dict[str, Any]] = []
            for c in candidates:
                stmt = (
                    pg_insert(ContentSource)
                    .values(
                        page_id=page.id,
                        platform="youtube",
                        source_url=c["source_url"],
                        source_title=c.get("source_title"),
                        channel_name=c.get("channel_name"),
                        duration_seconds=c.get("duration_seconds"),
                        thumbnail_url=c.get("thumbnail_url"),
                        detected_topic=c.get("detected_topic"),
                        status=ContentSourceStatus.DISCOVERED,
                        source_metadata=c.get("raw_metadata"),
                    )
                    .on_conflict_do_nothing(
                        index_elements=["page_id", "source_url"]
                    )
                    .returning(ContentSource.id)
                )
                result = await session.execute(stmt)
                row = result.fetchone()
                if row is not None:
                    results.append(
                        {
                            "id": row[0],
                            "page_id": page.id,
                            "source_url": c["source_url"],
                        }
                    )
            return results

        inserted_rows = run_async(_body)

        for row in inserted_rows:
            try:
                event = ContentDiscoveredEvent(
                    job_id=row["id"],
                    payload=ContentDiscoveredPayload(
                        content_source_id=row["id"],
                        page_id=UUID(str(row["page_id"])),
                        source_url=row["source_url"],
                        platform="youtube",
                    ),
                )
                publish_sync(str(row["id"]), event)
            except Exception as exc:
                logger.warning(
                    "queue_for_generation_sync: SSE publish failed for source={} err={}",
                    row.get("id"),
                    exc,
                )

        logger.info(
            "discovery.queue_for_generation_sync: page={} candidates={} inserted={}",
            page.id,
            len(candidates),
            len(inserted_rows),
        )
        return len(inserted_rows)
