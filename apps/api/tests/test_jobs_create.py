"""POST /jobs end-to-end: DB row created, Celery dispatched, event published."""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from database.models import Job


@pytest.mark.asyncio
async def test_create_job_persists_dispatches_and_publishes(
    client: AsyncClient,
    celery_calls: list[dict[str, object]],
    engine_and_factory,
    fake_redis,
) -> None:
    # Subscribe BEFORE creating the job so we can assert the published event.
    pubsub = fake_redis.pubsub()
    # We don't yet know the job id, so subscribe by pattern.
    await pubsub.psubscribe("job:*")

    payload = {"source_url": "https://example.com/video.mp4"}
    resp = await client.post("/jobs", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_url"] == payload["source_url"]
    assert body["status"] == "queued"
    job_id = body["id"]

    # DB row exists.
    _engine, factory = engine_and_factory
    async with factory() as session:
        rows = (await session.execute(select(Job).where(Job.source_url == payload["source_url"]))).scalars().all()
        assert len(rows) == 1
        assert str(rows[0].id) == job_id
        assert rows[0].status.value == "queued"

    # Celery was called with the download task on the download queue.
    assert celery_calls == [
        {
            "name": "download.fetch_source",
            "args": [job_id],
            "queue": "download",
        }
    ]

    # An event was published on the matching channel.
    received: dict | None = None
    for _ in range(50):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
        if msg and msg.get("type") == "pmessage":
            data = msg["data"]
            if isinstance(data, bytes | bytearray):
                data = data.decode()
            received = json.loads(data)
            channel = msg["channel"]
            if isinstance(channel, bytes | bytearray):
                channel = channel.decode()
            assert channel == f"job:{job_id}"
            break
        await asyncio.sleep(0)
    assert received is not None, "no SSE event published"
    assert received["type"] == "job.created"
    assert received["payload"]["source_url"] == payload["source_url"]

    await pubsub.punsubscribe("job:*")
    await pubsub.aclose()


@pytest.mark.asyncio
async def test_create_job_bad_url_returns_400(client: AsyncClient) -> None:
    resp = await client.post("/jobs", json={"source_url": "not-a-url"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
