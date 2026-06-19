#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# LLMeter — one-line judged run (SCRUM-23, LLM-as-judge)
#
# Loads .env, points the DB at localhost, syncs prompts, then runs
# the evaluator with --judge so every response is scored by Gemini.
#
# Usage (from repo root):
#   bash judge.sh                              # smoke dataset, Sonnet
#   bash judge.sh claude-sonnet-4-6 gpt-5      # pick the model(s)
#   DATASET=evaluator/datasets/regression_v2.yaml bash judge.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

PY=".venv/bin/python"
DATASET="${DATASET:-evaluator/datasets/sprint1_smoke.yaml}"
# Models: any args passed to the script, else a single cheap default.
if [[ $# -gt 0 ]]; then MODELS=("$@"); else MODELS=(claude-sonnet-4-6); fi

# ─── Load .env, make the DB reachable from the host ──────────
[[ -f .env ]] || { echo "✗ .env missing — copy .env.example and fill in keys." >&2; exit 1; }
set -a; source .env; set +a
# .env DATABASE_URL uses the Docker hostname `postgres`; rewrite to localhost.
DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}"
export DATABASE_URL

# ─── Preflight: the judge needs a Gemini key ─────────────────
[[ -n "${GEMINI_API_KEY:-}" ]] || { echo "✗ GEMINI_API_KEY not set in .env." >&2; exit 1; }

echo "▸ Syncing prompts into the DB…"
"$PY" -m app.prompts.cli sync

echo "▸ Judged run: dataset=$DATASET models=${MODELS[*]}"
exec "$PY" runner.py \
  --dataset "$DATASET" \
  --models "${MODELS[@]}" \
  --judge \
  --max-workers 2
