# Implementation Roadmap

The 18 steps below are the canonical implementation order. Phase 1 (this commit)
delivers steps 1-3 plus the contracts/skeleton for everything else. Phase 2
agents (frontend, api, workers) own the remaining steps as noted.

## Phase 1 -- Foundation (done)

1. Monorepo skeleton + tooling
   - pnpm workspace, pyproject for python packages, Makefile, docker-compose,
     .env.example, .gitignore.

2. Shared contracts
   - `packages/shared-py` (pydantic schemas, enums, SSE events, LLM JSON
     contracts) and the mirrored `packages/shared-types` for the frontend.

3. Database schema + migrations
   - SQLAlchemy 2.x async models in `packages/database` and the initial Alembic
     migration `0001_initial.py` covering all tables, enums, indices.

## Phase 2 -- API (owner: api agent)

4. FastAPI app skeleton in `apps/api`
   - Settings (pydantic-settings) reading `.env`, structured loguru logger,
     async DB session dependency, Redis client, Celery client.

5. REST endpoints
   - `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/clips`,
     `GET /assets/{id}/download`. See `docs/api-contracts.md` for shapes.

6. SSE endpoint
   - `GET /jobs/{id}/events` -- subscribe to Redis pub/sub, replay recent
     `logs` on connect, stream events.

7. Job enqueue
   - On `POST /jobs`, write a `jobs` row, enqueue `download.fetch_source`,
     publish `job.created`.

## Phase 3 -- Workers (owner: workers agent)

8. Celery app + queues
   - `packages/queue` -- Celery factory, queue routing, beat config.
     One container subscribes to CPU queues, the GPU compose override
     subscribes to GPU queues.

9. Storage backend
   - `packages/storage` -- `StorageBackend` ABC, `LocalStorage`,
     `S3Storage` skeleton, `put/get/url/delete`.

10. Downloader worker (`services/downloader`)
    - yt-dlp wrapper, writes source_video + source_thumbnail assets, records
      `source_metadata`.

11. WhisperX worker (`services/whisperx`)
    - word-level transcript, writes `transcripts` row + `transcript_json`
      asset.

12. Diarization worker (`services/diarization`)
    - pyannote, writes `speakers` rows + `diarization_json` asset. Honors
      `ENABLE_DIARIZATION`.

13. YOLO worker (`services/yolo`)
    - YOLOv11, writes `yolo_json` asset.

14. Qwen client + viral detection (`services/qwen`)
    - OpenAI-compatible client against `QWEN_BASE_URL`. Implements the 5
      prompt templates in `docs/llm-prompts.md`. Writes `analysis_results`.

15. Edit planner
    - Per detected clip, call Qwen to produce an `EditPlan` JSON. Writes
      `clips` rows.

16. Renderer (`services/rendering`)
    - ffmpeg + OpenCV: cut, 9:16 crop, burn ASS subtitles, overlays. Writes
      `render_tasks` + `clip_video` + `clip_thumbnail` assets.

## Phase 4 -- Frontend (owner: frontend agent)

17. Next.js UI
    - Submit URL, jobs list, job detail with live progress (SSE), clip
      gallery, per-clip preview + download. shadcn/ui components, TanStack
      Query for REST, native EventSource (or a tiny wrapper) for SSE.

## Phase 5 -- Hardening

18. Observability + production hardening
    - Structured logs to stdout (loguru -> JSON), Prometheus metrics on the
      API, healthchecks, rate-limit on `POST /jobs`, S3 backend wired,
      cookies for yt-dlp, GPU autoscaling notes.
