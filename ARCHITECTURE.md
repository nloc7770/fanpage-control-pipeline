# Architecture

## High-level service diagram

```
                              +--------------+
                              |   Frontend   |   Next.js 14, SSE consumer
                              |  (Next.js)   |
                              +------+-------+
                                     |
                           REST + SSE (8080)
                                     |
                              +------v-------+
                              |     API      |   FastAPI, async SQLAlchemy
                              |  (FastAPI)   |   - validates input
                              +------+-------+   - enqueues jobs
                                     |           - streams SSE per job
                                     |
              +----------------------+----------------------+
              |                                             |
              |                                             |
       +------v-------+                              +------v-------+
       |   Postgres   |                              |    Redis     |
       |  (jobs,      |                              | (broker +    |
       |   clips,...) |                              |  result +    |
       +------+-------+                              |   pub/sub)   |
              ^                                      +------+-------+
              |                                             |
              |       +-------------------------------------+
              |       |                  Celery queues
              |       v
              |   +---+---------------------------------+
              |   |  cpu queue        gpu queue         |
              |   |  --------         ---------         |
              |   |  download         whisperx          |
              |   |  qwen             diarization       |
              |   |  render-prep      yolo              |
              |   |                   render            |
              |   +-------------------------------------+
              |       |              |             |
              |       v              v             v
              | +-----+-----+  +-----+-----+ +-----+-----+
              | | worker-cpu|  | worker-gpu| | worker-gpu|
              | | (n replicas) | (whisperx,| | (render,  |
              | |              |  yolo,    | |  ffmpeg)  |
              | |              |  diar,    | |           |
              | |              |  qwen     | |           |
              | |              |  client)  | |           |
              | +-----+-----+  +-----+-----+ +-----+-----+
              |       |              |             |
              |       +--------------+-------------+
              |                      |
              |              writes assets +
              |              progress events
              |                      |
              +----------------------+
                         |
                  +------v-------+
                  |   Storage    |  local FS now, S3 interface ready
                  | /data/storage|
                  +--------------+
```

## Pipeline stages

1. `downloading`     -- yt-dlp pulls source video + metadata + thumbnail
2. `transcribing`    -- WhisperX produces word-level transcript
3. `analyzing`       -- pyannote diarization + YOLO visual analysis
4. `clip_planning`   -- Qwen detects viral moments + drafts per-clip edit plans
5. `rendering`       -- ffmpeg + OpenCV cut, crop to 9:16, burn captions, overlays
6. `completed`       -- all clips rendered, assets indexed

Each transition writes a row to `logs`, updates `jobs.status` /
`jobs.current_stage` / `jobs.progress_pct`, and publishes an SSE event via Redis
pub/sub. The API multiplexes Redis pub/sub into per-job SSE streams.

## Celery queue topology

We split workers by hardware profile, not by stage, so a single host can run
all CPU-only stages without contending for GPU memory:

| queue        | tasks                                              | hardware |
|--------------|----------------------------------------------------|----------|
| `download`   | `download.fetch_source`                            | CPU      |
| `qwen`       | `qwen.detect_clips`, `qwen.plan_edit`,             | CPU      |
|              | `qwen.rewrite_narrative`, `qwen.condense_subs`,    |          |
|              | `qwen.repair_json`                                 |          |
| `render-prep`| `render.prepare_assets` (subtitle ASS gen, etc.)   | CPU      |
| `whisperx`   | `asr.transcribe`                                   | GPU      |
| `diarization`| `diarization.diarize`                              | GPU      |
| `yolo`       | `vision.detect_objects`                            | GPU      |
| `render`     | `render.render_clip`                               | GPU      |

The `worker-cpu` container subscribes to `download`, `qwen`, `render-prep`.
The `worker-gpu` container (via `docker-compose.gpu.yml`) subscribes to
`whisperx`, `diarization`, `yolo`, `render`.

## SSE flow

```
worker  --publish-->  redis channel "job:{job_id}"  --subscribe-->  api SSE handler  -->  browser
```

The API endpoint `GET /jobs/{id}/events` opens a long-lived SSE stream. It
subscribes to the Redis channel for that job, replays recent events from the
`logs` table on connect (so a late-attaching client sees prior progress), and
streams new events as they arrive. The full event taxonomy lives in
`packages/shared-py/shared_py/events.py` and is mirrored in
`packages/shared-types/src/index.ts`.

## Storage abstraction

All file writes go through `packages/storage`'s `StorageBackend` ABC, with a
`LocalStorage` implementation backed by `STORAGE_LOCAL_PATH` and an
`S3Storage` implementation behind the same interface. Assets are keyed by
`{job_id}/{kind}/{filename}` and tracked in the `assets` table. Workers never
write to local paths directly; they call `storage.put(...)` and persist the
returned `path`.

## Failure model

- Any worker exception transitions the job to `failed`, records `error_message`,
  and emits `job.failed`. Per-clip failures emit `clip.failed` but do not
  necessarily fail the job: the job is considered `completed` if at least one
  clip rendered, otherwise `failed`.
- LLM JSON parse failures route through a dedicated `qwen.repair_json` task
  (see `docs/llm-prompts.md`) before giving up.
- All long tasks emit `worker.heartbeat` every ~5s so the API can detect dead
  workers and resurface stuck jobs.
