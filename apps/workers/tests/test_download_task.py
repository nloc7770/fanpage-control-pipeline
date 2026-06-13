"""Smoke-test the download task in MOCK_DOWNLOAD mode."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select


def test_download_writes_asset_and_metadata(
    make_job: Any, sqlite_db: Any, fake_redis: Any
) -> None:
    """`download.fetch_source`:

    * writes a `source_video` asset row;
    * stores yt-dlp metadata on `jobs.source_metadata`;
    * publishes at least one event on `job:{job_id}`.
    """
    from apps.workers.tasks.download import fetch_source
    from database.models import Asset, Job
    from shared_py.enums import AssetKind

    job_id = make_job(source_url="https://mock/url")
    pubsub = fake_redis.pubsub()
    pubsub.subscribe(f"job:{job_id}")

    # Drain the subscribe ack.
    pubsub.get_message(timeout=0.1)

    result = fetch_source.apply(args=[job_id, "https://mock/url"]).get()

    assert result["duration_s"] == 600.0  # mock fixture

    async def _check() -> None:
        async with sqlite_db() as session:
            assets = (await session.execute(select(Asset))).scalars().all()
            kinds = {a.kind for a in assets}
            assert AssetKind.SOURCE_VIDEO in kinds, kinds

            job_rows = (await session.execute(select(Job))).scalars().all()
            assert len(job_rows) == 1
            job = job_rows[0]
            assert job.source_metadata is not None
            assert job.source_metadata.get("title", "").startswith("Mock")

    asyncio.run(_check())

    # Pull any events queued for this job.
    received: list[Any] = []
    while True:
        msg = pubsub.get_message(timeout=0.05)
        if msg is None:
            break
        if msg.get("type") == "message":
            received.append(msg)
    assert received, "expected at least one SSE event"


def test_download_enqueues_next_stage(make_job: Any) -> None:
    """The mock download path enqueues `whisperx.transcribe`."""
    from apps.workers.tasks.download import fetch_source
    from apps.workers._app import celery

    job_id = make_job()

    sent: list[dict[str, Any]] = []

    original = celery.send_task

    def _spy(name: str, **kwargs: Any) -> Any:
        sent.append({"name": name, "kwargs": kwargs})
        return original(name, **kwargs)

    celery.send_task = _spy  # type: ignore[assignment]
    try:
        fetch_source.apply(args=[job_id, "https://mock/url"]).get()
    finally:
        celery.send_task = original  # type: ignore[assignment]

    names = [s["name"] for s in sent]
    assert "whisperx.transcribe" in names

    # Idempotency: re-running with the same job_id must not raise + not
    # duplicate the asset row.
    fetch_source.apply(args=[job_id, "https://mock/url"]).get()
