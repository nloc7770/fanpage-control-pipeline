#!/usr/bin/env bash
# Restart celery worker pointing at the local Ollama LLM.
#
# Usage:
#   bash scripts/restart_worker_local_llm.sh
#
# Prereq: bash scripts/setup_local_llm.sh has already brought up Ollama
# on 127.0.0.1:11434 with qwen2.5:7b-instruct pulled.

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${PROJECT_ROOT}"

log() { echo "[restart-worker] $*"; }

# ---- 1. Kill current worker(s) ------------------------------------------
if pgrep -f 'celery.*worker' >/dev/null 2>&1; then
  log "Killing existing celery workers..."
  pgrep -f 'celery.*worker' | xargs -r kill -9
  sleep 3
fi

# ---- 2. Verify remote LLM is reachable ----------------------------------
if ! curl -s --max-time 5 http://192.168.50.245:11436/v1/models >/dev/null 2>&1; then
  log "ERROR: remote Ollama at 192.168.50.245:11436 is not responding."
  log "Check that Ollama is running on host04."
  exit 1
fi
log "Remote LLM is reachable at http://192.168.50.245:11436"

# ---- 3. Export full env --------------------------------------------------
export PYTHONPATH="${PROJECT_ROOT}"
export LOG_LEVEL=INFO

export DATABASE_URL="postgresql+asyncpg://factory:factory@localhost:5432/factory"
export REDIS_URL="redis://localhost:6379/0"
export CELERY_BROKER_URL="redis://localhost:6379/1"
export CELERY_RESULT_BACKEND="redis://localhost:6379/2"

export STORAGE_BACKEND=local
export STORAGE_LOCAL_PATH="${PROJECT_ROOT}/_storage_data"
# Use real disk for yt-dlp/ffmpeg scratch — /tmp is a 16G tmpfs that fills up.
export WORKER_TMP_DIR="${PROJECT_ROOT}/_storage_data/_worktmp"
mkdir -p "${WORKER_TMP_DIR}"

# Token encryption (required by services/facebook/* and shared_py.crypto)
export TOKEN_ENCRYPTION_KEY="$(grep '^TOKEN_ENCRYPTION_KEY=' "${PROJECT_ROOT}/.env" | cut -d= -f2- | awk '{print $1}')"
# Facebook integration env (publisher rate limits + Graph API version)
export FACEBOOK_GRAPH_API_VERSION="${FACEBOOK_GRAPH_API_VERSION:-v22.0}"
export FACEBOOK_DAILY_LIMIT_PER_PAGE="${FACEBOOK_DAILY_LIMIT_PER_PAGE:-10}"
export FACEBOOK_MIN_DELAY_BETWEEN_POSTS_S="${FACEBOOK_MIN_DELAY_BETWEEN_POSTS_S:-1800}"
export REQUIRE_MANUAL_APPROVAL="${REQUIRE_MANUAL_APPROVAL:-true}"

# All real (no mocks)
export MOCK_DOWNLOAD=0 MOCK_ASR=0 MOCK_LLM=0 MOCK_RENDER=0 MOCK_YOLO=0
export ENABLE_DIARIZATION=0

# LLM: remote Ollama (host04 .245, qwen 32b on RTX 4090)
export QWEN_BASE_URL="http://192.168.50.245:11436/v1"
export QWEN_MODEL="qwen2.5:32b-instruct-q4_K_M"
export QWEN_TIMEOUT_S=600
export QWEN_CONTEXT_WINDOW=16384
export QWEN_MAX_TOKENS=4096

export YOUTUBE_MAX_DURATION_SECONDS=7200
export YOUTUBE_MIN_DURATION_SECONDS=180
export YOUTUBE_MAX_RESULTS_PER_PAGE=15

# yt-dlp: force h264 codec so cv2/YOLO can decode frames
export DOWNLOAD_FORMAT="bestvideo[vcodec^=avc1][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best"
export DOWNLOAD_OUTPUT_TEMPLATE="%(id)s.%(ext)s"

# WhisperX + YOLO (GPU)
export WHISPERX_MODEL=large-v3
export WHISPERX_DEVICE=cuda
export WHISPERX_COMPUTE_TYPE=float16
export WHISPERX_BATCH_SIZE=16
export YOLO_MODEL_PATH="${PROJECT_ROOT}/_storage_data/models/yolov11n.pt"
export YOLO_DEVICE=cuda

# Render: keep source audio, dual subs, 30s min
export KEEP_ORIGINAL_AUDIO=1
export MIN_OUTPUT_DURATION_S=30.0
export MIN_RECAP_DURATION_S=30.0
export MAX_HIGHLIGHT_SPAN_S=300.0

# ---- 4. Start worker -----------------------------------------------------
log "Starting celery worker..."
mkdir -p "${PROJECT_ROOT}/_logs"
WORKER_LOG="${PROJECT_ROOT}/_logs/worker.log"
nohup ~/.local/bin/celery -A apps.workers.worker_app worker \
  -Q download,whisperx,diarization,yolo,qwen,render-prep,render,discovery,reels,facebook,image_posts \
  -l INFO --concurrency=1 --pool=prefork > "${WORKER_LOG}" 2>&1 &
disown
sleep 6

# ---- 5. Verify ready ----------------------------------------------------
if grep -q 'celery@.*ready\.' "${WORKER_LOG}"; then
  log "Worker ready."
  grep 'ready\.' "${WORKER_LOG}" | tail -1
else
  log "Worker may not have started cleanly. Log tail:"
  tail -30 "${WORKER_LOG}"
  exit 1
fi

echo
log "Done. The worker will now process queued jobs using the LOCAL LLM."
log "Monitor: tail -f ${WORKER_LOG} | grep -vE '\\\\[download\\\\] +[0-9]'"
log "Dashboard: http://localhost:3000/jobs"
