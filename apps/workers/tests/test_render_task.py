"""Render task tests with MOCK_RENDER."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select


def test_render_clip_writes_asset_and_updates_render_task(
    sqlite_db: Any, make_job: Any
) -> None:
    """render.render_clip:

    * writes a clip_video asset row;
    * inserts + finalizes a render_tasks row;
    * sets the clip's status to RENDERED;
    * marks the job COMPLETED when it is the only clip.
    """
    from apps.workers.tasks.render_tasks import render_clip
    from database.models import Asset, Clip, Job, RenderTask
    from shared_py.enums import AssetKind, ClipStage, JobStatus
    from shared_py.llm_contracts import EditPlan
    from services.qwen.runner import _mock_edit_plan  # type: ignore[attr-defined]
    from uuid import uuid4

    job_id = make_job()

    # Insert a single planned clip with an edit_plan.
    plan = _mock_edit_plan({"clip_index": 0, "main_hook": "Hello"})

    async def _seed() -> str:
        async with sqlite_db() as session:
            clip = Clip(
                id=uuid4(),
                job_id=__import__("uuid").UUID(job_id),
                clip_index=0,
                start_time=5.0,
                end_time=20.0,
                duration=15.0,
                virality_score=8.0,
                main_hook="Hello",
                emotional_peak="excitement",
                retention_reason="curiosity",
                topics=["mock"],
                target_style="fast_cut",
                title=None,
                edit_plan=plan.model_dump(mode="json"),
                status=ClipStage.PLANNED,
            )
            session.add(clip)
            await session.commit()
            return str(clip.id)

    clip_id = asyncio.run(_seed())

    # Run the task.
    out = render_clip.apply(args=[job_id, clip_id, "/tmp/source.mp4"]).get()

    assert "asset_id" in out
    assert out["duration_s"] > 0

    async def _check() -> None:
        async with sqlite_db() as session:
            assets = (
                await session.execute(
                    select(Asset).where(Asset.kind == AssetKind.CLIP_VIDEO)
                )
            ).scalars().all()
            assert assets, "no clip_video asset row"
            assert assets[0].size_bytes and assets[0].size_bytes > 0

            render_tasks = (await session.execute(select(RenderTask))).scalars().all()
            assert len(render_tasks) == 1
            rt = render_tasks[0]
            assert rt.status == ClipStage.RENDERED
            assert rt.progress_pct == 100.0
            assert rt.output_asset_id is not None
            assert rt.started_at is not None and rt.finished_at is not None

            clip = (await session.execute(select(Clip))).scalar_one()
            assert clip.status == ClipStage.RENDERED

            job = (await session.execute(select(Job))).scalar_one()
            # The single clip rendered, so the job should be marked complete.
            assert job.status == JobStatus.COMPLETED
            assert job.progress_pct == 100.0
            assert job.finished_at is not None

    asyncio.run(_check())


def test_render_clip_missing_plan_is_skipped(
    sqlite_db: Any, make_job: Any
) -> None:
    """A clip without an edit_plan is reported as `skipped` rather than crashing."""
    from apps.workers.tasks.render_tasks import render_clip
    from database.models import Clip
    from shared_py.enums import ClipStage
    from uuid import UUID, uuid4

    job_id = make_job()

    async def _seed() -> str:
        async with sqlite_db() as session:
            clip = Clip(
                id=uuid4(),
                job_id=UUID(job_id),
                clip_index=0,
                start_time=0.0,
                end_time=5.0,
                duration=5.0,
                virality_score=0.0,
                topics=[],
                edit_plan=None,
                status=ClipStage.PLANNED,
            )
            session.add(clip)
            await session.commit()
            return str(clip.id)

    clip_id = asyncio.run(_seed())
    out = render_clip.apply(args=[job_id, clip_id, "/tmp/source.mp4"]).get()
    assert out.get("skipped") is True
