# packages/ffmpeg

**Status:** placeholder. Owner: Phase-2 workers agent.

Wraps ffmpeg + OpenCV operations needed by the render worker:

- `cut(src, start, end, dst)` -- frame-accurate cut.
- `crop_vertical(src, dst, crop_plan)` -- 9:16 crop driven by a
  `shared_py.llm_contracts.CropPlan` (static, center, smart, or
  face-tracked via OpenCV).
- `burn_subtitles(src, ass_path, dst, style)` -- libass subtitle burn-in.
- `apply_visual_effects(src, effects, dst)` -- zoom, flash, freeze frames,
  configurable per `VisualEffect`.
- `extract_thumbnail(src, t, dst)`.
- A typed `FfmpegCommand` builder that records the final command string into
  `render_tasks.ffmpeg_command` for debuggability.

All functions must report progress via a callback so the render worker can
emit `clip.rendering` SSE events.
