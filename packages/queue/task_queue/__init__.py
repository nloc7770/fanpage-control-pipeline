"""Celery configuration shared between the API (publisher) and workers (consumer).

The Python module is named ``task_queue`` rather than ``queue`` to avoid
shadowing the standard-library ``queue`` module (which Celery and Kombu use
internally). The on-disk package directory is still ``packages/queue/``.
"""

from __future__ import annotations

from task_queue.base_task import BaseTask, PermanentTaskError
from task_queue.celery_app import (
    QUEUE_NAMES,
    TASK_ROUTES,
    enqueue,
    make_celery,
    register_signal_logging,
)

__all__ = [
    "BaseTask",
    "PermanentTaskError",
    "QUEUE_NAMES",
    "TASK_ROUTES",
    "enqueue",
    "make_celery",
    "register_signal_logging",
]
