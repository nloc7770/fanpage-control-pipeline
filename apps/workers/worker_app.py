"""Celery application instance for the worker container.

Used by the docker-compose ``worker-cpu`` and ``worker-gpu`` services. The
sibling :mod:`apps.workers.celery_app` is a re-export so callers can use
either name (the legacy ``-A workers.celery_app`` invocation still works).

The actual Celery instance lives in :mod:`apps.workers._app` to break the
circular dependency between this module and the task modules (which need to
import ``celery`` at module load to register themselves).
"""

from __future__ import annotations

import os
import time
from functools import wraps
from typing import Any, Callable

from loguru import logger

from apps.workers._app import app, celery


def _log_config() -> None:
    """Configure loguru once per process."""
    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),  # noqa: T201 -- worker logs to stdout
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | "
            "{name}:{function}:{line} | {message}"
        ),
    )


_log_config()

# Import task modules so Celery registers them at app boot.
from apps.workers.tasks import (  # noqa: E402, F401  -- side-effect imports
    diarization_tasks,
    discovery_tasks,
    download as download_task,
    facebook_tasks,
    image_posts_tasks,
    pipeline_tasks,
    qwen_tasks,
    reels_tasks,
    render_tasks,
    whisperx_tasks,
    yolo_tasks,
)

__all__ = ["app", "celery"]

# Reference modules so static analysers don't strip the imports.
_ = (
    download_task,
    whisperx_tasks,
    diarization_tasks,
    yolo_tasks,
    qwen_tasks,
    render_tasks,
    pipeline_tasks,
    discovery_tasks,
    reels_tasks,
    facebook_tasks,
    image_posts_tasks,
)


# ---------------------------------------------------------------------------
# Post-registration wrapper: convert permanent HTTP errors + add structured
# stage-enter / stage-exit logs around every BaseTask run. Applied to the
# autoretry-installed ``_orig_run`` so the wrapper runs INSIDE autoretry --
# raising ``PermanentTaskError`` short-circuits the retry loop via
# ``dont_autoretry_for`` on :class:`task_queue.BaseTask`.
# ---------------------------------------------------------------------------


def _is_permanent_http_error(exc: BaseException) -> bool:
    """Return True for HTTP responses that won't change on retry (4xx, except 429)."""
    try:
        import httpx
    except ImportError:  # pragma: no cover -- httpx is a hard dep in prod
        return False
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        return False
    # 429 is rate-limit -- retrying with backoff IS the right move.
    return 400 <= int(status) < 500 and int(status) != 429


def _install_run_wrappers() -> None:
    """Wrap every BaseTask's ``_orig_run`` with logging + permanent-error translation.

    Celery's autoretry wrapper installs ``_orig_run`` AFTER task registration;
    we walk the registry once at boot and replace it with our own wrapper.
    The wrapper:

    * logs ``task.stage_enter`` and ``task.stage_exit`` with ``job_id`` and
      ``duration_s`` so the worker logs surface clear per-stage boundaries;
    * converts httpx 4xx errors (excluding 429) to
      :class:`task_queue.PermanentTaskError` so they bypass autoretry.
    """
    from task_queue.base_task import BaseTask, PermanentTaskError

    for name, task in list(celery.tasks.items()):
        if not isinstance(task, BaseTask):
            continue
        orig = getattr(task, "_orig_run", None) or task.run
        if getattr(orig, "__sff_wrapped__", False):
            continue

        @wraps(orig)
        def _wrapped(*args: Any, _orig: Callable[..., Any] = orig, _name: str = name, **kwargs: Any) -> Any:
            job_id = kwargs.get("job_id") or (args[1] if len(args) > 1 else None)
            t0 = time.monotonic()
            logger.info(
                "task.stage_enter name={} job_id={} args_len={} kwargs_keys={}",
                _name,
                job_id,
                len(args),
                list(kwargs.keys()),
            )
            try:
                result = _orig(*args, **kwargs)
            except PermanentTaskError:
                raise
            except Exception as exc:
                if _is_permanent_http_error(exc):
                    status = getattr(getattr(exc, "response", None), "status_code", "?")
                    duration_s = time.monotonic() - t0
                    logger.error(
                        "task.stage_exit name={} job_id={} duration_s={:.2f} status=permanent_http_{}",
                        _name,
                        job_id,
                        duration_s,
                        status,
                    )
                    raise PermanentTaskError(
                        f"permanent HTTP {status} from upstream: {exc}", cause=exc
                    ) from exc
                duration_s = time.monotonic() - t0
                logger.warning(
                    "task.stage_exit name={} job_id={} duration_s={:.2f} status=error exc={}",
                    _name,
                    job_id,
                    duration_s,
                    type(exc).__name__,
                )
                raise
            duration_s = time.monotonic() - t0
            logger.info(
                "task.stage_exit name={} job_id={} duration_s={:.2f} status=ok",
                _name,
                job_id,
                duration_s,
            )
            return result

        _wrapped.__sff_wrapped__ = True  # type: ignore[attr-defined]
        if getattr(task, "_orig_run", None) is not None:
            task._orig_run = _wrapped  # type: ignore[attr-defined]
        else:
            task.run = _wrapped  # type: ignore[method-assign]


_install_run_wrappers()
