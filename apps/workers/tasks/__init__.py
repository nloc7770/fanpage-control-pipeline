"""Celery task modules.

Each module declares one or more tasks bound to the shared :class:`task_queue.BaseTask`
and registered against the celery app exported by :mod:`apps.workers.worker_app`.
"""

from __future__ import annotations
