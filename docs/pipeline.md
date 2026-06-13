# End-to-end pipeline

Step-by-step data flow for one job. Each arrow is a Celery task transition;
each "writes" item lands in either the DB or the storage backend.

```
POST /jobs (source_url)
  |
  v
jobs row inserted (status=queued)        SSE: job.created
  |
  v
download.fetch_source                    queue=download   stage=downloading
  reads:  source_url
  writes: source_video, source_thumbnail assets
          jobs.source_metadata (title, duration, ...)
  SSE:    job.progress (pct from yt-dlp hook)
  |
  v
asr.transcribe                           queue=whisperx   stage=transcribing
  reads:  source_video (or extracted audio)
  writes: transcripts row, transcript_json asset
  SSE:    job.progress
  |
  v
diarization.diarize (if ENABLE_DIARIZATION) queue=diarization stage=analyzing
  reads:  source_audio
  writes: speakers rows, diarization_json asset
  |
  v
vision.detect_objects                    queue=yolo       stage=analyzing
  reads:  source_video (sampled frames)
  writes: yolo_json asset
  |
  v
qwen.detect_clips                        queue=qwen       stage=clip_planning
  reads:  transcript_json + diarization_json + yolo_json (summary)
  writes: analysis_results row, clips rows (status=planned), analysis_json asset
  SSE:    clip.planned per clip
  |
  +-- fan out, one per clip ----+
  |                              |
  v                              v
qwen.plan_edit (clip N)   ...    qwen.plan_edit (clip M)   queue=qwen
  writes: clips.edit_plan, edit_plan_json asset
  (may also call qwen.rewrite_narrative and qwen.condense_subs internally)
  |
  +-- join ----------------------+
  |
  v
render.prepare_assets                    queue=render-prep
  reads:  edit plans + condensed subs
  writes: subtitle_ass assets
  |
  +-- fan out, one per clip ----+
  |                              |
  v                              v
render.render_clip (N)    ...    render.render_clip (M)    queue=render  stage=rendering
  reads:  source_video, edit_plan, subtitle_ass, yolo face tracks
  writes: render_tasks row, clip_video, clip_thumbnail assets
          clips.status=rendered
  SSE:    clip.rendering (pct), clip.rendered
  |
  +-- join ----------------------+
  |
  v
jobs.status = completed | failed         SSE: job.completed | job.failed
jobs.finished_at = now
```

## Concurrency and ordering

- Pipeline-level steps (download -> ASR -> diarization -> YOLO -> clip
  planning) are strictly sequential per job.
- Per-clip steps (`qwen.plan_edit`, `render.render_clip`) fan out and run in
  parallel up to the worker concurrency limit.
- Diarization and YOLO are independent of each other and could run in parallel
  on a multi-GPU host; for the Phase-2 implementation we keep them sequential
  to keep memory predictable.

## Status transitions

```
queued -> downloading -> transcribing -> analyzing -> clip_planning -> rendering -> completed
                                                                                \-> failed
```

A transition that runs into an error sets `status=failed` directly from the
current stage. Per-clip failures emit `clip.failed` but do not fail the job
unless **all** clips fail.

## Progress accounting

`jobs.progress_pct` is updated by the active worker. Suggested weights:

| stage          | window of `progress_pct` |
|----------------|--------------------------|
| downloading    | 0  - 15                  |
| transcribing   | 15 - 40                  |
| analyzing      | 40 - 55                  |
| clip_planning  | 55 - 65                  |
| rendering      | 65 - 100                 |

Workers should emit `job.progress` frequently enough for a smooth UI bar but
not more than 4Hz.
