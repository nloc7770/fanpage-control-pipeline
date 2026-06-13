"""Celery client used by the API to dispatch tasks.

The API process never imports worker code; it only calls
`celery_app.send_task(name, args=[...], queue=...)`. Task routes mirror the
queue topology documented in ARCHITECTURE.md.
"""

from __future__ import annotations

from typing import Any

from celery import Celery

from app.config import get_settings

# Mapping of task-name prefixes -> Celery queue. Workers subscribe to a subset.
TASK_ROUTES: dict[str, dict[str, str]] = {
    "download.*": {"queue": "download"},
    "asr.*": {"queue": "whisperx"},
    "whisperx.*": {"queue": "whisperx"},
    "diarization.*": {"queue": "diarization"},
    "vision.*": {"queue": "yolo"},
    "yolo.*": {"queue": "yolo"},
    "qwen.*": {"queue": "qwen"},
    "render.*": {"queue": "render"},
    "render-prep.*": {"queue": "render-prep"},
}


def make_celery() -> Celery:
    """Build the API's Celery client. Idempotent for a given settings cache."""
    settings = get_settings()
    broker = settings.CELERY_BROKER_URL or settings.REDIS_URL
    backend = settings.CELERY_RESULT_BACKEND or settings.REDIS_URL

    app = Celery("factory", broker=broker, backend=backend)
    app.conf.task_routes = TASK_ROUTES
    app.conf.task_default_queue = "download"
    app.conf.broker_connection_retry_on_startup = True
    return app


_celery_app: Celery | None = None


def celery_app() -> Celery:
    """Lazy singleton, built on first access."""
    global _celery_app
    if _celery_app is None:
        _celery_app = make_celery()
    return _celery_app


def dispatch(name: str, args: list[Any], queue: str) -> str:
    """Send a Celery task, returning the task id.

    Wraps `send_task` for easy monkeypatching in tests.
    """
    result = celery_app().send_task(name, args=args, queue=queue)
    return str(result.id)
