# services/yolo

**Status:** placeholder. Owner: Phase-2 workers agent.

YOLOv11 visual analysis. One task: `vision.detect_objects`.

Responsibilities:

- Load `YOLO_MODEL_PATH` on `YOLO_DEVICE`.
- Sample frames at e.g. 1-2 fps; run detection per frame.
- For each detection record `{t, class, conf, bbox}`; aggregate per-class
  presence timelines and identify face/person tracks (used later by
  `crop_plan.mode=track_face`).
- Persist `yolo_json` asset with both the raw frame detections and the
  aggregated tracks.
- On `MOCK_YOLO=1`, emit a fixture with one face track centered on screen.

Emits `job.progress` (stage=`analyzing`). On completion, enqueues
`qwen.detect_clips`.
