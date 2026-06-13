"""Per-frame YOLO analysis with smart-crop focal points.

The runner lazy-imports ``ultralytics`` and only opens the model when needed.
Mock mode returns a centered face track that the rendering stage can consume
without changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass(slots=True)
class YoloDetection:
    """One detection in one sampled frame."""

    t: float
    cls: str
    conf: float
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels


@dataclass(slots=True)
class YoloAnalysis:
    """Aggregate analysis used by smart-crop and the LLM stage."""

    frame_size: tuple[int, int]
    sample_fps: float
    detections: list[YoloDetection]
    focal_track: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "frame_size": list(self.frame_size),
            "sample_fps": self.sample_fps,
            "detections": [
                {
                    "t": d.t,
                    "cls": d.cls,
                    "conf": d.conf,
                    "bbox": list(d.bbox),
                }
                for d in self.detections
            ],
            "focal_track": self.focal_track,
            "summary": self.summary,
        }


def analyze(video_path: str | Path, *, sample_fps: float = 2.0) -> YoloAnalysis:
    """Run YOLO on sampled frames. Honors ``MOCK_YOLO``."""
    if os.environ.get("MOCK_YOLO", "0") == "1":
        return _mock_analysis(sample_fps=sample_fps)

    try:
        from ultralytics import YOLO  # type: ignore[import-untyped]
        import cv2  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - install-time error
        raise RuntimeError(
            "ultralytics + opencv are required for YOLO. Install in the GPU "
            "worker image or set MOCK_YOLO=1 for dev mode."
        ) from exc

    # Ultralytics ships YOLO11 weights as `yolo11n.pt` (no 'v'); the previous
    # default `yolov11n.pt` does not exist on the model hub.
    model_path = os.environ.get("YOLO_MODEL_PATH", "yolo11n.pt")
    device = os.environ.get("YOLO_DEVICE", "cuda")
    logger.info("yolo: loading model={} device={}", model_path, device)
    model = YOLO(model_path)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cv2 cannot open {video_path}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / src_fps if src_fps > 0 else 0.0

    # Probe that cv2 can actually decode frames (AV1 / VP9 are often not
    # supported by the system OpenCV build even though the container opens).
    # Read the very first frame; a zero-size frame means the codec is
    # unsupported and YOLO would silently produce 0 detections.
    ret_probe, frame_probe = cap.read()
    if not ret_probe or frame_probe is None or frame_probe.size == 0:
        cap.release()
        raise RuntimeError(
            f"cv2 opened {video_path} but cannot decode frames "
            f"(width={width}, height={height}, fps={src_fps:.1f}). "
            "The codec is likely AV1 or VP9 which this OpenCV build does not "
            "support. Re-download with DOWNLOAD_FORMAT=bestvideo[vcodec^=avc1] "
            "to force h264, then re-run YOLO."
        )
    # Rewind so the main loop processes from frame 0.
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    step = max(1, int(src_fps / sample_fps))
    detections: list[YoloDetection] = []
    focal_track: list[dict[str, Any]] = []

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % step == 0:
            t = frame_idx / src_fps
            results = model.predict(frame, device=device, verbose=False)
            cxs: list[float] = []
            cys: list[float] = []
            for r in results:
                for box in r.boxes:
                    xyxy = box.xyxy[0].cpu().numpy().tolist()
                    cls_id = int(box.cls[0])
                    cls_name = model.names.get(cls_id, str(cls_id))
                    conf = float(box.conf[0])
                    detections.append(
                        YoloDetection(
                            t=t,
                            cls=cls_name,
                            conf=conf,
                            bbox=(xyxy[0], xyxy[1], xyxy[2], xyxy[3]),
                        )
                    )
                    if cls_name in ("person", "face"):
                        cxs.append((xyxy[0] + xyxy[2]) / 2.0 / width)
                        cys.append((xyxy[1] + xyxy[3]) / 2.0 / height)
            cx = sum(cxs) / len(cxs) if cxs else 0.5
            cy = sum(cys) / len(cys) if cys else 0.5
            focal_track.append({"t": t, "cx": cx, "cy": cy})
        frame_idx += 1
    cap.release()

    summary = {
        "duration_s": duration,
        "frame_count": frame_count,
        "sampled_frames": len(focal_track),
        "face_present_pct": _face_presence(detections, len(focal_track)),
    }
    return YoloAnalysis(
        frame_size=(width, height),
        sample_fps=sample_fps,
        detections=detections,
        focal_track=focal_track,
        summary=summary,
    )


def _face_presence(detections: list[YoloDetection], sampled: int) -> float:
    if sampled == 0:
        return 0.0
    timestamps_with_face = {d.t for d in detections if d.cls in ("person", "face")}
    return len(timestamps_with_face) / sampled


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


def _mock_analysis(*, sample_fps: float = 2.0) -> YoloAnalysis:
    """Centered face track across a 600s window."""
    duration = 600.0
    sampled = int(duration * sample_fps)
    detections: list[YoloDetection] = []
    focal_track: list[dict[str, Any]] = []
    for i in range(sampled):
        t = i / sample_fps
        # A 200x200 face roughly centered on a 1920x1080 source.
        detections.append(
            YoloDetection(
                t=t,
                cls="person",
                conf=0.95,
                bbox=(860.0, 440.0, 1060.0, 640.0),
            )
        )
        focal_track.append({"t": t, "cx": 0.5, "cy": 0.5})
    return YoloAnalysis(
        frame_size=(1920, 1080),
        sample_fps=sample_fps,
        detections=detections,
        focal_track=focal_track,
        summary={
            "duration_s": duration,
            "frame_count": int(duration * 30),
            "sampled_frames": sampled,
            "face_present_pct": 1.0,
            "mock": True,
        },
    )
