# Shortform Factory — Mô tả dự án

## Mục đích
Pipeline AI tự động: paste URL video dài → trả về N clip dọc 9:16 viral-ready, có sub song ngữ EN+VI, đã cắt highlight, đã caption, đã smart-crop. Built cho TikTok/Reels/Shorts workflow.

## Pipeline 6 stage

| # | Stage | Engine | Hardware |
|---|---|---|---|
| 1 | downloading | yt-dlp (ép codec h264) | CPU |
| 2 | transcribing | WhisperX large-v3 | GPU |
| 3 | analyzing | YOLOv11n + pyannote (optional) | GPU |
| 4 | clip_planning | Qwen 2.5 32b q4 | GPU (remote) |
| 5 | rendering | ffmpeg + OpenCV | CPU |
| 6 | completed | — | — |

Mỗi transition: ghi `logs`, update `jobs.current_stage` + `progress_pct`, publish SSE qua Redis pub/sub.

## Tech stack

- **Frontend** — Next.js 14, TS, Tailwind, shadcn/ui, TanStack Query, native EventSource cho SSE
- **API** — FastAPI + async SQLAlchemy 2.x + Alembic, port 8080
- **Worker** — Celery prefork concurrency=1, queue routing theo hardware: `download/qwen/render-prep` (CPU) vs `whisperx/diarization/yolo/render` (GPU)
- **DB** — Postgres 16 (`jobs`, `clips`, `assets`, `transcripts`, `speakers`, `analysis_results`, `render_tasks`, `logs`)
- **Cache/queue** — Redis 7 (broker DB1, result DB2, pub/sub DB0)
- **Storage** — `StorageBackend` ABC: `LocalStorage` (`_storage_data/`) hoặc `S3Storage` (sẵn sàng), key `{job_id}/{kind}/{filename}`
- **LLM** — OpenAI-compatible HTTP, hiện trỏ `http://192.168.50.245:11436/v1` model `qwen2.5:32b-instruct-q4_K_M` chạy split 2× RTX 4090

## Repo layout

```
apps/
  api/          FastAPI, routers /jobs /assets /discover, SSE /jobs/{id}/events
  workers/      Celery app, tasks/{download,whisperx,yolo,qwen,render}_tasks.py
  frontend/     Next.js, /jobs list, /jobs/[id] detail (SSE), /jobs/[id]/clips/[clipId]
packages/
  shared-py/    Pydantic schemas, enums (JobStatus, ClipStage), SSE event types, LLM contracts
  shared-types/ TS DTOs mirror shared-py
  database/     SQLAlchemy models + migrations (Alembic)
  ai/           Qwen client + prompt templates (viral_clip_detection, edit_plan, …)
  ffmpeg/       crop.py (fit + blur 9:16), subtitles.py (dual EN+VI), effects.py (zoom punches), pipeline.py (render_clip orchestrator)
  storage/      LocalStorage, S3Storage
  queue/        Celery factory + queue routing
services/
  downloader/   yt-dlp programmatic wrapper
  whisperx/     ASR runner
  diarization/  pyannote
  yolo/         YOLOv11n + focal_track builder
  qwen/         runner.py: detect_clips, plan_edit, _normalise_clip_highlights, quality gate
  rendering/    runner.py
infra/          postgres init.sql, redis.conf
scripts/        bootstrap.sh, restart_worker_local_llm.sh, setup_local_llm.sh
docs/           api-contracts, llm-prompts, pipeline
```

## Hạ tầng đang chạy

- **Local (ubuntu-duyloc)** — API uvicorn:8080, Celery worker prefork, Postgres:5432, Redis:6379, Frontend Next.js:3000
- **Remote .245 (lucas-WS-C621E)** — Ollama 0.24 user-mode port 11436, qwen2.5:32b-q4 split 2× RTX 4090 (~16GB + 13GB VRAM)
- **Tunnels Cloudflare** — API và FE expose ra ngoài qua trycloudflare.com

## Database schema (chính)

- `jobs(id, source_url, status, current_stage, progress_pct, error_message, source_metadata, created_at, finished_at)`
- `clips(id, job_id, clip_index, start_time, end_time, virality_score, main_hook, emotional_peak, retention_reason, topics, target_style, title, narrative_script_vi, edit_plan, status)`
- `assets(id, job_id, kind, path, size_bytes, mime, metadata)` — `kind ∈ {source_video, source_thumbnail, transcript_json, yolo_json, analysis_json, edit_plan_json, clip_video, clip_thumbnail, ...}`
- `render_tasks(id, clip_id, status, output_asset_id, ...)` — track render attempts
- `logs(id, job_id, clip_id, level, stage, message, created_at)` — pipeline structured logs
- `speakers(id, job_id, speaker_id, timeline)` — diarization output

## SSE event taxonomy

- `job.created`, `job.progress`, `job.stage_changed`, `job.completed`, `job.failed`
- `clip.planned`, `clip.rendered`, `clip.failed`
- `stage.complete` (mỗi stage publish 1 frame summary)

API endpoint `GET /jobs/{id}/events` mở long-lived SSE, replay logs cũ + stream events mới qua Redis pub/sub.

## Quality features

### Clip detection quality gate
Sau khi LLM trả `ClipDetectionResponse`, validate 3 gate:
1. `virality_score != 5.0` cho ≥50% clips (5 là default placeholder)
2. ≥60% clips có `main_hook` không rỗng
3. Source >5min: span(start_times) >5% duration (chống sliding-window artifact)

Fail → retry 1 lần với prompt prefix `"PREVIOUS RESPONSE WAS REJECTED — try harder"`. Retry fail → raise `LLMQualityError`.

### Vietnamese narrative anti-literal
Prompt `edit_plan_messages` thêm:
- Rule REWRITE not translate literally
- Tone shortform/TikTok (mình/họ/chúng ta thay vì tôi formal)
- Hook rule: first segment phải curiosity hook
- 5 trap detection (e.g. "game fish" ≠ "cá chơi game")
- 2-3 example EN → BAD VN → GOOD VN

Post-LLM warning detect literal patterns rõ.

### Smart-crop 9:16
**Fit + blurred background composer**:
1. Foreground: scale full source 1920×1080 → 1080×608, place centered
2. Background: scale source fill 1080×1920 cropped + boxblur=20
3. Compose foreground over background

→ Toàn cảnh source được giữ nguyên (không cắt ai), top/bottom là blur của chính source.

### Subtitle burn-in
- VI **vàng đậm 68px**, bold, outline 4px, shadow 3 (primary)
- EN **trắng 38px**, không bold (secondary)
- Margin VI 200px from bottom, EN 120px from bottom

### Zoom
Tắt hoàn toàn (amplitude 0%) — không zoom-in punches, không zoom-out beats, không auto-fallback.

## Frontend reactivity

- **Job detail page** — useJobSse(jobId) opens EventSource, on event invalidate `['job']`, `['clips']`, `['job-artifact', jobId]` (predicate), `['job-logs']`, `['clip', jobId, clipId]`
- **List page** — refetchInterval poll 3s khi có ≥1 job in-flight (queued/downloading/transcribing/analyzing/clip_planning/rendering), dừng khi tất cả terminal
- **Hero stats** — poll 5s khi in-flight, 60s khi idle
- **Clip detail page** — mount useJobSse cùng connection (deduplicate qua browser)
- **Polling fallback** — useJob/useClips refetchInterval 5s nếu SSE drops mid-job
- **refetchOnWindowFocus: true** mọi nơi

## Configuration

`.env` chính:
```
QWEN_BASE_URL=http://192.168.50.245:11436/v1
QWEN_MODEL=qwen2.5:32b-instruct-q4_K_M
QWEN_TIMEOUT_S=600
QWEN_MAX_TOKENS=8192
WHISPERX_MODEL=large-v3
WHISPERX_DEVICE=cuda
YOLO_MODEL_PATH=…/yolov11n.pt
YOLO_DEVICE=cuda
DOWNLOAD_FORMAT="bestvideo[vcodec^=avc1][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best"
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=postgresql+asyncpg://factory:factory@localhost:5432/factory
STORAGE_BACKEND=local
MOCK_*=0  # all real, no mocks
```

## Failure model

- Worker exception → `jobs.status=failed`, ghi `error_message`, emit `job.failed`
- Per-clip fail → `clip.failed` nhưng job vẫn `completed` nếu ≥1 clip render xong
- LLM JSON parse fail → route qua `qwen.repair_json`
- yt-dlp Postprocessing fail thường do network stall hoặc disk full
- cv2 không decode được codec → `RuntimeError` rõ ràng (gợi ý ép `vcodec^=avc1`)

## Flexible clip count

`target_clip_count` scale theo source duration: ~1 clip / 3 phút source, floor=3, không cap. Override được qua `yolo_summary.target_clip_count`. Source 21 phút → ~7 clips; source 5 phút → 3 clips.

## Performance

- yt-dlp h264 21-phút: ~10-20s
- WhisperX large-v3 21-phút: ~20-30s (cuda fp16)
- YOLO sample_fps=2 trên 21 phút: ~30-60s
- Qwen 32b detect_clips warm: ~2-3 phút
- Qwen 32b plan_edit per clip: ~20-30s × N
- Render per clip: ~20-50s (1080p, fit+blur, dual subs)
- **Tổng E2E** với source 21 phút, 7 clips: ~10-15 phút
