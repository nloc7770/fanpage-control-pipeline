# services/rendering

**Status:** placeholder. Owner: Phase-2 workers agent.

ffmpeg + OpenCV rendering. Tasks:

- `render.prepare_assets` -- CPU. For each clip:
  - Generate ASS subtitles from words within the clip range, applying
    `EditPlan.subtitle_style` and the condensed lines from `qwen.condense_subs`.
  - Persist `subtitle_ass` asset.
  - Enqueue `render.render_clip`.

- `render.render_clip` -- GPU. For each clip:
  - Open a `render_tasks` row (`status=rendering`, `started_at=now`).
  - Cut, 9:16 crop per `CropPlan`, burn ASS subtitles, apply
    `visual_effects` and `pattern_interrupts` via `packages/ffmpeg`.
  - Persist `clip_video` + `clip_thumbnail` assets.
  - Update `clips.status=rendered`, `render_tasks.status=rendered`,
    `output_asset_id`, `finished_at`, `progress_pct=100`.
  - Emit `clip.rendering` progress events and final `clip.rendered`.
  - On exception: `clip.status=failed`, `clip.failed` SSE event.

When the last clip completes, mark the job `completed` and emit
`job.completed`. If no clips rendered successfully, mark `failed`.

`MOCK_RENDER=1` skips actual ffmpeg and writes a 1-second black mp4 fixture.
