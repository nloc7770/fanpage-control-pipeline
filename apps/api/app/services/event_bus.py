"""Redis pub/sub helpers used by the SSE handler.

Channel layout: `job:{job_id}`. Payloads are JSON-serialized
`shared_py.events` SSE events. Each published event is also written to the
`logs` table (best-effort, fire-and-forget) so a late-attaching client can
replay history on connect.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any
from uuid import UUID

from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from database.models import Log
from shared_py.events import SSEEventType


def channel_for(job_id: UUID) -> str:
    """Canonical Redis pub/sub channel for a job."""
    return f"job:{job_id}"


def _event_dump(event: Any) -> tuple[str, dict[str, Any]]:
    """Return (event_type, plain-dict) for a typed SSE event."""
    if hasattr(event, "model_dump") and hasattr(event, "type"):
        payload: dict[str, Any] = event.model_dump(mode="json", by_alias=True)
        event_type = str(event.type)
        return event_type, payload
    if isinstance(event, dict):
        event_type = str(event.get("type", "unknown"))
        return event_type, event
    raise TypeError(f"Unsupported event type: {type(event)!r}")


async def _persist_log(
    factory: async_sessionmaker[Any],
    job_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Persist an event row to `logs`. Failures are logged but never raised."""
    try:
        async with factory() as session:
            row = Log(
                job_id=job_id,
                level="INFO",
                stage=event_type,
                message=event_type,
                payload=payload,
            )
            session.add(row)
            await session.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.bind(job_id=str(job_id), event_type=event_type).warning(
            "Failed to persist event to logs: {}", exc
        )


async def publish_event(
    redis: Redis,
    job_id: UUID,
    event: Any,
    *,
    session_factory: async_sessionmaker[Any] | None = None,
) -> None:
    """Publish an SSE event to Redis and (best-effort) persist it to `logs`.

    The persistence step is dispatched as a background task so it never blocks
    the hot path. If it fails, a warning is logged and the publish still
    succeeds.
    """
    event_type, payload = _event_dump(event)
    body = json.dumps(payload, default=str)
    await redis.publish(channel_for(job_id), body)

    if session_factory is not None:
        task = asyncio.create_task(
            _persist_log(session_factory, job_id, event_type, payload)
        )
        # Suppress orphan-task warnings by attaching a no-op callback.
        task.add_done_callback(lambda _t: None)


async def subscribe(redis: Redis, job_id: UUID) -> AsyncIterator[dict[str, Any]]:
    """Async generator yielding parsed event dicts for a job.

    Caller is responsible for closing the generator (e.g. via `aclose()`) when
    the consumer disconnects.
    """
    pubsub = redis.pubsub()
    channel = channel_for(job_id)
    await pubsub.subscribe(channel)
    try:
        async for message in pubsub.listen():
            if not message:
                continue
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if data is None:
                continue
            if isinstance(data, bytes | bytearray):
                data = data.decode("utf-8", errors="replace")
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                yield {"type": "unknown", "raw": data}
    finally:
        with suppress(Exception):
            await pubsub.unsubscribe(channel)
        with suppress(Exception):
            await pubsub.aclose()


def is_known_event_type(value: str) -> bool:
    """Helper for routers to check whether a string is a known SSE event."""
    try:
        SSEEventType(value)
    except ValueError:
        return False
    return True
