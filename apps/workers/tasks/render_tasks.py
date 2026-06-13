"""Stage 8: ``render.render_clip`` + ``render.generate_thumbnail``.

Owns the ffmpeg pipeline: cut -> 9:16 crop -> zoom punches -> subtitle burn
-> audio mix -> mp4. Mock mode short-circuits to a black mp4 in seconds.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared_py.enums import AssetKind, ClipStage, JobStatus
from shared_py.events import (
    ClipFailedEvent,
    ClipFailedPayload,
    ClipRenderedEvent,
    ClipRenderedPayload,
    ClipRenderingEvent,
    ClipRenderingPayload,
    JobCompletedEvent,
    JobCompletedPayload,
)
from shared_py.llm_contracts import EditPlan
from storage import get_storage
from task_queue import BaseTask

from apps.workers.db_ctx import run_async
from apps.workers.event_publisher import publish_stage_complete, publish_sync
from apps.workers.tasks._helpers import (
    insert_asset,
    publish_progress,
    stage_pct,
    update_job_stage,
)
from apps.workers._app import celery


@celery.task(
    name="render.render_clip",
    base=BaseTask,
    bind=True,
    queue="render",
)
def render_clip(
    self: BaseTask, job_id: str, clip_id: str, source_video_path: str
) -> dict[str, Any]:
    """Render one clip and publish clip.rendered / clip.failed."""
    setattr(self, "stage_name", "rendering")
    update_job_stage(
        job_id=job_id,
        new_status=JobStatus.RENDERING,
        stage_name="rendering",
        pct=stage_pct("rendering", 0.0),
    )

    clip_meta = _load_clip_with_plan(clip_id)
    if clip_meta is None:
        logger.error("render_clip: clip {} not found", clip_id)
        return {"clip_id": clip_id, "skipped": True}

    plan: EditPlan = clip_meta["plan"]
    clip_index: int = clip_meta["clip_index"]
    highlight_segments = clip_meta.get("highlight_segments") or []
    # Load word-level transcript so the render pipeline can burn karaoke subs
    # and pick zoom beats on long words. Best-effort; if it fails we proceed
    # with no dynamic subs (the pipeline falls back gracefully).
    transcript_words = _load_transcript_words(job_id)

    render_task_id = _create_render_task(clip_id=clip_id, worker_id=self.request.hostname or "worker")

    def _on_pct(pct: float) -> None:
        global_pct = stage_pct("rendering", pct / 100.0)
        publish_progress(job_id, stage="rendering", pct=global_pct)
        event = ClipRenderingEvent(
            job_id=UUID(job_id),
            payload=ClipRenderingPayload(
                clip_id=UUID(clip_id), clip_index=clip_index, pct=pct
            ),
        )
        publish_sync(job_id, event)
        _update_render_progress(render_task_id=render_task_id, pct=pct)

    output_dir = Path(os.environ.get("WORKER_TMP_DIR", "/tmp")) / f"sff-{job_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"clip-{clip_index:03d}.mp4"

    from services.rendering import render

    t0 = time.monotonic()
    try:
        result = render(
            plan=plan,
            source_path=source_video_path,
            output_path=output_path,
            start_time=clip_meta["start_time"],
            end_time=clip_meta["end_time"],
            subtitle_lines=None,  # condensed subs are an optional future step
            transcript_words=transcript_words,
            highlight_segments=highlight_segments,
            progress_cb=_on_pct,
        )
    except Exception as exc:
        logger.exception("render_clip failed for clip={}: {}", clip_id, exc)
        _mark_clip_failed(clip_id=clip_id, render_task_id=render_task_id, error=str(exc))
        publish_sync(
            job_id,
            ClipFailedEvent(
                job_id=UUID(job_id),
                payload=ClipFailedPayload(
                    clip_id=UUID(clip_id), clip_index=clip_index, error=str(exc)
                ),
            ),
        )
        _finalize_job_if_done(job_id)
        raise

    # Persist asset.
    storage = get_storage()
    key = f"{job_id}/{AssetKind.CLIP_VIDEO.value}/clip-{clip_index:03d}.mp4"
    put = storage.put(key, result.output_path)
    asset_id = insert_asset(
        job_id=job_id,
        kind=AssetKind.CLIP_VIDEO,
        path=put.path,
        size_bytes=put.size_bytes,
        mime="video/mp4",
        metadata={
            "clip_id": clip_id,
            "clip_index": clip_index,
            "duration_s": result.duration_s,
        },
    )

    _finalize_render_task(
        render_task_id=render_task_id,
        clip_id=clip_id,
        output_asset_id=asset_id,
        ffmpeg_command=result.ffmpeg_command,
    )

    publish_sync(
        job_id,
        ClipRenderedEvent(
            job_id=UUID(job_id),
            payload=ClipRenderedPayload(
                clip_id=UUID(clip_id),
                clip_index=clip_index,
                asset_id=UUID(asset_id),
            ),
        ),
    )

    # ---- structured stage_complete payload ---------------------------------
    width, height, codec = _video_props(result.output_path)
    publish_stage_complete(
        job_id,
        {
            "stage": "render",
            "engine": "ffmpeg",
            "clip_index": int(clip_index),
            "clip_id": clip_id,
            "output_path": str(put.path),
            "codec": codec,
            "width": width,
            "height": height,
            "duration_s": float(result.duration_s),
            "size_bytes": int(put.size_bytes) if put.size_bytes is not None else None,
            # subtitle_lines==None below; we always burn subs in current pipeline.
            "subtitles_burned": True,
            "elapsed_s": round(time.monotonic() - t0, 2),
        },
        clip_id=clip_id,
    )

    # Best-effort thumbnail (synchronous; cheap when mocked).
    celery.send_task(
        "render.generate_thumbnail",
        kwargs={
            "job_id": job_id,
            "clip_id": clip_id,
            "clip_video_path": put.path,
        },
        queue="render-prep",
    )

    _finalize_job_if_done(job_id)

    return {
        "clip_id": clip_id,
        "asset_id": asset_id,
        "render_task_id": render_task_id,
        "duration_s": result.duration_s,
    }


@celery.task(
    name="render.generate_thumbnail",
    base=BaseTask,
    bind=True,
    queue="render-prep",
)
def generate_thumbnail(
    self: BaseTask, job_id: str, clip_id: str, clip_video_path: str
) -> dict[str, Any]:
    """Snapshot a frame from the clip and store a thumbnails row."""
    setattr(self, "stage_name", "rendering")

    from database.models import Thumbnail

    t0 = time.monotonic()
    output_dir = Path(os.environ.get("WORKER_TMP_DIR", "/tmp")) / f"sff-{job_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = output_dir / f"clip-thumb-{clip_id}.jpg"

    mock = os.environ.get("MOCK_RENDER", "0") == "1"
    try:
        if mock:
            thumb_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)  # tiny stub
            frame_t = 0.0
        else:
            import shutil
            import subprocess

            from ffmpeg.probe import get_duration_s

            duration = get_duration_s(clip_video_path) or 0.0
            ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

            # Smart thumbnail: score candidate frames and pick the best one.
            frame_t = _select_best_thumbnail_frame(
                clip_video_path=clip_video_path,
                duration=duration,
                job_id=job_id,
                output_dir=output_dir,
                ffmpeg_bin=ffmpeg_bin,
            )

            subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-ss",
                    f"{frame_t:.3f}",
                    "-i",
                    str(clip_video_path),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    str(thumb_path),
                ],
                check=True,
                capture_output=True,
            )
    except Exception as exc:
        logger.warning("generate_thumbnail failed for clip={}: {}", clip_id, exc)
        return {"clip_id": clip_id, "thumbnail_id": None}

    # Persist asset + thumbnail row.
    storage = get_storage()
    key = f"{job_id}/{AssetKind.CLIP_THUMBNAIL.value}/clip-{clip_id}.jpg"
    try:
        put = storage.put(key, thumb_path)
    except Exception as exc:
        logger.warning("generate_thumbnail: storage put failed: {}", exc)
        return {"clip_id": clip_id, "thumbnail_id": None}

    asset_id = insert_asset(
        job_id=job_id,
        kind=AssetKind.CLIP_THUMBNAIL,
        path=put.path,
        size_bytes=put.size_bytes,
        mime="image/jpeg",
        metadata={"clip_id": clip_id, "frame_t": frame_t},
    )

    async def _add_thumb(session: AsyncSession) -> str:
        row = Thumbnail(
            clip_id=UUID(clip_id), path=put.path, frame_timestamp=frame_t
        )
        session.add(row)
        await session.flush()
        return str(row.id)

    thumbnail_id = run_async(_add_thumb)

    # ---- structured stage_complete payload ---------------------------------
    publish_stage_complete(
        job_id,
        {
            "stage": "thumbnail",
            "clip_id": clip_id,
            "frame_timestamp": float(frame_t),
            "size_bytes": int(put.size_bytes) if put.size_bytes is not None else None,
            "elapsed_s": round(time.monotonic() - t0, 2),
        },
        clip_id=clip_id,
    )

    return {"clip_id": clip_id, "thumbnail_id": thumbnail_id, "asset_id": asset_id}


# ---------------------------------------------------------------------------
# Smart thumbnail selection
# ---------------------------------------------------------------------------

# Candidate frame positions as fractions of clip duration.
_THUMBNAIL_SAMPLE_POSITIONS: tuple[float, ...] = (0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80)

# Score weights.
_W_BRIGHTNESS = 0.25
_W_CONTRAST = 0.25
_W_SHARPNESS = 0.20
_W_FACE_PRESENCE = 0.30


def _select_best_thumbnail_frame(
    *,
    clip_video_path: str,
    duration: float,
    job_id: str,
    output_dir: Path,
    ffmpeg_bin: str,
) -> float:
    """Score candidate frames and return the timestamp of the best one.

    Falls back to 50% of duration if scoring fails entirely.
    """
    fallback_t = duration / 2.0
    if duration <= 0:
        return fallback_t

    candidate_timestamps = [duration * p for p in _THUMBNAIL_SAMPLE_POSITIONS]

    try:
        scored = _score_thumbnail_candidates(
            clip_video_path=clip_video_path,
            timestamps=candidate_timestamps,
            job_id=job_id,
            output_dir=output_dir,
            ffmpeg_bin=ffmpeg_bin,
        )
    except Exception as exc:
        logger.debug("smart thumbnail scoring failed, using fallback: {}", exc)
        return fallback_t

    if not scored:
        return fallback_t

    best = max(scored, key=lambda item: item[1])
    logger.debug(
        "smart thumbnail: best frame at t={:.2f}s score={:.3f} (of {} candidates)",
        best[0],
        best[1],
        len(scored),
    )
    return best[0]


def _score_thumbnail_candidates(
    *,
    clip_video_path: str,
    timestamps: list[float],
    job_id: str,
    output_dir: Path,
    ffmpeg_bin: str,
) -> list[tuple[float, float]]:
    """Extract candidate frames, score each, and return (timestamp, score) pairs."""
    import subprocess

    from PIL import Image

    # Load YOLO detections for face/person presence scoring.
    yolo_detections = _load_yolo_detections_for_job(job_id)

    scored: list[tuple[float, float]] = []
    candidate_paths: list[Path] = []

    try:
        for i, t in enumerate(timestamps):
            candidate_path = output_dir / f"thumb-candidate-{i}.jpg"
            candidate_paths.append(candidate_path)

            result = subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-ss",
                    f"{t:.3f}",
                    "-i",
                    str(clip_video_path),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    str(candidate_path),
                ],
                capture_output=True,
                timeout=15,
            )
            if result.returncode != 0 or not candidate_path.exists():
                continue

            try:
                img = Image.open(candidate_path).convert("L")  # grayscale for analysis
                score = _compute_frame_score(img, t, yolo_detections)
                scored.append((t, score))
            except Exception as exc:
                logger.debug("scoring candidate {} failed: {}", i, exc)
                continue
    finally:
        # Clean up candidate temp files.
        for p in candidate_paths:
            p.unlink(missing_ok=True)

    return scored


def _compute_frame_score(
    grayscale_img: "Image.Image",
    timestamp: float,
    yolo_detections: list[dict[str, Any]],
) -> float:
    """Compute combined quality score for a single grayscale frame image."""
    import numpy as np

    pixels = np.asarray(grayscale_img, dtype=np.float32)

    brightness_score = _score_brightness(pixels)
    contrast_score = _score_contrast(pixels)
    sharpness_score = _score_sharpness(pixels)
    face_score = _score_face_presence(timestamp, yolo_detections)

    return (
        _W_BRIGHTNESS * brightness_score
        + _W_CONTRAST * contrast_score
        + _W_SHARPNESS * sharpness_score
        + _W_FACE_PRESENCE * face_score
    )


def _score_brightness(pixels: "np.ndarray") -> float:
    """Score brightness: penalize very dark (<50) or very bright (>220) frames."""
    mean_lum = float(pixels.mean())
    if mean_lum < 50:
        # Linear ramp from 0 at lum=0 to 1 at lum=50
        return mean_lum / 50.0
    if mean_lum > 220:
        # Linear ramp from 1 at lum=220 to 0 at lum=255
        return (255.0 - mean_lum) / 35.0
    # Good range: full score
    return 1.0


def _score_contrast(pixels: "np.ndarray") -> float:
    """Score contrast: higher std dev = more visual interest. Normalized to 0-1."""
    std = float(pixels.std())
    # Typical std for a well-contrasted image is 40-80. Cap at 80 for max score.
    return min(std / 80.0, 1.0)


def _score_sharpness(pixels: "np.ndarray") -> float:
    """Score sharpness via Laplacian variance. Higher = sharper."""
    import numpy as np

    # 3x3 Laplacian kernel applied via convolution approximation.
    # Use simple variance of second-order differences as a proxy.
    # Laplacian: sum of second derivatives in x and y.
    lap_x = pixels[:, 2:] + pixels[:, :-2] - 2 * pixels[:, 1:-1]
    lap_y = pixels[2:, :] + pixels[:-2, :] - 2 * pixels[1:-1, :]

    # Combine: use the overlapping region.
    h = min(lap_x.shape[0], lap_y.shape[0])
    w = min(lap_x.shape[1], lap_y.shape[1])
    laplacian = lap_x[:h, :w] + lap_y[:h, :w]

    variance = float(np.var(laplacian))
    # Normalize: typical sharp image has variance 500-2000+. Cap at 1500.
    return min(variance / 1500.0, 1.0)


def _score_face_presence(
    timestamp: float, yolo_detections: list[dict[str, Any]]
) -> float:
    """Score face/person presence near the given timestamp using YOLO data."""
    if not yolo_detections:
        # No YOLO data available; neutral score so other factors decide.
        return 0.5

    # Find detections within 0.5s of the candidate timestamp.
    tolerance = 0.5
    nearby = [
        d
        for d in yolo_detections
        if abs(d.get("t", -999) - timestamp) <= tolerance
        and d.get("cls") in ("person", "face")
    ]

    if not nearby:
        return 0.0

    # Score based on max confidence of nearby person/face detections.
    max_conf = max(d.get("conf", 0.0) for d in nearby)
    # Bonus for multiple detections (capped).
    count_bonus = min(len(nearby) * 0.1, 0.3)
    return min(max_conf + count_bonus, 1.0)


def _load_yolo_detections_for_job(job_id: str) -> list[dict[str, Any]]:
    """Load YOLO detection data for the job. Returns empty list if unavailable."""
    import json

    from database.models import Asset

    try:

        async def _body(session: AsyncSession) -> list[dict[str, Any]]:
            result = await session.execute(
                select(Asset).where(
                    Asset.job_id == UUID(job_id),
                    Asset.kind == AssetKind.YOLO_JSON,
                )
            )
            asset = result.scalar_one_or_none()
            if asset is None:
                return []
            try:
                with open(asset.path, "rb") as fp:
                    data = json.loads(fp.read())
            except (OSError, json.JSONDecodeError):
                return []
            return data.get("detections", [])

        return run_async(_body)
    except Exception as exc:
        logger.debug("failed to load YOLO detections for thumbnail scoring: {}", exc)
        return []


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _maybe_enqueue_reel_generation(job_id: str) -> None:
    """If the job has a facebook_page_id in source_metadata, enqueue reel generation.

    Purely additive: any failure is swallowed so the existing pipeline is unaffected.
    """
    from database.models import Job

    async def _load(session: AsyncSession) -> dict | None:
        q = await session.execute(select(Job).where(Job.id == UUID(job_id)))
        job = q.scalar_one_or_none()
        if job is None:
            return None
        return dict(job.source_metadata or {})

    try:
        metadata = run_async(_load)
        if metadata and metadata.get("facebook_page_id"):
            celery.send_task(
                "reels.generate_from_source",
                kwargs={"job_id": job_id},
                queue="reels",
            )
            logger.info(
                "_maybe_enqueue_reel_generation: enqueued reels.generate_from_source "
                "for job={} page={}",
                job_id,
                metadata["facebook_page_id"],
            )
    except Exception as exc:
        logger.warning(
            "_maybe_enqueue_reel_generation: failed for job={}: {}", job_id, exc
        )


def _video_props(path: Path) -> tuple[int | None, int | None, str | None]:
    """Best-effort (width, height, codec) probe for the rendered clip.

    Returns ``(None, None, None)`` if ffprobe is unavailable or the file is a
    MOCK_RENDER stub. Swallows errors -- the stage_complete event still ships
    with the rest of the payload.
    """
    try:
        from ffmpeg.probe import ffprobe_json

        info = ffprobe_json(path)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                w = stream.get("width")
                h = stream.get("height")
                codec = stream.get("codec_name")
                return (
                    int(w) if isinstance(w, int) else None,
                    int(h) if isinstance(h, int) else None,
                    str(codec) if codec else None,
                )
    except Exception as exc:  # pragma: no cover - mock paths
        logger.debug("render: ffprobe video_props failed for {}: {}", path, exc)
    return None, None, None


def _load_clip_with_plan(clip_id: str) -> dict[str, Any] | None:
    from database.models import Clip

    async def _body(session: AsyncSession) -> dict[str, Any] | None:
        result = await session.execute(select(Clip).where(Clip.id == UUID(clip_id)))
        row = result.scalar_one_or_none()
        if row is None:
            return None
        if not row.edit_plan:
            logger.warning("_load_clip_with_plan: clip {} missing edit_plan", clip_id)
            return None
        plan = EditPlan.model_validate(row.edit_plan)

        # Pull highlight_segments off the JSON blob (they're not part of the
        # EditPlan pydantic schema). When present, the renderer will produce
        # a stitched recap montage instead of a single continuous cut.
        highlights: list[dict[str, Any]] = []
        raw_hl = row.edit_plan.get("highlight_segments") if isinstance(row.edit_plan, dict) else None
        if isinstance(raw_hl, list):
            for h in raw_hl:
                if not isinstance(h, dict):
                    continue
                try:
                    s = float(h.get("start", 0.0))
                    e = float(h.get("end", 0.0))
                except (TypeError, ValueError):
                    continue
                if e > s:
                    highlights.append({"start": s, "end": e})

        return {
            "id": str(row.id),
            "clip_index": row.clip_index,
            "start_time": float(row.start_time),
            "end_time": float(row.end_time),
            "duration": float(row.duration),
            "plan": plan,
            "highlight_segments": highlights,
        }

    return run_async(_body)


def _load_transcript_words(job_id: str) -> list[dict[str, Any]]:
    """Return the flat word list for the job. Returns ``[]`` on any error."""
    from database.models import Transcript

    async def _body(session: AsyncSession) -> list[dict[str, Any]]:
        result = await session.execute(
            select(Transcript).where(Transcript.job_id == UUID(job_id))
        )
        row = result.scalar_one_or_none()
        if row is None:
            return []
        words = row.words or []
        if not isinstance(words, list):
            return []
        return list(words)

    try:
        return run_async(_body)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("_load_transcript_words failed for job={}: {}", job_id, exc)
        return []


def _create_render_task(*, clip_id: str, worker_id: str) -> str:
    from database.models import RenderTask

    async def _body(session: AsyncSession) -> str:
        row = RenderTask(
            id=uuid4(),
            clip_id=UUID(clip_id),
            worker_id=worker_id[:128] if worker_id else None,
            status=ClipStage.RENDERING,
            started_at=datetime.utcnow(),
            progress_pct=0.0,
        )
        session.add(row)
        await session.flush()
        return str(row.id)

    return run_async(_body)


def _update_render_progress(*, render_task_id: str, pct: float) -> None:
    from database.models import RenderTask

    async def _body(session: AsyncSession) -> None:
        result = await session.execute(
            select(RenderTask).where(RenderTask.id == UUID(render_task_id))
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.progress_pct = float(pct)

    try:
        run_async(_body)
    except Exception as exc:  # progress updates are best-effort
        logger.debug("progress update suppressed: {}", exc)


def _finalize_render_task(
    *,
    render_task_id: str,
    clip_id: str,
    output_asset_id: str,
    ffmpeg_command: str | None,
) -> None:
    from database.models import Clip, RenderTask

    async def _body(session: AsyncSession) -> None:
        rt_result = await session.execute(
            select(RenderTask).where(RenderTask.id == UUID(render_task_id))
        )
        rt = rt_result.scalar_one_or_none()
        if rt is not None:
            rt.status = ClipStage.RENDERED
            rt.finished_at = datetime.utcnow()
            rt.output_asset_id = UUID(output_asset_id)
            rt.progress_pct = 100.0
            if ffmpeg_command:
                rt.ffmpeg_command = ffmpeg_command[:8000]

        clip_result = await session.execute(select(Clip).where(Clip.id == UUID(clip_id)))
        clip = clip_result.scalar_one_or_none()
        if clip is not None:
            clip.status = ClipStage.RENDERED

    run_async(_body)


def _mark_clip_failed(
    *, clip_id: str, render_task_id: str | None, error: str
) -> None:
    from database.models import Clip, RenderTask

    async def _body(session: AsyncSession) -> None:
        if render_task_id:
            rt_result = await session.execute(
                select(RenderTask).where(RenderTask.id == UUID(render_task_id))
            )
            rt = rt_result.scalar_one_or_none()
            if rt is not None:
                rt.status = ClipStage.FAILED
                rt.finished_at = datetime.utcnow()
                rt.error_message = error[:8000]
        clip_result = await session.execute(select(Clip).where(Clip.id == UUID(clip_id)))
        clip = clip_result.scalar_one_or_none()
        if clip is not None:
            clip.status = ClipStage.FAILED

    run_async(_body)


def _finalize_job_if_done(job_id: str) -> None:
    """Check if all clips are terminal; if so, set jobs.status to completed/failed."""
    from database.models import Clip, Job

    async def _body(session: AsyncSession) -> tuple[JobStatus | None, int, float]:
        rows = await session.execute(
            select(Clip.status).where(Clip.job_id == UUID(job_id))
        )
        statuses = [row[0] for row in rows.all()]
        if not statuses:
            return None, 0, 0.0
        terminal = {ClipStage.RENDERED, ClipStage.FAILED}
        if any(s not in terminal for s in statuses):
            return None, 0, 0.0
        rendered = sum(1 for s in statuses if s == ClipStage.RENDERED)
        # Compute total duration from rendered clips.
        dur_q = await session.execute(
            select(Clip.duration).where(
                Clip.job_id == UUID(job_id),
                Clip.status == ClipStage.RENDERED,
            )
        )
        total_dur = sum(float(d[0] or 0.0) for d in dur_q.all())
        new_status = JobStatus.COMPLETED if rendered > 0 else JobStatus.FAILED

        job_q = await session.execute(select(Job).where(Job.id == UUID(job_id)))
        job = job_q.scalar_one_or_none()
        if job is not None:
            job.status = new_status
            job.current_stage = "completed" if new_status == JobStatus.COMPLETED else "failed"
            job.progress_pct = 100.0
            job.finished_at = datetime.utcnow()
        return new_status, rendered, total_dur

    new_status, rendered, total_dur = run_async(_body)
    if new_status is None:
        return

    if new_status == JobStatus.COMPLETED:
        publish_sync(
            job_id,
            JobCompletedEvent(
                job_id=UUID(job_id),
                payload=JobCompletedPayload(clip_count=rendered, duration_s=total_dur),
            ),
        )
        # Phase 2C: if this job was triggered by a Facebook page discovery,
        # enqueue reel draft generation. Purely additive — does not affect the
        # existing render pipeline in any way.
        _maybe_enqueue_reel_generation(job_id)
        # Reclaim disk: drop the raw downloaded source video + the worker's
        # temp scratch dir for this job. Runs *after* the user-visible
        # job.completed SSE so the frontend never waits on it. Wrapped so a
        # missing/locked file never demotes a successful job.
        try:
            _cleanup_job_disk_assets(job_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "_cleanup_job_disk_assets: unexpected failure for job={}: {}",
                job_id,
                exc,
            )
    else:
        from shared_py.events import JobFailedEvent, JobFailedPayload

        publish_sync(
            job_id,
            JobFailedEvent(
                job_id=UUID(job_id),
                payload=JobFailedPayload(
                    stage="rendering", error="all clips failed to render"
                ),
            ),
        )
        # FAILED jobs accumulate the same source_video files. Clean them too —
        # the user can still inspect deliverables (clip_video etc) since
        # those rows survive. Best-effort, never raises.
        try:
            _cleanup_job_disk_assets(job_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "_cleanup_job_disk_assets (failed branch): unexpected failure "
                "for job={}: {}",
                job_id,
                exc,
            )


# ---------------------------------------------------------------------------
# Disk-asset cleanup (post-completion)
# ---------------------------------------------------------------------------


# Asset kinds whose on-disk file is no longer needed once the job is done.
# clip_video, clip_thumbnail, transcript_json, edit_plan_json, etc are
# the deliverables — DO NOT include them here.
_CLEANUP_ASSET_KINDS: tuple[AssetKind, ...] = (
    AssetKind.SOURCE_VIDEO,
    AssetKind.SOURCE_THUMBNAIL,
)


def _cleanup_job_disk_assets(job_id: str) -> None:
    """Delete heavy source assets + the worker scratch dir for a finished job.

    Removes:
      * ``Asset`` rows of kind ``source_video`` / ``source_thumbnail`` for the
        job — and unlinks the file at ``Asset.path``.
      * ``${WORKER_TMP_DIR}/sff-{job_id}/`` if present (yt-dlp + ffmpeg
        scratch).

    Preserved: ``clip_video``, ``clip_thumbnail``, transcript_json,
    edit_plan_json, etc — and the rest of ``_storage_data/{job_id}/``.

    No-op when ``STORAGE_BACKEND != local`` for the asset filesystem half;
    the DB rows + worktmp dir are still cleaned (s3 path is not where
    we'd unlink the local copy from anyway).
    """
    from database.models import Asset

    storage_backend = (os.environ.get("STORAGE_BACKEND") or "local").lower()
    is_local = storage_backend == "local"

    freed_bytes = 0
    files_removed = 0

    async def _body(session: AsyncSession) -> int:
        nonlocal freed_bytes, files_removed
        result = await session.execute(
            select(Asset).where(
                Asset.job_id == UUID(job_id),
                Asset.kind.in_(_CLEANUP_ASSET_KINDS),
            )
        )
        rows = list(result.scalars().all())
        for asset in rows:
            path = asset.path
            if is_local and path:
                try:
                    if os.path.exists(path):
                        try:
                            size = os.path.getsize(path)
                        except OSError:
                            size = 0
                        os.unlink(path)
                        freed_bytes += int(size)
                        files_removed += 1
                except Exception as exc:
                    logger.warning(
                        "_cleanup_job_disk_assets: failed to unlink {} "
                        "(asset={}, kind={}): {}",
                        path,
                        asset.id,
                        asset.kind,
                        exc,
                    )
            try:
                await session.delete(asset)
            except Exception as exc:
                logger.warning(
                    "_cleanup_job_disk_assets: failed to delete asset row "
                    "{} (kind={}): {}",
                    asset.id,
                    asset.kind,
                    exc,
                )
        return len(rows)

    try:
        rows_deleted = run_async(_body)
    except Exception as exc:
        logger.warning(
            "_cleanup_job_disk_assets: DB cleanup failed for job={}: {}",
            job_id,
            exc,
        )
        rows_deleted = 0

    # Worker scratch dir (always filesystem-local; safe regardless of backend).
    worktmp_root = os.environ.get("WORKER_TMP_DIR", "/tmp")
    worktmp_dir = Path(worktmp_root) / f"sff-{job_id}"
    worktmp_removed = False
    if worktmp_dir.exists():
        try:
            shutil.rmtree(worktmp_dir, ignore_errors=True)
            worktmp_removed = not worktmp_dir.exists()
        except Exception as exc:
            logger.warning(
                "_cleanup_job_disk_assets: failed to remove worktmp {}: {}",
                worktmp_dir,
                exc,
            )

    logger.info(
        "_cleanup_job_disk_assets: job={} asset_rows_deleted={} "
        "files_removed={} freed_bytes={} worktmp_removed={} backend={}",
        job_id,
        rows_deleted,
        files_removed,
        freed_bytes,
        worktmp_removed,
        storage_backend,
    )
