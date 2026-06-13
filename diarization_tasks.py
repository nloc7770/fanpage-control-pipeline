"""Stage 3: ``diarization.diarize``.

Runs pyannote diarization, persists per-speaker rows, merges speaker labels
into ``transcripts.segments`` and routes onward to YOLO.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared_py.enums import AssetKind, JobStatus
from storage import get_storage
from task_queue import BaseTask

from apps.workers.db_ctx import run_async
from apps.workers.tasks._helpers import (
    insert_asset,
    publish_progress,
    stage_pct,
    update_job_stage,
)
from apps.workers._app import celery


@celery.task(
    name="diarization.diarize",
    base=BaseTask,
    bind=True,
    queue="diarization",
)
def diarize(self: BaseTask, job_id: str, video_path: str) -> dict[str, Any]:
    """Run pyannote diarization, merge labels, persist asset, enqueue YOLO."""
    setattr(self, "stage_name", "analyzing")
    update_job_stage(
        job_id=job_id,
        new_status=JobStatus.ANALYZING,
        stage_name="analyzing",
        pct=stage_pct("analyzing", 0.0),
    )

    from services.diarization import diarize as run_diar

    turns = run_diar(video_path)
    publish_progress(
        job_id, stage="analyzing", pct=stage_pct("analyzing", 0.3)
    )

    _replace_speakers(job_id=job_id, turns=turns)
    _merge_speaker_labels(job_id=job_id, turns=turns)

    storage = get_storage()
    payload = [
        {"speaker_id": t.speaker_id, "start": t.start, "end": t.end} for t in turns
    ]
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp.write(json.dumps(payload).encode("utf-8"))
        tmp_path = Path(tmp.name)
    try:
        key = f"{job_id}/{AssetKind.DIARIZATION_JSON.value}/diarization.json"
        put = storage.put(key, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    asset_id = insert_asset(
        job_id=job_id,
        kind=AssetKind.DIARIZATION_JSON,
        path=put.path,
        size_bytes=put.size_bytes,
        mime="application/json",
        metadata={"speaker_count": len({t.speaker_id for t in turns})},
    )

    publish_progress(
        job_id, stage="analyzing", pct=stage_pct("analyzing", 0.6)
    )
    logger.info(
        "diarization.diarize: job={} turns={} asset={}",
        job_id,
        len(turns),
        asset_id,
    )

    celery.send_task(
        "yolo.analyze",
        kwargs={"job_id": job_id, "video_path": video_path},
        queue="yolo",
    )

    return {"diarization_asset_id": asset_id, "turn_count": len(turns)}


def _replace_speakers(*, job_id: str, turns: list[Any]) -> None:
    """Atomic replace: delete prior rows, insert one row per speaker_id with timeline."""
    from database.models import Speaker

    by_speaker: dict[str, list[dict[str, float]]] = {}
    for t in turns:
        by_speaker.setdefault(t.speaker_id, []).append({"start": t.start, "end": t.end})

    async def _body(session: AsyncSession) -> None:
        await session.execute(delete(Speaker).where(Speaker.job_id == UUID(job_id)))
        for speaker_id, timeline in by_speaker.items():
            session.add(
                Speaker(
                    job_id=UUID(job_id),
                    speaker_id=speaker_id,
                    timeline=timeline,
                )
            )

    run_async(_body)


def _merge_speaker_labels(*, job_id: str, turns: list[Any]) -> None:
    """Attach a ``speaker`` field to each transcript word/segment that overlaps a turn."""
    from database.models import Transcript

    async def _body(session: AsyncSession) -> None:
        result = await session.execute(
            select(Transcript).where(Transcript.job_id == UUID(job_id))
        )
        transcript = result.scalar_one_or_none()
        if transcript is None:
            logger.warning("merge_speaker_labels: no transcript for job {}", job_id)
            return
        for seg in transcript.segments:
            seg["speaker"] = _label_at(turns, (seg.get("start", 0.0) + seg.get("end", 0.0)) / 2)
        for word in transcript.words:
            word["speaker"] = _label_at(turns, (word.get("start", 0.0) + word.get("end", 0.0)) / 2)
        # SQLAlchemy needs us to flag JSONB columns as modified.
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(transcript, "segments")
        flag_modified(transcript, "words")

    run_async(_body)


def _label_at(turns: list[Any], t: float) -> str | None:
    for turn in turns:
        if turn.start <= t <= turn.end:
            return turn.speaker_id
    return None
