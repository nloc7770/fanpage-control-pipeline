"""Base Celery task with retry/backoff defaults and failure side-effects.

Every worker task inherits :class:`BaseTask`. On unrecoverable failure (after
all retries exhausted) the task transitions the owning ``jobs`` row to
``failed`` and publishes a ``job.failed`` SSE event so the frontend can show
the error immediately instead of waiting for a heartbeat to expire.
"""

from __future__ import annotations

from typing import Any

from celery import Task
from loguru import logger


class PermanentTaskError(Exception):
    """Wrapper exception for errors that must NOT be retried.

    Tasks (or framework wrappers) raise this when an upstream call returned a
    permanent failure -- e.g. HTTP 4xx, validation error, or any condition
    that will produce the same result on the next attempt. It is listed in
    :attr:`BaseTask.dont_autoretry_for` so Celery's autoretry skips it and
    the task transitions straight to FAILURE (firing ``on_failure``).
    """

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.__cause__ = cause


class BaseTask(Task):
    """Common defaults: bounded retries, exponential backoff, jitter.

    Concrete tasks can override ``max_retries``, ``autoretry_for``,
    ``retry_backoff`` or ``retry_kwargs`` on the function-level decorator.
    """

    # Reliability defaults -- override per-task as needed.
    autoretry_for: tuple[type[BaseException], ...] = (Exception,)
    max_retries: int = 3
    retry_backoff: bool = True
    retry_backoff_max: int = 60
    retry_jitter: bool = True
    acks_late: bool = True
    track_started: bool = True

    # Subclasses can list exception types that should NOT trigger a retry
    # (e.g. validation errors that will fail the same way every time).
    # :class:`PermanentTaskError` is included by default so framework-level
    # exception translators can mark errors non-retryable without per-task
    # configuration.
    dont_autoretry_for: tuple[type[BaseException], ...] = (PermanentTaskError,)

    def on_failure(  # type: ignore[override]
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        """Final-failure hook: mark the job ``failed`` + publish ``job.failed``.

        The hook is best-effort: if either side-effect raises we still let the
        original exception propagate to Celery's failure machinery. The
        side-effects live in :mod:`apps.workers.event_publisher` and
        :mod:`apps.workers.db_ctx`; we lazy-import them so this module remains
        importable from the API side (publisher only).
        """
        job_id = self._extract_job_id(args, kwargs)
        stage = getattr(self, "stage_name", self.name or "unknown")
        logger.error(
            "task.final_failure name={} id={} job_id={} exc={}",
            self.name,
            task_id,
            job_id,
            exc,
        )

        if not job_id:
            return

        try:
            from apps.workers.db_ctx import mark_job_failed_sync

            mark_job_failed_sync(job_id=job_id, error=str(exc), stage=stage)
        except Exception as side_exc:  # pragma: no cover - defensive
            logger.exception("on_failure: db update failed: {}", side_exc)

        try:
            from uuid import UUID

            from shared_py.events import JobFailedEvent, JobFailedPayload

            from apps.workers.event_publisher import publish_sync

            event = JobFailedEvent(
                job_id=UUID(str(job_id)),
                payload=JobFailedPayload(stage=stage, error=str(exc)),
            )
            publish_sync(str(job_id), event)
        except Exception as side_exc:  # pragma: no cover - defensive
            logger.exception("on_failure: event publish failed: {}", side_exc)

    @staticmethod
    def _extract_job_id(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
        """Best-effort: every pipeline task takes ``job_id`` as first arg/kwarg."""
        if "job_id" in kwargs:
            return str(kwargs["job_id"])
        if args:
            return str(args[0])
        return None
