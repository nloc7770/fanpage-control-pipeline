"""End-to-end-ish test of the LLM stage with mocked Qwen output."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select


@pytest.fixture
def seeded_signals(sqlite_db: Any, make_job: Any) -> tuple[str, dict[str, Any]]:
    """Insert the upstream-stage outputs (transcript, speakers, yolo asset)."""
    job_id = make_job()
    job_uuid = job_id  # already a str

    from database.models import Asset, Job, Speaker, Transcript
    from shared_py.enums import AssetKind

    async def _setup() -> None:
        async with sqlite_db() as session:
            # Stamp duration on jobs.source_metadata.
            jq = await session.execute(select(Job))
            job = jq.scalars().first()
            job.source_metadata = {"duration_s": 600.0, "title": "test"}

            # Transcript with a few words.
            words = [
                {"start": float(i), "end": float(i) + 0.4, "word": f"w{i}"}
                for i in range(20)
            ]
            session.add(
                Transcript(
                    job_id=job.id,
                    language="en",
                    segments=[{"start": 0.0, "end": 8.0, "text": "w0 w1 w2"}],
                    words=words,
                )
            )

            session.add(
                Speaker(
                    job_id=job.id,
                    speaker_id="SPEAKER_00",
                    timeline=[{"start": 0.0, "end": 600.0}],
                )
            )

            # YOLO asset: persist a JSON file the qwen task can slice.
            yolo_payload = {
                "frame_size": [1920, 1080],
                "sample_fps": 2.0,
                "focal_track": [
                    {"t": float(i) * 0.5, "cx": 0.5, "cy": 0.5} for i in range(40)
                ],
                "detections": [],
                "summary": {"face_present_pct": 1.0, "duration_s": 600.0},
            }
            import tempfile

            with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8"
            ) as fp:
                json.dump(yolo_payload, fp)
                yolo_path = fp.name
            session.add(
                Asset(
                    job_id=job.id,
                    kind=AssetKind.YOLO_JSON,
                    path=yolo_path,
                    size_bytes=Path(yolo_path).stat().st_size,
                    mime="application/json",
                    asset_metadata=yolo_payload["summary"],
                )
            )

            await session.commit()

    asyncio.run(_setup())
    return job_uuid, {}


def test_detect_clips_inserts_clips_and_fans_out_plan_edit(
    seeded_signals: tuple[str, dict[str, Any]], sqlite_db: Any
) -> None:
    """qwen.detect_clips creates clip rows and enqueues qwen.plan_edit per clip."""
    from apps.workers._app import celery
    from apps.workers.tasks.qwen_tasks import detect_clips
    from database.models import Clip

    job_id, _ = seeded_signals

    sent: list[dict[str, Any]] = []
    original = celery.send_task

    def _spy(name: str, **kwargs: Any) -> Any:
        sent.append({"name": name, "kwargs": kwargs})
        return original(name, **kwargs)

    celery.send_task = _spy  # type: ignore[assignment]
    try:
        result = detect_clips.apply(
            args=[job_id, "/tmp/source.mp4", 600.0]
        ).get()
    finally:
        celery.send_task = original  # type: ignore[assignment]

    assert result["clip_count"] >= 1

    async def _check() -> None:
        async with sqlite_db() as session:
            clips = (await session.execute(select(Clip))).scalars().all()
            assert clips, "expected at least one clip row"
            assert all(c.status.value == "planned" for c in clips)
            # Mock fixture produces 3 clips for 600s/target=5 -> "non-overlap"
            # leaves room for ~3 30s clips. Assertions are inclusive to allow
            # future tweaks to the mock heuristic.
            assert 1 <= len(clips) <= 10

    asyncio.run(_check())

    plan_calls = [s for s in sent if s["name"] == "qwen.plan_edit"]
    assert len(plan_calls) == result["clip_count"]

    # Idempotency: re-running detect_clips should not duplicate clip rows
    # (upsert by (job_id, clip_index)). It MAY re-enqueue plan_edit (which is
    # safe -- plan_edit itself is idempotent on clip_id).
    detect_clips.apply(args=[job_id, "/tmp/source.mp4", 600.0]).get()

    async def _check_no_dupes() -> None:
        async with sqlite_db() as session:
            clips = (await session.execute(select(Clip))).scalars().all()
            assert len(clips) == result["clip_count"], (
                "detect_clips re-run duplicated clip rows"
            )

    asyncio.run(_check_no_dupes())


def test_plan_edit_updates_clip_and_enqueues_render(
    seeded_signals: tuple[str, dict[str, Any]], sqlite_db: Any
) -> None:
    """qwen.plan_edit fills edit_plan/title/narrative + enqueues render.render_clip."""
    from apps.workers._app import celery
    from apps.workers.tasks.qwen_tasks import detect_clips, plan_edit
    from database.models import Clip

    job_id, _ = seeded_signals

    # Need clips first.
    detect_clips.apply(args=[job_id, "/tmp/source.mp4", 600.0]).get()

    async def _get_first_clip_id() -> str:
        async with sqlite_db() as session:
            clips = (await session.execute(select(Clip))).scalars().all()
            return str(clips[0].id)

    clip_id = asyncio.run(_get_first_clip_id())

    sent: list[dict[str, Any]] = []
    original = celery.send_task

    def _spy(name: str, **kwargs: Any) -> Any:
        sent.append({"name": name, "kwargs": kwargs})
        return original(name, **kwargs)

    celery.send_task = _spy  # type: ignore[assignment]
    try:
        result = plan_edit.apply(
            args=[job_id, clip_id, "/tmp/source.mp4"]
        ).get()
    finally:
        celery.send_task = original  # type: ignore[assignment]

    assert "edit_plan_asset_id" in result

    async def _check() -> None:
        async with sqlite_db() as session:
            clip = (
                await session.execute(select(Clip).where(Clip.id.cast(type_=__import__("sqlalchemy").String) == clip_id))
            ).scalar_one_or_none()
            if clip is None:
                # sqlite stores UUID as str; try without cast
                clip = (
                    await session.execute(select(Clip))
                ).scalars().first()
            assert clip is not None
            assert clip.edit_plan is not None
            assert clip.title  # mock fixture sets "Viral hook #..."
            assert clip.narrative_script_vi  # VI string

    asyncio.run(_check())

    assert any(s["name"] == "render.render_clip" for s in sent), sent
