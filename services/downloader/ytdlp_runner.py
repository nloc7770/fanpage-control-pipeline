"""Thin yt-dlp programmatic wrapper.

We intentionally avoid the CLI for two reasons: (1) avoids shelling out per
job, (2) lets us hook progress events without parsing stderr. The runner
honors ``MOCK_DOWNLOAD=1`` and returns a synthetic fixture file path so the
end-to-end pipeline can run on a dev box without network access.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass(slots=True)
class DownloadResult:
    """Output of :func:`download`.

    ``thumbnail_path`` and ``audio_path`` are optional -- yt-dlp doesn't always
    produce a separate audio file when the merged container already has the
    audio track.
    """

    video_path: Path
    metadata: dict[str, Any]
    thumbnail_path: Path | None = None
    audio_path: Path | None = None
    extras: dict[str, Any] = field(default_factory=dict)


DEFAULT_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best"
PROGRESS_THROTTLE_S = 1.0


def download(
    url: str,
    output_dir: str | Path,
    *,
    cookies_path: str | Path | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
    format_selector: str | None = None,
) -> DownloadResult:
    """Download ``url`` into ``output_dir``.

    The ``progress_cb`` is called as ``(pct_in_0_100, message)``. It is invoked
    at most once a second by default; downstream code should still treat it
    as best-effort and idempotent.
    """
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if os.environ.get("MOCK_DOWNLOAD", "0") == "1":
        return _mock_download(url, output_dir, progress_cb)

    try:
        import yt_dlp  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - install-time error
        raise RuntimeError(
            "yt-dlp is not installed. Install with `pip install yt-dlp` "
            "or set MOCK_DOWNLOAD=1 for dev mode."
        ) from exc

    last_emit = [0.0]  # mutable from closure

    def _hook(d: dict[str, Any]) -> None:
        if progress_cb is None:
            return
        if d.get("status") == "downloading":
            import time

            now = time.monotonic()
            if now - last_emit[0] < PROGRESS_THROTTLE_S:
                return
            last_emit[0] = now
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = (done / total * 100.0) if total else 0.0
            progress_cb(pct, f"downloaded {done}/{total} bytes")
        elif d.get("status") == "finished":
            progress_cb(100.0, "post-processing")

    ydl_opts: dict[str, Any] = {
        "format": format_selector or os.environ.get("DOWNLOAD_FORMAT", DEFAULT_FORMAT),
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_hook],
        "writethumbnail": True,
        "postprocessors": [
            {"key": "FFmpegMetadata"},
        ],
    }
    if cookies_path:
        ydl_opts["cookiefile"] = str(cookies_path)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError(f"yt-dlp returned no info for {url}")
        # yt-dlp stores per-entry info under "entries" for playlists; we set
        # noplaylist=True but guard anyway.
        if "entries" in info and info.get("entries"):
            info = info["entries"][0]
        # ``prepare_filename`` returns the canonical output path that yt-dlp
        # used (before merge it may have an intermediate ext; after merge a
        # FFmpegMerger postprocessor rewrites ``filepath``/``_filename`` in
        # ``info`` to point at the final container). Prefer the post-processed
        # ``filepath`` / ``_filename``; fall back to ``prepare_filename``.
        candidate = (
            info.get("filepath")
            or info.get("_filename")
            or ydl.prepare_filename(info)
        )

    video_path = Path(candidate).resolve() if candidate else None
    if not video_path or not video_path.is_file():
        # Fall back to globbing the output directory by id.
        vid_id = info.get("id", "") if info else ""
        candidates = [
            p
            for p in output_dir.glob(f"{vid_id}*")
            if p.is_file() and p.suffix.lower() in (".mp4", ".mkv", ".webm", ".m4v")
        ]
        if not candidates:
            raise RuntimeError(
                f"yt-dlp finished but no video file under {output_dir} (id={vid_id})"
            )
        # Prefer mp4 if present.
        candidates.sort(key=lambda p: (0 if p.suffix.lower() == ".mp4" else 1, p.name))
        video_path = candidates[0].resolve()

    thumb_path = _pick_thumbnail(output_dir, info)
    if thumb_path is not None:
        thumb_path = thumb_path.resolve()

    metadata = {
        "title": info.get("title"),
        "duration_s": float(info.get("duration") or 0.0),
        "thumbnail_url": info.get("thumbnail"),
        "uploader": info.get("uploader"),
        "upload_date": info.get("upload_date"),
        "view_count": info.get("view_count"),
        "id": info.get("id"),
        "extractor": info.get("extractor"),
    }
    logger.info(
        "downloader: completed url={} video={} duration={}s",
        url,
        video_path,
        metadata.get("duration_s"),
    )
    return DownloadResult(
        video_path=video_path,
        thumbnail_path=thumb_path,
        metadata=metadata,
    )


def _pick_thumbnail(output_dir: Path, info: dict[str, Any]) -> Path | None:
    vid_id = info.get("id") or ""
    if not vid_id:
        return None
    for ext in ("webp", "jpg", "jpeg", "png"):
        cand = output_dir / f"{vid_id}.{ext}"
        if cand.exists():
            return cand
    return None


def _mock_download(
    url: str,
    output_dir: Path,
    progress_cb: Callable[[float, str], None] | None,
) -> DownloadResult:
    """Produce a deterministic fixture without touching the network."""
    logger.info("MOCK_DOWNLOAD: synthesising fixture for url={}", url)

    video_path = output_dir / "mock_source.mp4"
    if not video_path.exists():
        # Write a tiny valid mp4 stub. If ffmpeg is available, render a 5s
        # black clip; otherwise just touch an empty file (the rest of the
        # pipeline runs in MOCK mode anyway).
        try:
            from ffmpeg.pipeline import write_blank_mp4

            write_blank_mp4(video_path, duration_s=5.0)
        except Exception as exc:
            logger.warning("MOCK_DOWNLOAD: ffmpeg unavailable ({}); writing stub", exc)
            video_path.write_bytes(b"\x00" * 64)

    if progress_cb:
        progress_cb(50.0, "mock: midway")
        progress_cb(100.0, "mock: done")

    metadata = {
        "title": "Mock source for shortform-factory",
        "duration_s": 600.0,
        "thumbnail_url": None,
        "uploader": "mock",
        "upload_date": "20260101",
        "view_count": 1234,
        "id": "mock",
        "extractor": "mock",
    }
    return DownloadResult(video_path=video_path.resolve(), metadata=metadata)
