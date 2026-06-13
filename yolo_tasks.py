"""Stage 4: ``yolo.analyze``.

Sampled-frame YOLOv11 detection. Writes a ``yolo_json`` asset with per-frame
detections + a focal-point track for smart-crop.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from loguru import logger

from shared_py.enums import AssetKind, JobStatus
from storage import get_storage
from task_queue import BaseTask

from apps.workers.event_publisher import publish_stage_complete
from apps.workers.tasks._helpers import (
    insert_asset,
    publish_progress,
    stage_pct,
    update_job_stage,
)
from apps.workers._app import celery


@celery.task(
    name="yolo.analyze",
    base=BaseTask,
    bind=True,
    queue="yolo",
)
def analyze(self: BaseTask, job_id: str, video_path: str) -> dict[str, Any]:
    """Run YOLO + smart-crop hint extraction, then enqueue Qwen analysis."""
    setattr(self, "stage_name", "analyzing")
    update_job_stage(
        job_id=job_id,
        new_status=JobStatus.ANALYZING,
        stage_name="analyzing",
        pct=stage_pct("analyzing", 0.6),
    )

    from services.yolo import analyze as run_yolo

    t0 = time.monotonic()
    analysis = run_yolo(video_path)
    publish_progress(
        job_id, stage="analyzing", pct=stage_pct("analyzing", 0.9)
    )

    storage = get_storage()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp.write(json.dumps(analysis.to_json()).encode("utf-8"))
        tmp_path = Path(tmp.name)
    try:
        key = f"{job_id}/{AssetKind.YOLO_JSON.value}/yolo.json"
        put = storage.put(key, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    asset_id = insert_asset(
        job_id=job_id,
        kind=AssetKind.YOLO_JSON,
        path=put.path,
        size_bytes=put.size_bytes,
        mime="application/json",
        metadata=analysis.summary,
    )

    publish_progress(
        job_id, stage="analyzing", pct=stage_pct("analyzing", 1.0)
    )
    logger.info(
        "yolo.analyze: job={} detections={} asset={}",
        job_id,
        len(analysis.detections),
        asset_id,
    )

    # ---- structured stage_complete payload ---------------------------------
    # Bucket detections into person / face / other so the frontend can show a
    # one-liner without re-parsing the per-frame array.
    cls_counts: Counter[str] = Counter(d.cls for d in analysis.detections)
    detections_bucket = {
        "person": int(cls_counts.get("person", 0)),
        "face": int(cls_counts.get("face", 0)),
        "other": int(
            sum(c for cls, c in cls_counts.items() if cls not in ("person", "face"))
        ),
    }
    # Friendly engine label: basename of ``YOLO_MODEL_PATH`` minus the .pt
    # suffix so we render "yolo11n" instead of a long absolute path. Falls
    # back to "YOLOv11n" when no model env is set.
    raw_model = os.environ.get("YOLO_MODEL_PATH", "YOLOv11n")
    engine_label = Path(raw_model).stem if "/" in raw_model or raw_model.endswith(".pt") else raw_model
    publish_stage_complete(
        job_id,
        {
            "stage": "vision",
            "engine": engine_label,
            "device": os.environ.get("YOLO_DEVICE", "cuda"),
            "sampled_frames": int(len(analysis.focal_track)),
            "sample_fps": float(analysis.sample_fps),
            "detections": detections_bucket,
            "focal_regions_count": int(len(analysis.focal_track)),
            "elapsed_s": round(time.monotonic() - t0, 2),
        },
    )

    celery.send_task(
        "qwen.analyze_content",
        kwargs={"job_id": job_id, "video_path": video_path},
        queue="qwen",
    )

    # Release GPU memory so the next GPU-bound stage doesn't OOM on a single
    # GPU box. Safe no-op without torch/CUDA.
    _release_gpu_memory()

    return {"yolo_asset_id": asset_id, "detection_count": len(analysis.detections)}


def _release_gpu_memory() -> None:
    """Best-effort ``torch.cuda.empty_cache``; no-op when torch/CUDA is absent."""
    try:
        import torch  # type: ignore[import-not-found]

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("torch.cuda.empty_cache skipped: {}", exc)
