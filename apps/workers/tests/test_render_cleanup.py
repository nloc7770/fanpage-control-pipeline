"""Post-completion disk cleanup hook in ``render_tasks._finalize_job_if_done``.

Verifies that once a job is marked COMPLETED:
  * the source_video file on disk is unlinked and its Asset row deleted;
  * clip_video file + Asset row are preserved (deliverables survive);
  * the worker's scratch dir at ``${WORKER_TMP_DIR}/sff-{job_id}/`` is wiped.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select


def test_completion_cleans_source_video_and_worktmp(
    sqlite_db: Any, make_job: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """End-to-end: render the only clip -> job COMPLETED -> source files gone."""
    from apps.workers.tasks.render_tasks import render_clip
    from database.models import Asset, Clip, Job
    from services.qwen.runner import _mock_edit_plan  # type: ignore[attr-defined]
    from shared_py.enums import AssetKind, ClipStage, JobStatus

    # Point WORKER_TMP_DIR at a fresh tmp dir for this test so we can assert
    # its contents independently of what the conftest seeded.
    worktmp_root = tmp_path / "worktmp"
    worktmp_root.mkdir()
    monkeypatch.setenv("WORKER_TMP_DIR", str(worktmp_root))

    # Storage root for the (real) source/clip files we'll plant on disk.
    storage_root = tmp_path / "storage"
    storage_root.mkdir()

    job_id = make_job()

    # Plant a fake source_video file on disk, register an Asset row pointing
    # at it. Also pre-create the worker scratch dir for this job so we can
    # assert it gets removed.
    source_video_path = storage_root / job_id / "source_video" / "src.mp4"
    source_video_path.parent.mkdir(parents=True)
    source_video_path.write_bytes(b"\x00" * 4096)  # 4 KiB stub

    source_thumb_path = storage_root / job_id / "source_thumbnail" / "thumb.jpg"
    source_thumb_path.parent.mkdir(parents=True)
    source_thumb_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)

    job_worktmp = worktmp_root / f"sff-{job_id}"
    job_worktmp.mkdir()
    (job_worktmp / "ytdlp.tmp.part").write_bytes(b"x" * 1024)

    plan = _mock_edit_plan({"clip_index": 0, "main_hook": "Hello"})

    async def _seed() -> str:
        async with sqlite_db() as session:
            session.add(
                Asset(
                    id=uuid4(),
                    job_id=UUID(job_id),
                    kind=AssetKind.SOURCE_VIDEO,
                    path=str(source_video_path),
                    size_bytes=4096,
                    mime="video/mp4",
                )
            )
            session.add(
                Asset(
                    id=uuid4(),
                    job_id=UUID(job_id),
                    kind=AssetKind.SOURCE_THUMBNAIL,
                    path=str(source_thumb_path),
                    size_bytes=os.path.getsize(source_thumb_path),
                    mime="image/jpeg",
                )
            )
            clip = Clip(
                id=uuid4(),
                job_id=UUID(job_id),
                clip_index=0,
                start_time=5.0,
                end_time=20.0,
                duration=15.0,
                virality_score=8.0,
                topics=["mock"],
                target_style="fast_cut",
                edit_plan=plan.model_dump(mode="json"),
                status=ClipStage.PLANNED,
            )
            session.add(clip)
            await session.commit()
            return str(clip.id)

    clip_id = asyncio.run(_seed())

    # Drive the only clip through to RENDERED -> job marked COMPLETED ->
    # cleanup hook fires inside _finalize_job_if_done.
    render_clip.apply(args=[job_id, clip_id, str(source_video_path)]).get()

    # --- Filesystem assertions -------------------------------------------------
    assert not source_video_path.exists(), "source_video file should be unlinked"
    assert not source_thumb_path.exists(), "source_thumbnail file should be unlinked"
    assert not job_worktmp.exists(), "worker scratch dir should be removed"

    # --- DB assertions ---------------------------------------------------------
    async def _check() -> None:
        async with sqlite_db() as session:
            source_assets = (
                await session.execute(
                    select(Asset).where(
                        Asset.job_id == UUID(job_id),
                        Asset.kind == AssetKind.SOURCE_VIDEO,
                    )
                )
            ).scalars().all()
            assert source_assets == [], "source_video Asset row should be deleted"

            source_thumbs = (
                await session.execute(
                    select(Asset).where(
                        Asset.job_id == UUID(job_id),
                        Asset.kind == AssetKind.SOURCE_THUMBNAIL,
                    )
                )
            ).scalars().all()
            assert source_thumbs == [], "source_thumbnail Asset row should be deleted"

            clip_video_assets = (
                await session.execute(
                    select(Asset).where(
                        Asset.job_id == UUID(job_id),
                        Asset.kind == AssetKind.CLIP_VIDEO,
                    )
                )
            ).scalars().all()
            assert clip_video_assets, "clip_video Asset row must be preserved"
            preserved_path = clip_video_assets[0].path
            assert preserved_path and Path(preserved_path).exists(), (
                f"clip_video file should still exist on disk: {preserved_path}"
            )

            job = (await session.execute(select(Job))).scalar_one()
            assert job.status == JobStatus.COMPLETED

    asyncio.run(_check())
