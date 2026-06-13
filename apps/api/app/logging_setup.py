"""Loguru configuration with JSON output and uvicorn intercept.

Replaces the default logging config so that every log line from FastAPI,
uvicorn and SQLAlchemy flows through loguru's structured sink.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from loguru import logger


class InterceptHandler(logging.Handler):
    """Forward stdlib logging records into loguru."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back  # type: ignore[assignment]
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def _serialize_record(record: dict[str, Any]) -> str:
    """JSON-serialize a loguru record for structured output."""
    import json

    payload: dict[str, Any] = {
        "ts": record["time"].isoformat(),
        "level": record["level"].name,
        "logger": record["name"],
        "message": record["message"],
    }
    extra = record.get("extra") or {}
    if extra:
        payload["extra"] = extra
    if record.get("exception") is not None:
        payload["exception"] = str(record["exception"])
    return json.dumps(payload, default=str)


def _sink(message: Any) -> None:
    record = message.record
    print(_serialize_record(record), file=sys.stdout, flush=True)


def configure_logging(level: str = "INFO") -> None:
    """Install loguru as the root logger. Idempotent."""
    logger.remove()
    logger.add(_sink, level=level.upper(), enqueue=False, backtrace=False, diagnose=False)

    logging.basicConfig(handlers=[InterceptHandler()], level=level.upper(), force=True)
    for noisy in (
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "fastapi",
        "sqlalchemy.engine",
    ):
        std_logger = logging.getLogger(noisy)
        std_logger.handlers = [InterceptHandler()]
        std_logger.propagate = False
