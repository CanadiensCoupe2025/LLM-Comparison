#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# LLMeter — Sprint 1 demo
# ─────────────────────────────────────────────────────────────
# Walks through the maximum surface delivered by epic SCRUM-10:
#   1. Preflight  — Postgres health + required API keys
#   2. Prompts    — sync versioned YAML prompts into the DB
#   3. Runner     — parallel multi-model evaluation, persisted
#   4. Aggregate  — per-model + per-run summary from SQL
#
# Cost : ~15-20 cents per run (reasoning models o3/gpt-5 dominate;
#        Sonnet alone is sub-cent — see the per-model breakdown).
# Wall : ~10-15 seconds (bounded by the slowest model, not the sum).
#
# Run it from the repo root:
#   bash demo.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

DATASET="evaluator/datasets/demo_v1.yaml"
MODELS=(claude-sonnet-4-6 claude-opus-4-8 gpt-5 o3)

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
ok "ANTHROPIC_API_KEY and OPENAI_API_KEY loaded"
if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  warn "DEEPSEEK_API_KEY not set — DeepSeek models skipped (registry-ready, just gated by missing key)."
fi

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
banner "3/4  Parallel multi-model run (SCRUM-19 + SCRUM-22)"
printf '%sDataset%s  %s\n' "$C_DIM" "$C_OFF" "$DATASET"
printf '%sModels%s   %s\n' "$C_DIM" "$C_OFF" "${MODELS[*]}"
printf '%sFan-out%s  %d cases × %d models = %d parallel calls\n' \
  "$C_DIM" "$C_OFF" 6 "${#MODELS[@]}" $((6 * ${#MODELS[@]}))
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
python runner.py --dataset "$DATASET" --models "${MODELS[@]}" --max-workers 12
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

banner "Demo complete"
printf '%sNext sprint:%s LLM-as-judge (SCRUM-23), Grafana dashboard (SCRUM-30),\n' "$C_DIM" "$C_OFF"
printf '             CI evaluation gate (SCRUM-25).\n\n'
