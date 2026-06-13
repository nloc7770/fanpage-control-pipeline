#!/usr/bin/env bash
# One-shot dev bootstrap.
# - Copies .env.example -> .env if missing
# - Creates a python venv under .venv and installs the shared packages in editable mode
# - Installs pnpm dependencies for the TS workspace
#
# Run from the repo root: `bash scripts/bootstrap.sh` or `make bootstrap`.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "[bootstrap] repo root: $REPO_ROOT"

# -- .env --------------------------------------------------------------------
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[bootstrap] wrote .env (from .env.example) -- edit before running prod"
else
  echo "[bootstrap] .env already exists, leaving it alone"
fi

# -- python venv -------------------------------------------------------------
if [[ ! -d .venv ]]; then
  echo "[bootstrap] creating .venv"
  python3.11 -m venv .venv 2>/dev/null || python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip wheel
python -m pip install -e packages/shared-py
python -m pip install -e packages/database

# -- pnpm --------------------------------------------------------------------
if command -v pnpm >/dev/null 2>&1; then
  echo "[bootstrap] installing pnpm workspace deps"
  pnpm install --frozen-lockfile || pnpm install
else
  echo "[bootstrap] pnpm not installed -- skipping JS deps. Install pnpm to continue."
fi

echo "[bootstrap] done. Next: make up && make migrate && make seed"
