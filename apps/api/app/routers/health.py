"""Liveness and readiness endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from loguru import logger
from sqlalchemy import text

from app.deps import RedisDep, SessionDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Always returns 200 with `{status: "ok"}` -- pure liveness check."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(session: SessionDep, redis: RedisDep, response: Response) -> dict[str, Any]:
    """Verifies the DB and Redis are reachable. Returns 503 on any failure."""
    db_status = "up"
    redis_status = "up"

    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = "down"
        logger.warning("readyz: DB unreachable: {}", exc)

    try:
        pong = await redis.ping()
        if not pong:
            redis_status = "down"
    except Exception as exc:
        redis_status = "down"
        logger.warning("readyz: Redis unreachable: {}", exc)

    body: dict[str, Any] = {
        "status": "ok" if db_status == "up" and redis_status == "up" else "degraded",
        "db": db_status,
        "redis": redis_status,
    }
    if body["status"] != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return body
