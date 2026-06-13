"""Server-Sent Events helpers.

Provides a `StreamingResponse` factory configured for SSE and a function that
formats event frames per the spec used in `docs/api-contracts.md`:

    event: <type>
    id: <id>
    data: <json>

The handler also emits a `: ping` comment every 15s as a keep-alive.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse

SSE_HEADERS: dict[str, str] = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

KEEPALIVE_INTERVAL_S = 15.0


def format_sse_frame(
    *,
    event: str,
    data: Any,
    event_id: str | None = None,
) -> bytes:
    """Encode a single SSE frame as bytes."""
    parts: list[str] = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event}")
    if isinstance(data, str | bytes | bytearray):
        body = data.decode("utf-8") if isinstance(data, bytes | bytearray) else data
    else:
        body = json.dumps(data, default=str)
    parts.append(f"data: {body}")
    parts.append("")  # blank line terminator
    parts.append("")
    return "\n".join(parts).encode("utf-8")


def sse_keepalive() -> bytes:
    return b": ping\n\n"


def sse_response(generator: AsyncIterator[bytes]) -> StreamingResponse:
    """Wrap an async byte generator as an SSE `StreamingResponse`."""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def merge_with_keepalive(
    source: AsyncIterator[bytes],
    *,
    interval: float = KEEPALIVE_INTERVAL_S,
) -> AsyncIterator[bytes]:
    """Yield from `source` and inject a keep-alive comment every `interval`s."""
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def _pump() -> None:
        try:
            async for chunk in source:
                await queue.put(chunk)
        finally:
            await queue.put(None)

    pump_task = asyncio.create_task(_pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield sse_keepalive()
                continue
            if item is None:
                return
            yield item
    finally:
        pump_task.cancel()
        try:
            await pump_task
        except (asyncio.CancelledError, Exception):
            pass
