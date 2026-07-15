#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# LLMeter — Sprint 1 demo
# ─────────────────────────────────────────────────────────────
# Walks through the maximum surface delivered by epic SCRUM-10:
#   1. Preflight  — Postgres health + required API keys
#   2. Prompts    — sync versioned YAML prompts into the DB
#   3. Runner     — parallel multi-model evaluation, judged (Gemini),
#                   3 samples per (case, model) → mean ± stddev, then
#                   the per-profile final decision (auto, SCRUM-38)
#   4. Aggregate  — per-model + per-run summary + decision from SQL
#
# Cost : ~50-75 cents per run (3 samples × reasoning models dominate;
#        the Gemini judge/decision calls are cents).
# Wall : ~2-4 minutes (model calls are parallel; judge calls are
#        sequential on the main thread — 72 of them at 3 samples).
#
# Run it from the repo root:
#   bash demo.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

DATASET="evaluator/datasets/demo_v1.yaml"
MODELS=(claude-sonnet-4-6 claude-opus-4-8 gpt-5.4 gpt-5.5)

# Colors — bail to plain text if not a TTY.
if [[ -t 1 ]]; then
  C_HEAD=$'\033[1;36m' C_OK=$'\033[1;32m' C_WARN=$'\033[1;33m'
  C_ERR=$'\033[1;31m' C_DIM=$'\033[2m' C_OFF=$'\033[0m'
else
  C_HEAD='' C_OK='' C_WARN='' C_ERR='' C_DIM='' C_OFF=''
fi

banner() {
  printf '\n%s┌──────────────────────────────────────────────────────────────┐%s\n' "$C_HEAD" "$C_OFF"
  printf   '%s│ %-60s │%s\n' "$C_HEAD" "$1" "$C_OFF"
  printf   '%s└──────────────────────────────────────────────────────────────┘%s\n' "$C_HEAD" "$C_OFF"
}

step()  { printf '%s▸%s %s\n' "$C_HEAD" "$C_OFF" "$1"; }
ok()    { printf '  %s✓%s %s\n' "$C_OK"   "$C_OFF" "$1"; }
warn()  { printf '  %s!%s %s\n' "$C_WARN" "$C_OFF" "$1"; }
die()   { printf '  %s✗%s %s\n' "$C_ERR"  "$C_OFF" "$1" >&2; exit 1; }

# ─── 0. Load .env ────────────────────────────────────────────
if [[ ! -f .env ]]; then
  die ".env missing. Copy .env.example to .env and fill in the keys."
fi
set -a; source .env; set +a

# The .env DATABASE_URL points at `postgres:5432` (Docker hostname),
# which is unreachable from the host. Rewrite to localhost — the
# Postgres port is published in docker-compose.yml.
DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}"
export DATABASE_URL

# ─── 1. Preflight ────────────────────────────────────────────
banner "1/4  Preflight"

step "Postgres reachable on localhost:5432"
if ! docker compose exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
  die "Postgres not healthy. Run: docker compose up -d postgres"
fi
ok "container llmcomp_postgres is healthy"

step "API keys present in environment"
[[ -n "${ANTHROPIC_API_KEY:-}" ]] || die "ANTHROPIC_API_KEY not set in .env"
[[ -n "${OPENAI_API_KEY:-}"    ]] || die "OPENAI_API_KEY not set in .env"
[[ -n "${GEMINI_API_KEY:-}"    ]] || die "GEMINI_API_KEY not set in .env (juge + décision finale)"
ok "ANTHROPIC_API_KEY, OPENAI_API_KEY and GEMINI_API_KEY loaded"

step "Python venv + deps"
if [[ ! -d .venv ]]; then
  die ".venv missing. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r app/requirements.txt"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
ok "$(python --version) — venv active"

# ─── 2. Sync versioned prompts ───────────────────────────────
banner "2/4  Sync versioned prompts (SCRUM-18)"
step "python -m app.prompts.cli sync"
python -m app.prompts.cli sync | sed 's/^/    /'

# ─── 3. Run the evaluation ───────────────────────────────────
banner "3/4  Parallel multi-model run, judged (SCRUM-19/22/23/38)"
SAMPLES=3
printf '%sDataset%s  %s\n' "$C_DIM" "$C_OFF" "$DATASET"
printf '%sModels%s   %s\n' "$C_DIM" "$C_OFF" "${MODELS[*]}"
printf '%sFan-out%s  %d cases × %d models × %d samples = %d calls (+ jugement Gemini)\n' \
  "$C_DIM" "$C_OFF" 6 "${#MODELS[@]}" "$SAMPLES" $((6 * ${#MODELS[@]} * SAMPLES))
echo

step "Cases under evaluation (from $DATASET)"
python - <<PY
from app.datasets import load_dataset
ds = load_dataset("$DATASET")
for c in ds.cases:
    cat = c.raw.get("category", "?")
    one_line = " ".join(c.prompt.split())
    if len(one_line) > 78:
        one_line = one_line[:75] + "..."
    print(f"    • [{cat:<12}] {c.id}")
    print(f"        {one_line}")
PY
echo

START_TS=$(date +%s)
python runner.py --dataset "$DATASET" --models "${MODELS[@]}" --max-workers 12 \
  --judge --samples "$SAMPLES" --temperature 0.7
WALL=$(( $(date +%s) - START_TS ))
echo
ok "wall time: ${WALL}s"

# ─── 4. Aggregate from Postgres ──────────────────────────────
banner "4/4  Persisted metrics (SCRUM-16 + view 004)"

PSQL=(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off)

step "Latest run header"
"${PSQL[@]}" -c "
  SELECT id AS run_id, dataset, prompt_id,
         to_char(started_at, 'YYYY-MM-DD HH24:MI:SS') AS started,
         to_char(finished_at, 'YYYY-MM-DD HH24:MI:SS') AS finished
    FROM runs ORDER BY id DESC LIMIT 1;"

step "Per-model breakdown for the latest run"
"${PSQL[@]}" -c "
  WITH r AS (SELECT MAX(id) AS id FROM runs)
  SELECT m.name                                    AS model,
         COUNT(*)                                  AS n,
         ROUND(AVG(res.latency_ms)::numeric, 0)    AS avg_ms,
         SUM(res.input_tokens)                     AS in_tok,
         SUM(res.output_tokens)                    AS out_tok,
         to_char(SUM(res.cost), 'FM\$0.000000')   AS cost
    FROM results res
    JOIN models  m ON m.id = res.model_id
    JOIN r        ON res.run_id = r.id
   GROUP BY m.name
   ORDER BY SUM(res.cost) DESC;"

step "Per-case response preview (latest run)"
"${PSQL[@]}" -c "
  WITH r AS (SELECT MAX(id) AS id FROM runs)
  SELECT res.case_id,
         m.name AS model,
         res.latency_ms AS ms,
         LEFT(regexp_replace(res.response, E'[\\n\\r]+', ' ', 'g'), 55) AS response
    FROM results res
    JOIN models  m ON m.id = res.model_id
    JOIN r        ON res.run_id = r.id
   ORDER BY res.case_id, m.name;"

step "Run-level aggregates from the run_metrics view (migration 004)"
"${PSQL[@]}" -c "
  SELECT run_id, n_results, n_models, n_cases,
         duration_ms,
         to_char(total_cost, 'FM\$0.000000')      AS total_cost,
         total_input_tokens  AS in_tok,
         total_output_tokens AS out_tok,
         avg_latency_ms,
         p50_latency_ms,
         p95_latency_ms
    FROM run_metrics
   ORDER BY run_id DESC LIMIT 5;"

step "Décision finale par profil (SCRUM-38 — auto après un run jugé)"
"${PSQL[@]}" -c "
  SELECT profile, recommended_model, confidence
    FROM decision_by_profile
   WHERE run_id = (SELECT MAX(id) FROM runs)
   ORDER BY profile;"

banner "Demo complete"
printf '%sDashboards:%s http://localhost:3000 — LLM Model Comparison,\n' "$C_DIM" "$C_OFF"
printf '            LLM Final Decision (run le plus récent).\n\n'
