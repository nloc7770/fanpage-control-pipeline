"""Orchestration tasks (light-weight stage advances)."""

from __future__ import annotations

from typing import Any

from loguru import logger

from shared_py.enums import JobStatus
from task_queue import BaseTask

from apps.workers.tasks._helpers import (
    publish_progress,
    stage_pct,
    update_job_stage,
)
from apps.workers._app import celery


@celery.task(
    name="pipeline.advance_job",
    base=BaseTask,
    bind=True,
    queue="qwen",  # cheap; runs on CPU queue
)
def advance_job(
    self: BaseTask,
    job_id: str,
    to_status: str,
    stage: str,
    pct: float | None = None,
) -> dict[str, Any]:
    """Set ``jobs.status`` / ``current_stage`` / ``progress_pct`` and publish events.

    Designed to be called from the API between stages, or from a worker task
    when explicit orchestration is preferable to embedding the side-effect in
    the stage task itself.
    """
    setattr(self, "stage_name", stage)
    try:
        new_status = JobStatus(to_status)
    except ValueError as exc:
        raise ValueError(f"unknown JobStatus: {to_status!r}") from exc

    effective_pct = pct if pct is not None else stage_pct(stage, 0.0)
    update_job_stage(
        job_id=job_id,
        new_status=new_status,
        stage_name=stage,
        pct=effective_pct,
    )
    publish_progress(job_id, stage=stage, pct=effective_pct)
    logger.info(
        "pipeline.advance_job: job={} -> {} stage={} pct={}",
        job_id,
        new_status,
        stage,
        effective_pct,
    )
    return {"status": new_status.value, "stage": stage, "pct": effective_pct}
