# apps/workers

Celery worker application for the shortform-factory pipeline.

## Module layout

```
apps/workers/
  worker_app.py            # Celery app instance (canonical name)
  celery_app.py            # re-export shim so `-A workers.celery_app` keeps working
  db_ctx.py                # sync wrapper around AsyncSession (asyncio.run pattern)
  event_publisher.py       # sync publish_sync() to Redis pub/sub
  tasks/
    __init__.py
    _helpers.py            # shared stage helpers (progress, asset writes)
    download.py            # download.fetch_source
    whisperx_tasks.py      # whisperx.transcribe
    diarization_tasks.py   # diarization.diarize
    yolo_tasks.py          # yolo.analyze
    qwen_tasks.py          # qwen.{analyze_content,detect_clips,plan_edit,repair_json}
    render_tasks.py        # render.render_clip, render.generate_thumbnail
    pipeline_tasks.py      # pipeline.advance_job
  tests/
    conftest.py            # all MOCK_* on, fakeredis, in-memory sqlite
    test_download_task.py
    test_qwen_pipeline.py
    test_render_task.py
  Dockerfile               # python:3.11-slim + ffmpeg (CPU queues)
  Dockerfile.gpu           # nvidia/cuda:12.1.0-runtime + torch/whisperx/pyannote/ultralytics
```

## How tasks talk to the database

Celery's default pool is **prefork**, which forks the worker per task -- a fresh
OS process with no inherited event loop. To talk to async SQLAlchemy from a
sync Celery task we use a small wrapper in `db_ctx.py`:

```python
from apps.workers.db_ctx import run_async

async def _body(session: AsyncSession) -> None:
    ...

run_async(_body)
```

`run_async` opens a per-call engine + session, hands you the session,
commits or rolls back automatically, and disposes the engine on exit. We do
**not** keep a long-lived engine because forks share file descriptors with the
parent and that breaks asyncpg.

This is one of two reasonable patterns; the other is `anyio.from_thread.run`
which needs an outer event loop (e.g. eventlet/gevent pool). We picked the
sync-wrap approach because it's the safest default for prefork workers.

## How tasks publish events

`apps/workers/event_publisher.publish_sync(job_id, event)` writes one message
to Redis pub/sub channel `job:{job_id}`. The API's SSE handler subscribes to
that channel and forwards events to the browser. The event model is one of
the pydantic classes in `shared_py.events`.

## Mock mode

All five expensive stages honor a `MOCK_*` env var that short-circuits to a
deterministic fixture:

| Env             | Stage         | Behaviour when set                        |
|-----------------|---------------|-------------------------------------------|
| `MOCK_DOWNLOAD` | downloader    | writes a 5s black mp4 (or empty stub)     |
| `MOCK_ASR`      | whisperx      | returns a 400-word, 600s fixture          |
| `MOCK_LLM`      | qwen          | returns 3 clips + a stable edit plan      |
| `MOCK_YOLO`     | yolo          | centered face track across 600s           |
| `MOCK_RENDER`   | render        | writes a black mp4 (or a sentinel)        |

With **all** flags on, the entire 11-task pipeline runs end-to-end against an
in-memory sqlite + fakeredis in under ~10 seconds on a tiny dev machine.

## Running a worker locally

The compose files set everything up automatically (`make up`). For
debugging a single queue from the host shell:

```bash
# Activate your venv first.
export PYTHONPATH=$(pwd):$(pwd)/packages/shared-py:$(pwd)/packages/database:\
$(pwd)/packages/queue:$(pwd)/packages/storage:$(pwd)/packages/ai:\
$(pwd)/packages/ffmpeg:$(pwd)/services

export CELERY_BROKER_URL=redis://localhost:6379/1
export CELERY_RESULT_BACKEND=redis://localhost:6379/2
export DATABASE_URL=postgresql+asyncpg://factory:factory@localhost:5432/factory

# CPU queues -- one worker, four greenlets.
celery -A apps.workers.worker_app worker -Q download,qwen,render-prep -l INFO --concurrency=4

# Just the GPU queues -- single greenlet to avoid OOM.
celery -A apps.workers.worker_app worker -Q whisperx,diarization,yolo,render -l INFO --concurrency=1

# Dev mode -- no GPUs, no network, no ffmpeg required.
MOCK_DOWNLOAD=1 MOCK_ASR=1 MOCK_LLM=1 MOCK_YOLO=1 MOCK_RENDER=1 \
celery -A apps.workers.worker_app worker -Q download,whisperx,diarization,yolo,qwen,render-prep,render \
  -l INFO --concurrency=2
```

## Tests

```bash
cd apps/workers
PYTHONPATH=...as above... pytest -q
```

`tests/conftest.py` flips every `MOCK_*` flag on, swaps the Redis client out
for `fakeredis`, and wires the DB to an in-memory sqlite (with the same ORM
models -- aiosqlite is sufficient for the schema since the worker code never
relies on Postgres-only types beyond JSONB, which sqlite happily stores as
JSON text).

## Failure handling

Every task inherits `task_queue.BaseTask`, which:

1. Auto-retries on `Exception` up to 3 times, exponential backoff, jitter.
2. On final failure, calls `db_ctx.mark_job_failed_sync(...)` to set
   `jobs.status = failed` + `error_message`, and publishes a `job.failed`
   SSE event.
3. Per-clip render failures emit `clip.failed` and let `_finalize_job_if_done`
   decide whether the job overall succeeded (at least one clip rendered) or
   failed (no clips rendered).

## Idempotency

Tasks are idempotent on `job_id`:

* Asset rows are deduped by `(job_id, kind, path)`.
* The transcripts row is upserted by `job_id` (unique index).
* `clips` are upserted by `(job_id, clip_index)` using Postgres
  `ON CONFLICT` (with a select-then-update fallback for the sqlite tests).
* `render_tasks` are append-only by design but `_finalize_job_if_done` is a
  pure read-then-set that converges to the same final state.
