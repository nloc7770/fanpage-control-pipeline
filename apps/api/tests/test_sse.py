"""Smoke test for the SSE event bus: publish one event, read one event."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services import event_bus
from shared_py.events import JobProgressEvent, JobProgressPayload


@pytest.mark.asyncio
async def test_publish_and_subscribe(
    fake_redis, engine_and_factory
) -> None:
    _engine, factory = engine_and_factory
    job_id = uuid4()

    sub_gen = event_bus.subscribe(fake_redis, job_id)

    async def _read_one() -> dict:
        return await sub_gen.__anext__()

    reader = asyncio.create_task(_read_one())
    await asyncio.sleep(0.05)  # let the subscriber attach

    event = JobProgressEvent(
        job_id=job_id,
        payload=JobProgressPayload(stage="downloading", pct=42.0, message="halfway"),
    )
    await event_bus.publish_event(fake_redis, job_id, event, session_factory=factory)

    result = await asyncio.wait_for(reader, timeout=2.0)
    assert result["type"] == "job.progress"
    assert result["payload"]["stage"] == "downloading"
    assert result["payload"]["pct"] == 42.0

    await sub_gen.aclose()


@pytest.mark.asyncio
async def test_publish_persists_to_logs(
    fake_redis, engine_and_factory
) -> None:
    from sqlalchemy import select

    from database.models import Log

    _engine, factory = engine_and_factory
    job_id = uuid4()

    event = JobProgressEvent(
        job_id=job_id,
        payload=JobProgressPayload(stage="downloading", pct=10.0),
    )
    await event_bus.publish_event(fake_redis, job_id, event, session_factory=factory)

    # Persistence is a background task; give it a tick.
    for _ in range(20):
        async with factory() as session:
            rows = (
                await session.execute(select(Log).where(Log.job_id == job_id))
            ).scalars().all()
            if rows:
                break
        await asyncio.sleep(0.05)
    assert rows, "expected log row to be written"
    assert rows[0].stage == "job.progress"
    payload = rows[0].payload
    assert isinstance(payload, dict)
    assert payload["payload"]["stage"] == "downloading"
    # Round-trip JSON should be intact.
    assert json.dumps(payload)
