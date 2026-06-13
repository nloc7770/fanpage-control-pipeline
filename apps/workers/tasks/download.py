"""Stage 1: ``download.fetch_source``.

Pulls the source video with yt-dlp, persists the asset rows, extracts metadata
into ``jobs.source_metadata`` and enqueues stage 2.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from shared_py.enums import AssetKind, JobStatus
from storage import get_storage
from task_queue import BaseTask

from apps.workers.event_publisher import publish_stage_complete
from apps.workers.tasks._helpers import (
    insert_asset,
    publish_progress,
    stage_pct,
    update_job_source_metadata,
    update_job_stage,
)
from apps.workers._app import celery


@celery.task(
    name="download.fetch_source",
    base=BaseTask,
    bind=True,
    queue="download",
)
def fetch_source(self: BaseTask, job_id: str, source_url: str) -> dict[str, Any]:
    """Download ``source_url`` for ``job_id`` and enqueue the next stage."""
    setattr(self, "stage_name", "downloading")
    update_job_stage(
        job_id=job_id,
        new_status=JobStatus.DOWNLOADING,
        stage_name="downloading",
        pct=stage_pct("downloading", 0.0),
    )

    t0 = time.monotonic()
    work_dir = Path(os.environ.get("WORKER_TMP_DIR", "/tmp")) / f"sff-{job_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    def _progress(pct: float, msg: str) -> None:
        global_pct = stage_pct("downloading", pct / 100.0)
        publish_progress(job_id, stage="downloading", pct=global_pct, message=msg)

    # Lazy import so test fixtures don't pull yt-dlp.
    from services.downloader import download

    cookies_path = os.environ.get("DOWNLOAD_COOKIES_FILE") or None
    result = download(
        source_url,
        output_dir=work_dir,
        cookies_path=cookies_path,
        progress_cb=_progress,
    )

    storage = get_storage()
    video_key = f"{job_id}/{AssetKind.SOURCE_VIDEO.value}/{result.video_path.name}"
    put = storage.put(video_key, result.video_path)
    video_asset_id = insert_asset(
        job_id=job_id,
        kind=AssetKind.SOURCE_VIDEO,
        path=put.path,
        size_bytes=put.size_bytes,
        mime="video/mp4",
        metadata={"original_url": source_url},
    )

    thumb_asset_id: str | None = None
    if result.thumbnail_path and result.thumbnail_path.exists():
        thumb_key = (
            f"{job_id}/{AssetKind.SOURCE_THUMBNAIL.value}/{result.thumbnail_path.name}"
        )
        thumb_put = storage.put(thumb_key, result.thumbnail_path)
        thumb_asset_id = insert_asset(
            job_id=job_id,
            kind=AssetKind.SOURCE_THUMBNAIL,
            path=thumb_put.path,
            size_bytes=thumb_put.size_bytes,
            mime="image/webp",
        )

    update_job_source_metadata(job_id=job_id, metadata=result.metadata)
    publish_progress(
        job_id, stage="downloading", pct=stage_pct("downloading", 1.0)
    )
    logger.info(
        "download.fetch_source: job={} video_asset={} thumb_asset={}",
        job_id,
        video_asset_id,
        thumb_asset_id,
    )

    # ---- structured stage_complete payload ---------------------------------
    # Most of the rich fields come from the yt-dlp metadata dict; resolution
    # falls back to ffprobe on the saved file when the runner didn't capture
    # width/height (older mocks).
    width, height = _resolution(result.video_path, result.metadata)
    duration_meta = result.metadata.get("duration_s")
    publish_stage_complete(
        job_id,
        {
            "stage": "download",
            "engine": "yt-dlp",
            "source_url": source_url,
            "video_id": result.metadata.get("id"),
            "title": result.metadata.get("title"),
            "uploader": result.metadata.get("uploader"),
            "duration_s": float(duration_meta) if duration_meta is not None else None,
            "format": result.video_path.suffix.lstrip(".") or None,
            "resolution": f"{width}x{height}" if width and height else None,
            "size_bytes": int(put.size_bytes) if put.size_bytes is not None else None,
            "thumbnail_url": result.metadata.get("thumbnail_url"),
            "elapsed_s": round(time.monotonic() - t0, 2),
        },
    )

    # Hand off to ASR.
    celery.send_task(
        "whisperx.transcribe",
        kwargs={"job_id": job_id, "video_path": put.path},
        queue="whisperx",
    )

    return {
        "video_asset_id": video_asset_id,
        "thumbnail_asset_id": thumb_asset_id,
        "duration_s": result.metadata.get("duration_s"),
    }


def _resolution(
    video_path: Path, metadata: dict[str, Any]
) -> tuple[int | None, int | None]:
    """Best-effort (width, height) for the downloaded source.

    yt-dlp populates ``width`` / ``height`` on the info dict for most
    extractors, but our :class:`DownloadResult.metadata` deliberately doesn't
    expose them. Fall back to ffprobe -- cheap on the already-on-disk file --
    and swallow any errors (mocked / non-mp4 paths).
    """
    w = metadata.get("width")
    h = metadata.get("height")
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return w, h
    try:
        from ffmpeg.probe import ffprobe_json

        info = ffprobe_json(video_path)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                pw = stream.get("width")
                ph = stream.get("height")
                if isinstance(pw, int) and isinstance(ph, int):
                    return pw, ph
    except Exception as exc:  # pragma: no cover - probe is best-effort
        logger.debug("download: ffprobe resolution lookup failed: {}", exc)
    return None, None
