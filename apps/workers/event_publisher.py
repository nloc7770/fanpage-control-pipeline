"""Synchronous Redis pub/sub helper for SSE events.

Workers publish events via :func:`publish_sync`. The API's SSE handler is
subscribed to ``job:{job_id}`` and forwards each payload verbatim to the
browser. We keep this module sync (not async) so it's safe inside
Celery's prefork tasks without an event loop.

The Redis client is created lazily and cached at module scope; we never close
it explicitly -- the worker process terminating tears down the connection.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any
from uuid import UUID

import redis
from loguru import logger

from shared_py.events import StageCompleteEvent, StageCompletePayload


@lru_cache(maxsize=1)
def _client() -> redis.Redis:
    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    return redis.Redis.from_url(url, decode_responses=False)


def reset_client_cache() -> None:
    """Test helper: drop the cached Redis client (e.g. switching to fakeredis)."""
    _client.cache_clear()


def publish_sync(job_id: str, event: Any) -> int:
    """Publish ``event`` (a pydantic SSE event model or any obj with ``.model_dump_json``).

    Returns the number of subscribers that received the message (Redis ``PUBLISH``
    return value).
    """
    channel = f"job:{job_id}"
    if hasattr(event, "model_dump_json"):
        payload = event.model_dump_json()
    elif isinstance(event, (bytes, str)):
        payload = event
    else:
        import json

        payload = json.dumps(event)

    try:
        return int(_client().publish(channel, payload))
    except Exception as exc:
        logger.warning("publish_sync: failed channel={} err={}", channel, exc)
        return 0


def _truncate(text: str | None, *, limit: int = 200) -> str | None:
    """Return ``text`` truncated to ``limit`` chars with an ellipsis suffix.

    Used for ``summary_preview`` / ``hook_preview`` fields the frontend renders
    inline -- keeps each log row well under the 2 KB target.
    """
    if text is None:
        return None
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _persist_stage_log(
    job_id: str, payload: dict[str, Any], clip_id: str | None = None
) -> None:
    """Insert one row into the ``logs`` table for a ``stage.complete`` event.

    Best-effort: any failure is swallowed (the SSE event still made it to
    Redis). Runs inside the worker's existing ``run_async`` shim so we share
    the prefork-safe per-call engine.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    # Local imports keep this module importable in environments that don't
    # have the DB stack on the path (e.g. ad-hoc CLI tools).
    try:
        from database.models import Log

        from apps.workers.db_ctx import run_async
    except Exception as exc:  # pragma: no cover - defensive import guard
        logger.debug("_persist_stage_log: DB stack unavailable: {}", exc)
        return

    stage = str(payload.get("stage") or "stage.complete")
    message = f"stage.complete:{stage}"

    async def _body(session: AsyncSession) -> None:
        row = Log(
            job_id=UUID(str(job_id)),
            clip_id=UUID(str(clip_id)) if clip_id else None,
            level="INFO",
            stage="stage.complete",
            message=message,
            payload=payload,
        )
        session.add(row)

    try:
        run_async(_body)
    except Exception as exc:  # pragma: no cover - best-effort logging path
        logger.warning(
            "_persist_stage_log failed job={} stage={}: {}", job_id, stage, exc
        )


def publish_stage_complete(
    job_id: str,
    payload: dict[str, Any],
    *,
    clip_id: str | None = None,
) -> None:
    """Emit one ``stage.complete`` event + persist a row to ``logs``.

    ``payload`` must include a ``stage`` key. Any non-scalar values (e.g.
    nested ``clips`` lists) are passed through verbatim -- the model uses
    ``extra="allow"`` so additional fields aren't dropped.

    Both side-effects are best-effort: Redis publish failures and DB write
    failures are logged and swallowed so a single broken downstream never
    cascades into a task-level error on the success path.
    """
    if "stage" not in payload:
        raise ValueError("stage.complete payload must include 'stage'")

    event = StageCompleteEvent(
        job_id=UUID(str(job_id)),
        payload=StageCompletePayload.model_validate(payload),
    )
    # 1) Redis pub/sub -- live SSE consumers see it immediately.
    publish_sync(job_id, event)
    # 2) Persist to logs table -- /jobs/{id}/logs endpoint reads from here.
    _persist_stage_log(
        job_id, event.payload.model_dump(mode="json"), clip_id=clip_id
    )

    # Structured loguru entry for stdout (worker container logs).
    bound = logger.bind(stage=payload.get("stage"), job_id=str(job_id))
    if clip_id:
        bound = bound.bind(clip_id=str(clip_id))
    bound.info("stage.complete | {}", payload.get("stage"))
