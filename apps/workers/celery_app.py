"""Re-export ``celery`` so ``celery -A workers.celery_app`` / ``worker_app`` both work.

The docker-compose files were authored against ``workers.celery_app``; the
Phase-2 spec calls the canonical module ``worker_app``. This shim keeps both
invocations functional.
"""

from __future__ import annotations

from apps.workers.worker_app import app, celery

__all__ = ["app", "celery"]
