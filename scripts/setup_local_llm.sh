#!/usr/bin/env bash
# Setup local LLM (Ollama + Qwen2.5 7B) to replace the offline remote Qwen
# endpoint. Idempotent: re-running is safe.
#
# Usage:
#   bash scripts/setup_local_llm.sh
#
# What it does:
#   1. Install Ollama if missing.
#   2. Start Ollama daemon on 127.0.0.1:11434 (default port).
#   3. Pull qwen2.5:7b-instruct (~5GB, first run only).
#   4. Smoke test the OpenAI-compatible /v1 endpoint.
#   5. Print the env vars the worker should be restarted with.

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="/tmp/ollama.log"
HOST="127.0.0.1"
PORT="11434"
BASE_URL="http://${HOST}:${PORT}"
MODEL="qwen2.5:7b-instruct"

log() { echo "[setup-llm] $*"; }

# ---- 1. Install Ollama --------------------------------------------------
if ! command -v ollama >/dev/null 2>&1; then
  log "Installing Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
else
  log "Ollama already installed: $(ollama --version 2>&1 | head -1)"
fi

# ---- 2. Kill any stale ollama instance ----------------------------------
if pgrep -f "ollama serve" >/dev/null 2>&1; then
  log "Killing existing ollama serve..."
  pkill -f "ollama serve" || true
  sleep 2
fi

# ---- 3. Start Ollama daemon ---------------------------------------------
log "Starting ollama serve on ${HOST}:${PORT}..."
OLLAMA_HOST="${HOST}:${PORT}" nohup ollama serve > "${LOG_FILE}" 2>&1 &
disown
sleep 5

# Health check loop (up to 30s)
for i in $(seq 1 15); do
  if curl -s --max-time 2 "${BASE_URL}/api/tags" >/dev/null 2>&1; then
    log "Daemon is up."
    break
  fi
  sleep 2
done

if ! curl -s --max-time 2 "${BASE_URL}/api/tags" >/dev/null 2>&1; then
  log "ERROR: ollama daemon did not start. Log tail:"
  tail -30 "${LOG_FILE}"
  exit 1
fi

# ---- 4. Pull model -------------------------------------------------------
if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "${MODEL}"; then
  log "Model ${MODEL} already present."
else
  log "Pulling ${MODEL} (~5GB, this may take a few minutes)..."
  OLLAMA_HOST="${HOST}:${PORT}" ollama pull "${MODEL}"
fi

# ---- 5. Smoke test OpenAI-compatible endpoint ---------------------------
log "Smoke test /v1/models..."
curl -s "${BASE_URL}/v1/models" | python3 -m json.tool | head -20

log "Smoke test /v1/chat/completions (JSON mode)..."
curl -s "${BASE_URL}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d "{
    \"model\": \"${MODEL}\",
    \"messages\": [{\"role\":\"user\",\"content\":\"Reply ONLY with this JSON object and nothing else: {\\\"ok\\\":true}\"}],
    \"temperature\": 0.1,
    \"response_format\": {\"type\":\"json_object\"}
  }" | python3 -c "
import sys, json
d = json.load(sys.stdin)
content = d.get('choices', [{}])[0].get('message', {}).get('content', '')
print('Response content:', content[:200])
try:
    parsed = json.loads(content)
    print('Parsed JSON:', parsed)
except Exception as e:
    print('NOT VALID JSON:', e)
"

# ---- 6. Print env vars worker needs --------------------------------------
cat <<EOF

================================================================
  Local LLM is ready. To switch the pipeline, run:

  bash ${PROJECT_ROOT}/scripts/restart_worker_local_llm.sh

  Or set these env vars manually before launching celery:
    QWEN_BASE_URL=${BASE_URL}/v1
    QWEN_MODEL=${MODEL}
    QWEN_TIMEOUT_S=300
    QWEN_CONTEXT_WINDOW=16384
    QWEN_MAX_TOKENS=4096
================================================================
EOF
