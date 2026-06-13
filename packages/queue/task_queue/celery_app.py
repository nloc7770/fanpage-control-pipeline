"""Celery application factory and routing table.

The factory is intentionally small: it builds a :class:`celery.Celery` with the
broker/result backend pulled from env, applies the shared routing table, and
returns the instance. Both the API and the workers call it -- the API in
``send_task`` mode (publisher), the worker as ``-A workers.worker_app``.
"""

from __future__ import annotations

import os
from typing import Any

from celery import Celery
from celery.signals import task_failure, task_postrun, task_prerun
from loguru import logger

# ---------------------------------------------------------------------------
# Canonical task name -> queue mapping.
#
# Keep in lockstep with the tables in ``apps/workers/README.md`` and
# ``ARCHITECTURE.md``. The API imports ``TASK_ROUTES`` to discover where each
# task lives without depending on the worker package.
# ---------------------------------------------------------------------------

QUEUE_NAMES: tuple[str, ...] = (
    "download",
    "whisperx",
    "diarization",
    "yolo",
    "qwen",
    "render-prep",
    "render",
    "discovery",
    "reels",
    "facebook",
    "image_posts",
)

TASK_ROUTES: dict[str, dict[str, str]] = {
    # Stage 1 -- ingest
    "download.fetch_source": {"queue": "download"},
    # Stage 2 -- ASR
    "whisperx.transcribe": {"queue": "whisperx"},
    "asr.transcribe": {"queue": "whisperx"},  # legacy alias
    # Stage 3 -- diarization
    "diarization.diarize": {"queue": "diarization"},
    # Stage 4 -- vision
    "yolo.analyze": {"queue": "yolo"},
    "vision.detect_objects": {"queue": "yolo"},  # legacy alias
    # Stage 5-7 -- LLM
    "qwen.analyze_content": {"queue": "qwen"},
    "qwen.detect_clips": {"queue": "qwen"},
    "qwen.plan_edit": {"queue": "qwen"},
    "qwen.rewrite_narrative": {"queue": "qwen"},
    "qwen.condense_subs": {"queue": "qwen"},
    "qwen.repair_json": {"queue": "qwen"},
    # Stage 8 -- rendering
    "render.prepare_assets": {"queue": "render-prep"},
    "render.render_clip": {"queue": "render"},
    "render.generate_thumbnail": {"queue": "render-prep"},
    # Orchestration / housekeeping
    "pipeline.advance_job": {"queue": "qwen"},  # cpu queue; cheap stage updates
    # Facebook / discovery / publishing (Phase 2)
    "discovery.scan_sources": {"queue": "discovery"},
    "discovery.fetch_metadata": {"queue": "discovery"},
    "reels.generate_reel": {"queue": "reels"},
    "reels.process_content_source": {"queue": "reels"},
    "facebook.publish_reel": {"queue": "facebook"},
    "facebook.schedule_reel": {"queue": "facebook"},
    "facebook.refresh_token": {"queue": "facebook"},
    # Image posts (Phase 3)
    "image_posts.generate_for_pages": {"queue": "image_posts"},
    "image_posts.generate_one": {"queue": "image_posts"},
    "image_posts.regenerate_image": {"queue": "image_posts"},
    "image_posts.regenerate_caption": {"queue": "image_posts"},
    "image_posts.publish_one": {"queue": "image_posts"},
    "image_posts.publish_scheduled_image_posts": {"queue": "image_posts"},
}


def _env(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val else default


def make_celery(
    name: str = "shortform_factory",
    broker: str | None = None,
    backend: str | None = None,
    *,
    include: list[str] | None = None,
    extra_config: dict[str, Any] | None = None,
) -> Celery:
    """Build a configured Celery app.

    Parameters
    ----------
    name:
        Celery application name (appears in worker logs and result keys).
    broker:
        Broker URL. Defaults to ``CELERY_BROKER_URL`` env var.
    backend:
        Result backend URL. Defaults to ``CELERY_RESULT_BACKEND``.
    include:
        Module paths to autodiscover tasks from. The workers pass
        ``["apps.workers.tasks.*"]``; the API typically passes nothing because
        it only publishes by name.
    extra_config:
        Optional overrides merged into ``app.conf``.
    """
    broker = broker or _env("CELERY_BROKER_URL", "redis://redis:6379/1")
    backend = backend or _env("CELERY_RESULT_BACKEND", "redis://redis:6379/2")

    app = Celery(name, broker=broker, backend=backend, include=include or [])

    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Reliability defaults: redeliver on worker crash, no prefetch hoarding.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_track_started=True,
        result_expires=3600,
        # Routing
        task_routes=TASK_ROUTES,
        task_default_queue="download",
        # Soft/hard time limits guard against runaway jobs. Render is the
        # longest stage; bump it via extra_config if needed.
        task_soft_time_limit=int(_env("CELERY_TASK_SOFT_TIME_LIMIT", "1800")),
        task_time_limit=int(_env("CELERY_TASK_TIME_LIMIT", "3600")),
        # Broker connection robustness.
        broker_connection_retry_on_startup=True,
        broker_heartbeat=30,
    )

    if extra_config:
        app.conf.update(**extra_config)

    return app


def enqueue(app: Celery, task_name: str, *args: Any, **kwargs: Any) -> str:
    """Typed wrapper around ``send_task`` so callers never import task modules.

    Returns the Celery ``AsyncResult.id`` as a string. Honors :data:`TASK_ROUTES`;
    if the task name isn't in the table the caller gets an explicit error rather
    than a silent default-queue dispatch.
    """
    if task_name not in TASK_ROUTES:
        raise ValueError(
            f"Unknown task name '{task_name}'. Add it to TASK_ROUTES in "
            "packages/queue/task_queue/celery_app.py first."
        )
    queue = TASK_ROUTES[task_name]["queue"]
    result = app.send_task(task_name, args=args, kwargs=kwargs, queue=queue)
    logger.debug("enqueued task={} queue={} id={}", task_name, queue, result.id)
    return result.id


def register_signal_logging(app: Celery) -> None:
    """Hook Celery's task lifecycle signals into loguru.

    Persistent DB-side logging (the ``logs`` table) lives in the worker's
    ``BaseTask`` because it needs an active DB session per task; this hook only
    handles the structured loguru side, which is always safe.
    """

    @task_prerun.connect(weak=False)
    def _on_prerun(  # type: ignore[no-untyped-def]
        sender=None, task_id=None, task=None, args=None, kwargs=None, **_: object
    ):
        logger.info(
            "task.prerun name={} id={} args={} kwargs={}",
            getattr(task, "name", "?"),
            task_id,
            args,
            kwargs,
        )

    @task_postrun.connect(weak=False)
    def _on_postrun(  # type: ignore[no-untyped-def]
        sender=None,
        task_id=None,
        task=None,
        state=None,
        retval=None,
        **_: object,
    ):
        logger.info(
            "task.postrun name={} id={} state={}",
            getattr(task, "name", "?"),
            task_id,
            state,
        )

    @task_failure.connect(weak=False)
    def _on_failure(  # type: ignore[no-untyped-def]
        sender=None,
        task_id=None,
        exception=None,
        traceback=None,
        einfo=None,
        **_: object,
    ):
        logger.error(
            "task.failure name={} id={} exc={}",
            getattr(sender, "name", "?"),
            task_id,
            exception,
        )

    # Suppress "function defined but not used" linters: signal.connect keeps refs.
    _ = (_on_prerun, _on_postrun, _on_failure)
