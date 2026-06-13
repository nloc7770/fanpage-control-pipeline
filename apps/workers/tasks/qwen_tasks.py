"""Stages 5-7: Qwen LLM tasks.

* ``qwen.analyze_content`` -- write analysis_results, enqueue clip detection.
* ``qwen.detect_clips`` -- insert clips rows, fan out plan_edit per clip.
* ``qwen.plan_edit`` -- update clips.edit_plan/title/narrative, enqueue render.
* ``qwen.repair_json`` -- helper for callers whose JSON failed to parse.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared_py.enums import AssetKind, ClipStage, JobStatus
from shared_py.events import ClipPlannedEvent, ClipPlannedPayload
from shared_py.llm_contracts import ClipDetectionItem, ClipDetectionResponse, EditPlan
from storage import get_storage
from task_queue import BaseTask

from apps.workers.db_ctx import run_async
from apps.workers.event_publisher import (
    _truncate,
    publish_stage_complete,
    publish_sync,
)
from apps.workers.tasks._helpers import (
    insert_asset,
    publish_progress,
    stage_pct,
    update_job_stage,
)
from apps.workers._app import celery


def _qwen_engine() -> str:
    """Canonical engine label for stage_complete payloads.

    Reads ``QWEN_MODEL`` from the environment (set by the worker container);
    falls back to the production default so dev / mock runs still produce a
    useful string for the frontend.
    """
    return os.environ.get("QWEN_MODEL", "qwen3-coder-next-q5km")


# ---------------------------------------------------------------------------
# qwen.analyze_content
# ---------------------------------------------------------------------------


@celery.task(
    name="qwen.analyze_content",
    base=BaseTask,
    bind=True,
    queue="qwen",
)
def analyze_content(self: BaseTask, job_id: str, video_path: str) -> dict[str, Any]:
    """Persist analysis_results then enqueue ``qwen.detect_clips``."""
    setattr(self, "stage_name", "clip_planning")
    update_job_stage(
        job_id=job_id,
        new_status=JobStatus.CLIP_PLANNING,
        stage_name="clip_planning",
        pct=stage_pct("clip_planning", 0.0),
    )

    transcript_segments, transcript_words, speakers = _load_signals(job_id)

    from services.qwen.runner import analyze_content as run_analysis

    t0 = time.monotonic()
    result = run_analysis(
        transcript=transcript_segments,
        speakers=[{"speaker_id": s["speaker_id"], "timeline": s["timeline"]} for s in speakers],
    )
    _upsert_analysis(job_id=job_id, analysis=result)

    storage = get_storage()
    payload: dict[str, Any] = {
        "emotional_peaks": result.emotional_peaks,
        "viral_moments": result.viral_moments,
        "topic_shifts": result.topic_shifts,
        "retention_signals": result.retention_signals,
        "summary": result.summary,
    }
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        tmp_path = Path(tmp.name)
    try:
        key = f"{job_id}/{AssetKind.ANALYSIS_JSON.value}/analysis.json"
        put = storage.put(key, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    asset_id = insert_asset(
        job_id=job_id,
        kind=AssetKind.ANALYSIS_JSON,
        path=put.path,
        size_bytes=put.size_bytes,
        mime="application/json",
    )

    publish_progress(
        job_id, stage="clip_planning", pct=stage_pct("clip_planning", 0.3)
    )

    # Load duration for the next stage.
    duration = _load_duration(job_id)
    _ = transcript_words  # currently unused here; passed in the next task

    # ---- structured stage_complete payload ---------------------------------
    publish_stage_complete(
        job_id,
        {
            "stage": "analyze",
            "engine": _qwen_engine(),
            "emotional_peaks": int(len(result.emotional_peaks)),
            "viral_moments": int(len(result.viral_moments)),
            "topic_shifts": int(len(result.topic_shifts)),
            "retention_signals": int(len(result.retention_signals)),
            "summary_preview": _truncate(result.summary, limit=200),
            "elapsed_s": round(time.monotonic() - t0, 2),
        },
    )

    celery.send_task(
        "qwen.detect_clips",
        kwargs={"job_id": job_id, "video_path": video_path, "duration_s": duration},
        queue="qwen",
    )

    return {"analysis_asset_id": asset_id}


# ---------------------------------------------------------------------------
# qwen.detect_clips
# ---------------------------------------------------------------------------


@celery.task(
    name="qwen.detect_clips",
    base=BaseTask,
    bind=True,
    queue="qwen",
)
def detect_clips(
    self: BaseTask, job_id: str, video_path: str, duration_s: float
) -> dict[str, Any]:
    """Run viral clip detection and fan out ``qwen.plan_edit`` per clip."""
    setattr(self, "stage_name", "clip_planning")

    transcript_segments, transcript_words, speakers = _load_signals(job_id)
    yolo_summary = _load_yolo_summary(job_id)

    publish_progress(
        job_id, stage="clip_planning", pct=stage_pct("clip_planning", 0.5)
    )

    from services.qwen.runner import detect_clips as run_detect

    t0 = time.monotonic()

    # Pass compact sentence-level segments (no nested words). The runner
    # tries a single-pass prompt first and only chunks if the message size
    # exceeds the Qwen server's context-window budget (see
    # ``services/qwen/runner.py``).
    compact_segments = [
        {
            "start": float(s.get("start", 0.0)),
            "end": float(s.get("end", 0.0)),
            "text": (s.get("text") or "").strip(),
        }
        for s in (transcript_segments or [])
        if (s.get("text") or "").strip()
    ]
    _ = transcript_words  # not used here; word-level data is loaded per-clip in plan_edit

    # Flexible clip count: scale with source duration so a long video produces
    # more clips than a short one. Floor 3, default ~1 clip per 3 minutes,
    # honour explicit override from yolo_summary, no hard cap.
    duration_for_count = duration_s or _load_duration(job_id)
    explicit = (
        yolo_summary.get("target_clip_count") if isinstance(yolo_summary, dict) else None
    )
    if explicit:
        target_count = int(explicit)
    else:
        target_count = max(3, int(round(float(duration_for_count) / 180.0)))

    response: ClipDetectionResponse = run_detect(
        transcript_segments=compact_segments,
        signals={"diarization": speakers, "visual_summary": yolo_summary},
        duration=duration_s or _load_duration(job_id),
        target_clip_count=target_count,
    )

    inserted = _upsert_clips(job_id=job_id, items=response.clips)
    publish_progress(
        job_id, stage="clip_planning", pct=stage_pct("clip_planning", 0.9)
    )

    # Publish a clip.planned event per inserted clip and fan out plan_edit.
    for clip_id, item in inserted:
        event = ClipPlannedEvent(
            job_id=UUID(job_id),
            payload=ClipPlannedPayload(
                clip_id=UUID(clip_id),
                clip_index=item.clip_index,
                title=item.main_hook[:140],
                virality_score=item.virality_score,
            ),
        )
        publish_sync(job_id, event)
        celery.send_task(
            "qwen.plan_edit",
            kwargs={
                "job_id": job_id,
                "clip_id": clip_id,
                "video_path": video_path,
            },
            queue="qwen",
        )

    publish_progress(
        job_id, stage="clip_planning", pct=stage_pct("clip_planning", 1.0)
    )
    logger.info("qwen.detect_clips: job={} inserted {} clips", job_id, len(inserted))

    # ---- structured stage_complete payload ---------------------------------
    # Top-8 clip preview keeps the row under the 2 KB target; ``main_hook`` is
    # already short on the source contract but we still bound to 120 chars to
    # stay defensive against future schema drift.
    clip_previews = [
        {
            "clip_index": int(item.clip_index),
            "duration_s": float(item.duration),
            "virality_score": float(item.virality_score),
            "main_hook": _truncate(item.main_hook, limit=120),
        }
        for _cid, item in inserted[:8]
    ]
    # The runner's pre-merge count isn't on the ClipDetectionResponse, so we
    # report ``candidates_before_merge`` equal to clips_kept (a floor). The
    # runner logs the real pre-merge count for ops who need it.
    publish_stage_complete(
        job_id,
        {
            "stage": "detect_clips",
            "engine": _qwen_engine(),
            "context_window": int(os.environ.get("QWEN_CONTEXT_WINDOW", "16384")),
            "chunks_processed": None,
            "candidates_before_merge": int(len(inserted)),
            "clips_kept": int(len(inserted)),
            "clips": clip_previews,
            "elapsed_s": round(time.monotonic() - t0, 2),
        },
    )

    return {"clip_count": len(inserted), "clip_ids": [cid for cid, _ in inserted]}


# ---------------------------------------------------------------------------
# qwen.plan_edit
# ---------------------------------------------------------------------------


@celery.task(
    name="qwen.plan_edit",
    base=BaseTask,
    bind=True,
    queue="qwen",
)
def plan_edit(self: BaseTask, job_id: str, clip_id: str, video_path: str) -> dict[str, Any]:
    """Run per-clip edit planning, persist plan + title, then enqueue render."""
    setattr(self, "stage_name", "clip_planning")

    clip_dict = _load_clip(clip_id)
    if clip_dict is None:
        logger.error("plan_edit: clip {} not found", clip_id)
        return {"clip_id": clip_id, "skipped": True}

    transcript_window = _load_transcript_window(
        job_id=job_id,
        start=clip_dict["start_time"],
        end=clip_dict["end_time"],
    )
    yolo_window = _load_yolo_window(
        job_id=job_id,
        start=clip_dict["start_time"],
        end=clip_dict["end_time"],
    )

    # If this clip is a recap montage, rebuild the transcript_window from
    # ONLY the highlight portions with timestamps remapped onto the stitched
    # timeline. That way Qwen's narrative_segments come out aligned to the
    # stitched output duration (not the source-window span).
    highlight_segments = clip_dict.get("highlight_segments") or []
    if highlight_segments:
        transcript_window = _restitch_transcript_words(
            transcript_window, highlight_segments
        )
        # Tell Qwen the clip "starts at 0" with stitched duration so it places
        # narrative segments on the recap timeline.
        stitched_dur = sum(
            float(h["end"]) - float(h["start"]) for h in highlight_segments
        )
        clip_dict = dict(clip_dict)
        clip_dict["start_time"] = 0.0
        clip_dict["end_time"] = stitched_dur
        clip_dict["duration"] = stitched_dur

    from services.qwen.runner import plan_edit as run_plan_edit

    t0 = time.monotonic()
    plan: EditPlan = run_plan_edit(
        clip=clip_dict,
        transcript_window=transcript_window,
        yolo_hints=yolo_window,
    )

    # Persist EditPlan asset for debuggability.
    storage = get_storage()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp.write(plan.model_dump_json().encode("utf-8"))
        tmp_path = Path(tmp.name)
    try:
        key = f"{job_id}/{AssetKind.EDIT_PLAN_JSON.value}/clip-{plan.clip_index}.json"
        put = storage.put(key, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    asset_id = insert_asset(
        job_id=job_id,
        kind=AssetKind.EDIT_PLAN_JSON,
        path=put.path,
        size_bytes=put.size_bytes,
        mime="application/json",
        metadata={"clip_id": clip_id, "clip_index": plan.clip_index},
    )

    _update_clip_plan(clip_id=clip_id, plan=plan)

    # ---- structured stage_complete payload ---------------------------------
    # ``subtitle_lines`` is a proxy: the EditPlan contract doesn't carry
    # condensed subtitle lines (those are produced inline by the renderer),
    # so we report the narrative script line count -- the user-visible
    # "burned subtitle line count" the frontend wants to display.
    narrative_lines = (
        len([ln for ln in (plan.narrative_script_vi or "").splitlines() if ln.strip()])
    )
    publish_stage_complete(
        job_id,
        {
            "stage": "plan_edit",
            "engine": _qwen_engine(),
            "clip_index": int(plan.clip_index),
            "clip_id": clip_id,
            "title": _truncate(plan.title, limit=200),
            "hook_preview": _truncate(plan.hook, limit=80),
            "subtitle_lines": int(narrative_lines),
            "pattern_interrupts": int(len(plan.pattern_interrupts)),
            "elapsed_s": round(time.monotonic() - t0, 2),
        },
        clip_id=clip_id,
    )

    # Enqueue render.
    celery.send_task(
        "render.render_clip",
        kwargs={
            "job_id": job_id,
            "clip_id": clip_id,
            "source_video_path": video_path,
        },
        queue="render",
    )

    return {"clip_id": clip_id, "edit_plan_asset_id": asset_id}


# ---------------------------------------------------------------------------
# qwen.repair_json
# ---------------------------------------------------------------------------


@celery.task(
    name="qwen.repair_json",
    base=BaseTask,
    bind=True,
    queue="qwen",
)
def repair_json(self: BaseTask, broken: str, schema_name: str | None = None) -> dict[str, Any]:
    """Standalone repair helper. Returns the repaired text.

    Not a hot-path task -- the chat client repairs inline when ``chat_json``
    is used. We expose this as a Celery task so other tasks (or admin tools)
    can trigger a repair via ``send_task`` without running their own client.
    """
    from ai.qwen_client import QwenClient
    from ai.prompts import json_repair_messages

    with QwenClient() as qwen:
        text = qwen.chat(
            json_repair_messages(broken=broken, schema_hint=schema_name or "unknown"),
            response_format="json",
            temperature=0.0,
        )
    return {"repaired": text}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_signals(
    job_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (transcript_segments, transcript_words, speakers)."""
    from database.models import Speaker, Transcript

    async def _body(
        session: AsyncSession,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        t_result = await session.execute(
            select(Transcript).where(Transcript.job_id == UUID(job_id))
        )
        transcript = t_result.scalar_one_or_none()
        segments = list(transcript.segments) if transcript else []
        words = list(transcript.words) if transcript else []

        s_result = await session.execute(
            select(Speaker).where(Speaker.job_id == UUID(job_id))
        )
        speakers = [
            {"speaker_id": s.speaker_id, "timeline": s.timeline} for s in s_result.scalars()
        ]
        return segments, words, speakers

    return run_async(_body)


def _load_duration(job_id: str) -> float:
    from database.models import Job

    async def _body(session: AsyncSession) -> float:
        result = await session.execute(select(Job).where(Job.id == UUID(job_id)))
        job = result.scalar_one_or_none()
        if job is None or not job.source_metadata:
            return 0.0
        try:
            return float(job.source_metadata.get("duration_s", 0.0))
        except (TypeError, ValueError):
            return 0.0

    return run_async(_body)


def _load_yolo_summary(job_id: str) -> dict[str, Any]:
    from database.models import Asset

    async def _body(session: AsyncSession) -> dict[str, Any]:
        result = await session.execute(
            select(Asset).where(
                Asset.job_id == UUID(job_id),
                Asset.kind == AssetKind.YOLO_JSON,
            )
        )
        asset = result.scalar_one_or_none()
        if asset is None:
            return {}
        return asset.asset_metadata or {}

    return run_async(_body)


def _load_yolo_window(
    job_id: str, *, start: float, end: float
) -> dict[str, Any]:
    """Slice the yolo focal track to the given window. Returns a compact summary."""
    from database.models import Asset

    async def _body(session: AsyncSession) -> dict[str, Any]:
        result = await session.execute(
            select(Asset).where(
                Asset.job_id == UUID(job_id),
                Asset.kind == AssetKind.YOLO_JSON,
            )
        )
        asset = result.scalar_one_or_none()
        if asset is None:
            return {"focal_track": [], "summary": {}}
        try:
            with open(asset.path, "rb") as fp:
                data = json.loads(fp.read())
        except (OSError, json.JSONDecodeError):
            return {"focal_track": [], "summary": asset.asset_metadata or {}}
        focal = [
            p for p in data.get("focal_track", []) if start <= p.get("t", -1) <= end
        ]
        return {"focal_track": focal, "summary": data.get("summary", {})}

    return run_async(_body)


def _restitch_transcript_words(
    words: list[dict[str, Any]],
    highlights: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter + remap words onto the stitched recap timeline.

    Each word's source-time is mapped into the concatenation of the
    highlights (in order); words outside every highlight are dropped. The
    returned list mirrors the ``{start, end, word}`` shape consumed by
    ``services.qwen.runner.plan_edit`` which then groups them into segments.
    """
    if not words or not highlights:
        return words or []
    sorted_hl = sorted(
        ({"start": float(h["start"]), "end": float(h["end"])} for h in highlights),
        key=lambda h: h["start"],
    )

    def _map(t: float) -> float | None:
        offset = 0.0
        for hl in sorted_hl:
            if hl["start"] <= t <= hl["end"]:
                return offset + (t - hl["start"])
            offset += max(0.0, hl["end"] - hl["start"])
        return None

    out: list[dict[str, Any]] = []
    for w in words:
        try:
            ws = float(w.get("start", 0.0))
            we = float(w.get("end", ws + 0.1))
        except (TypeError, ValueError):
            continue
        new_s = _map(ws)
        new_e = _map(we)
        if new_s is None or new_e is None:
            continue
        if new_e <= new_s:
            new_e = new_s + 0.05
        text = str(w.get("word") or w.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "start": new_s,
                "end": new_e,
                "word": text,
                "speaker": w.get("speaker"),
            }
        )
    return out


def _load_transcript_window(
    job_id: str, *, start: float, end: float
) -> list[dict[str, Any]]:
    from database.models import Transcript

    async def _body(session: AsyncSession) -> list[dict[str, Any]]:
        result = await session.execute(
            select(Transcript).where(Transcript.job_id == UUID(job_id))
        )
        transcript = result.scalar_one_or_none()
        if transcript is None:
            return []
        return [
            w
            for w in (transcript.words or [])
            if start <= float(w.get("start", -1)) <= end
        ]

    return run_async(_body)


def _load_clip(clip_id: str) -> dict[str, Any] | None:
    from database.models import Clip

    async def _body(session: AsyncSession) -> dict[str, Any] | None:
        result = await session.execute(select(Clip).where(Clip.id == UUID(clip_id)))
        row = result.scalar_one_or_none()
        if row is None:
            return None
        # Recover highlight_segments seeded by qwen.detect_clips so the
        # stitched recap pipeline (transcript window + render) can use them.
        highlights: list[dict[str, Any]] = []
        edit_plan = row.edit_plan or {}
        if isinstance(edit_plan, dict):
            raw_hl = edit_plan.get("highlight_segments") or []
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
                        highlights.append(
                            {"start": s, "end": e, "reason": str(h.get("reason") or "")}
                        )
        return {
            "id": str(row.id),
            "clip_index": row.clip_index,
            "start_time": row.start_time,
            "end_time": row.end_time,
            "duration": row.duration,
            "virality_score": row.virality_score,
            "main_hook": row.main_hook or "",
            "emotional_peak": row.emotional_peak or "",
            "retention_reason": row.retention_reason or "",
            "topics": row.topics or [],
            "target_style": row.target_style or "",
            "highlight_segments": highlights,
        }

    return run_async(_body)


def _upsert_clips(
    *, job_id: str, items: list[ClipDetectionItem]
) -> list[tuple[str, ClipDetectionItem]]:
    """UPSERT clips by (job_id, clip_index); return (clip_id, item) pairs in input order."""
    from database.models import Clip

    job_uuid = UUID(job_id)

    async def _body(session: AsyncSession) -> list[tuple[str, ClipDetectionItem]]:
        out: list[tuple[str, ClipDetectionItem]] = []
        for item in items:
            # Seed edit_plan with highlight_segments so the recap montage data
            # survives until plan_edit / render_clip. plan_edit later overwrites
            # this whole field with the full EditPlan (which we merge highlights
            # back into via _update_clip_plan).
            hl_serialised = [
                h.model_dump() for h in (item.highlight_segments or [])
            ]
            seed_edit_plan: dict[str, Any] = {"highlight_segments": hl_serialised}

            # Try insert; on conflict update by (job_id, clip_index).
            stmt = (
                pg_insert(Clip)
                .values(
                    id=uuid4(),
                    job_id=job_uuid,
                    clip_index=item.clip_index,
                    start_time=item.start_time,
                    end_time=item.end_time,
                    duration=item.duration,
                    virality_score=item.virality_score,
                    main_hook=item.main_hook,
                    emotional_peak=item.emotional_peak,
                    retention_reason=item.retention_reason,
                    topics=item.topics,
                    target_style=item.target_style,
                    edit_plan=seed_edit_plan,
                    status=ClipStage.PLANNED,
                )
                .on_conflict_do_update(
                    index_elements=["job_id", "clip_index"],
                    set_={
                        "start_time": item.start_time,
                        "end_time": item.end_time,
                        "duration": item.duration,
                        "virality_score": item.virality_score,
                        "main_hook": item.main_hook,
                        "emotional_peak": item.emotional_peak,
                        "retention_reason": item.retention_reason,
                        "topics": item.topics,
                        "target_style": item.target_style,
                        "edit_plan": seed_edit_plan,
                    },
                )
                .returning(Clip.id)
            )
            try:
                result = await session.execute(stmt)
                clip_id = str(result.scalar_one())
            except Exception:
                # SQLite / non-Postgres fallback for tests.
                await session.execute(
                    select(Clip).where(
                        Clip.job_id == job_uuid, Clip.clip_index == item.clip_index
                    )
                )
                existing_q = await session.execute(
                    select(Clip).where(
                        Clip.job_id == job_uuid, Clip.clip_index == item.clip_index
                    )
                )
                existing = existing_q.scalar_one_or_none()
                if existing is not None:
                    existing.start_time = item.start_time
                    existing.end_time = item.end_time
                    existing.duration = item.duration
                    existing.virality_score = item.virality_score
                    existing.main_hook = item.main_hook
                    existing.emotional_peak = item.emotional_peak
                    existing.retention_reason = item.retention_reason
                    existing.topics = item.topics
                    existing.target_style = item.target_style
                    existing.edit_plan = seed_edit_plan
                    await session.flush()
                    clip_id = str(existing.id)
                else:
                    new_id = uuid4()
                    session.add(
                        Clip(
                            id=new_id,
                            job_id=job_uuid,
                            clip_index=item.clip_index,
                            start_time=item.start_time,
                            end_time=item.end_time,
                            duration=item.duration,
                            virality_score=item.virality_score,
                            main_hook=item.main_hook,
                            emotional_peak=item.emotional_peak,
                            retention_reason=item.retention_reason,
                            topics=item.topics,
                            target_style=item.target_style,
                            edit_plan=seed_edit_plan,
                            status=ClipStage.PLANNED,
                        )
                    )
                    await session.flush()
                    clip_id = str(new_id)
            out.append((clip_id, item))
        return out

    return run_async(_body)


def _upsert_analysis(*, job_id: str, analysis: Any) -> None:
    from database.models import AnalysisResult

    async def _body(session: AsyncSession) -> None:
        result = await session.execute(
            select(AnalysisResult).where(AnalysisResult.job_id == UUID(job_id))
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = AnalysisResult(job_id=UUID(job_id))
            session.add(row)
        row.emotional_peaks = list(analysis.emotional_peaks)
        row.viral_moments = list(analysis.viral_moments)
        row.topic_shifts = list(analysis.topic_shifts)
        row.retention_signals = list(analysis.retention_signals)
        row.summary = analysis.summary

    run_async(_body)


def _update_clip_plan(*, clip_id: str, plan: EditPlan) -> None:
    from database.models import Clip

    async def _body(session: AsyncSession) -> None:
        result = await session.execute(select(Clip).where(Clip.id == UUID(clip_id)))
        clip = result.scalar_one_or_none()
        if clip is None:
            logger.warning("_update_clip_plan: clip {} missing", clip_id)
            return
        # Preserve highlight_segments that detect_clips seeded -- EditPlan
        # doesn't model them, so model_dump() would drop them. Merge back in.
        prev = clip.edit_plan or {}
        prev_hl: list[dict[str, Any]] = []
        if isinstance(prev, dict):
            raw_hl = prev.get("highlight_segments") or []
            if isinstance(raw_hl, list):
                prev_hl = [h for h in raw_hl if isinstance(h, dict)]
        new_plan = plan.model_dump(mode="json")
        if prev_hl:
            new_plan["highlight_segments"] = prev_hl
        clip.title = plan.title
        clip.narrative_script_vi = plan.narrative_script_vi
        clip.edit_plan = new_plan

    run_async(_body)
