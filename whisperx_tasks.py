"""Stage 2: ``whisperx.transcribe``.

WhisperX produces word-level transcripts. The task writes a ``transcripts``
row and persists a ``transcript_json`` asset, then routes to diarization
(or YOLO if diarization is disabled).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared_py.enums import AssetKind, JobStatus
from storage import get_storage
from task_queue import BaseTask

from apps.workers.db_ctx import run_async
from apps.workers.event_publisher import publish_stage_complete
from apps.workers.tasks._helpers import (
    insert_asset,
    publish_progress,
    stage_pct,
    update_job_stage,
)
from apps.workers._app import celery


@celery.task(
    name="whisperx.transcribe",
    base=BaseTask,
    bind=True,
    queue="whisperx",
)
def transcribe(self: BaseTask, job_id: str, video_path: str) -> dict[str, Any]:
    """Transcribe the source video and route onward."""
    setattr(self, "stage_name", "transcribing")
    update_job_stage(
        job_id=job_id,
        new_status=JobStatus.TRANSCRIBING,
        stage_name="transcribing",
        pct=stage_pct("transcribing", 0.0),
    )

    from services.whisperx import transcribe as run_whisperx

    publish_progress(
        job_id, stage="transcribing", pct=stage_pct("transcribing", 0.2)
    )
    t0 = time.monotonic()
    result = run_whisperx(video_path)
    publish_progress(
        job_id, stage="transcribing", pct=stage_pct("transcribing", 0.85)
    )

    # Upsert the transcripts row (one per job).
    transcript_id = _upsert_transcript(
        job_id=job_id,
        language=result.language,
        segments=result.segments,
        words=result.words,
    )

    # Persist transcript_json asset.
    storage = get_storage()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp.write(
            json.dumps(
                {
                    "language": result.language,
                    "segments": result.segments,
                    "words": result.words,
                },
                ensure_ascii=False,
            ).encode("utf-8")
        )
        tmp_path = Path(tmp.name)
    try:
        key = f"{job_id}/{AssetKind.TRANSCRIPT_JSON.value}/transcript.json"
        put = storage.put(key, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    asset_id = insert_asset(
        job_id=job_id,
        kind=AssetKind.TRANSCRIPT_JSON,
        path=put.path,
        size_bytes=put.size_bytes,
        mime="application/json",
        metadata={"transcript_id": transcript_id, "language": result.language},
    )

    publish_progress(
        job_id, stage="transcribing", pct=stage_pct("transcribing", 1.0)
    )
    logger.info(
        "whisperx.transcribe: job={} transcript={} asset={}",
        job_id,
        transcript_id,
        asset_id,
    )

    # ---- structured stage_complete payload ---------------------------------
    segments_count = len(result.segments)
    words_count = len(result.words)
    avg_words = (
        round(words_count / segments_count, 1) if segments_count > 0 else 0.0
    )
    model_name = os.environ.get("WHISPERX_MODEL", "large-v3")
    device = os.environ.get("WHISPERX_DEVICE", "cuda")
    compute_type = os.environ.get("WHISPERX_COMPUTE_TYPE", "float16")
    publish_stage_complete(
        job_id,
        {
            "stage": "transcribe",
            "engine": f"whisperx {model_name}",
            "device": device,
            "compute_type": compute_type,
            "language": result.language,
            # whisperx doesn't expose per-job language probability; leave null
            # rather than fabricate -- the frontend renders "—" for null.
            "language_probability": None,
            "segments_count": int(segments_count),
            "words_count": int(words_count),
            "avg_words_per_segment": float(avg_words),
            "elapsed_s": round(time.monotonic() - t0, 2),
        },
    )

    # Routing: diarization first if enabled, else straight to YOLO.
    enabled = os.environ.get("ENABLE_DIARIZATION", "1") == "1"
    if enabled:
        celery.send_task(
            "diarization.diarize",
            kwargs={"job_id": job_id, "video_path": video_path},
            queue="diarization",
        )
    else:
        celery.send_task(
            "yolo.analyze",
            kwargs={"job_id": job_id, "video_path": video_path},
            queue="yolo",
        )

    # Free GPU memory before handing off to the next stage. WhisperX large-v3
    # holds ~3GB of CUDA tensors; releasing them lets YOLO/diarization load
    # without OOM on a single-GPU box. Wrapped so CPU-only envs don't break.
    _release_gpu_memory()

    return {"transcript_asset_id": asset_id, "language": result.language}


def _release_gpu_memory() -> None:
    """Best-effort ``torch.cuda.empty_cache``; no-op when torch/CUDA is absent."""
    try:
        import torch  # type: ignore[import-not-found]

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("torch.cuda.empty_cache skipped: {}", exc)


def _upsert_transcript(
    *,
    job_id: str,
    language: str,
    segments: list[dict[str, Any]],
    words: list[dict[str, Any]],
) -> str:
    """Upsert the per-job transcript row (idempotent on re-run)."""
    from database.models import Transcript

    async def _body(session: AsyncSession) -> str:
        result = await session.execute(
            select(Transcript).where(Transcript.job_id == UUID(job_id))
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.language = language
            existing.segments = segments
            existing.words = words
            await session.flush()
            return str(existing.id)
        row = Transcript(
            job_id=UUID(job_id),
            language=language,
            segments=segments,
            words=words,
        )
        session.add(row)
        await session.flush()
        return str(row.id)

    return run_async(_body)
