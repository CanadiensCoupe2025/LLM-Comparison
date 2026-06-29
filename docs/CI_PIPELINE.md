# CI Pipeline (SCRUM-25)

> **Jira:** SCRUM-25 — *Créer le pipeline GitHub Actions CI avec lint, tests et
> évaluations sur PR* · Epic SCRUM-11 (Évaluation)
> **File:** [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

This document explains how continuous integration works on this project, why it
is built the way it is, and how to operate and extend it.

---

## 1. What problem this solves

The repo rule is *"Every PR must pass the eval suite before merge."* Before
SCRUM-25 that rule was unenforced — `.github/workflows/` was empty, so a pull
request could merge code that broke tests or **regressed answer quality** and
nothing would catch it.

SCRUM-25 makes the rule real: every PR now runs **lint → unit tests → a real
(but cheap) evaluation**, and the pipeline **fails if answer quality drops below
3.5/5**. It is the automated quality gate that protects everything else in the
codebase.

### Definition of Done (from Jira)

| DoD | Where it's met |
|-----|----------------|
| Workflow triggers on every PR | `on: pull_request` → `branches: [main]` |
| Lint, unit tests and evaluations chain automatically | two jobs, `eval-gate` `needs: lint-and-test` |
| Pipeline fails if a quality score drops below the threshold | `runner --fail-under 3.5` → exit code 5 |
| API keys injected from GitHub Secrets | `env: ANTHROPIC_API_KEY: ${{ secrets.* }}` etc. |

---

## 2. The shape of the pipeline

```
Pull Request to main
        │
        ▼
┌──────────────────────────┐
│ Job 1: lint-and-test     │   offline · no secrets · no DB
│  • ruff check            │
│  • pytest + coverage 70% │
└──────────────────────────┘
        │ (needs: passes)
        ▼
┌──────────────────────────────────────────────┐
│ Job 2: eval-gate                              │   real LLM calls
│  • Postgres 16 service container              │
│  • apply schema + migrations + seed           │
│  • prompts.cli sync                           │
│  • runner --judge --fail-under 3.5            │
│        └─ exit 5 if any model mean < 3.5/5    │
└──────────────────────────────────────────────┘
```

Two jobs, run in sequence. The cheap, deterministic checks (job 1) act as a
pre-filter so we never spend API tokens on a branch that already fails lint or
tests.

---

## 3. Job 1 — `lint-and-test` (fast, offline, free)

```yaml
- run: pip install -r requirements-dev.txt
- run: ruff check app tests
- run: pytest --cov=app --cov-report=term-missing --cov-fail-under=70
```

- **No API keys, no database.** The unit tests mock the LLM SDKs
  (`anthropic.Anthropic`, `openai.OpenAI`, `google.genai`) and `psycopg`, so the
  whole suite runs with zero network calls. That is what makes this job cheap and
  deterministic.
- **Coverage gate ≥ 70%.** Configured in
  [`pyproject.toml`](../pyproject.toml) (`[tool.coverage.report] fail_under = 70`)
  and enforced with `--cov-fail-under=70`. Current coverage is ~79%.
- **Lint = ruff**, configured conservatively so it catches real defects without
  forcing a reformat of the existing tree:
  ```toml
  [tool.ruff.lint]
  select = ["E", "F", "I"]      # pycodestyle errors, pyflakes, import sorting
  ignore = ["E501", "E731"]     # line-length + lambda-assignment: pre-existing style
  ```

Dev/CI tooling lives in [`requirements-dev.txt`](../requirements-dev.txt)
(`-r app/requirements.txt` + `ruff`) so it never ships in the runtime container
image (`app/Dockerfile` installs only `app/requirements.txt`).

---

## 4. Job 2 — `eval-gate` (the quality gate)

This job runs a **real** evaluation against live model APIs, then fails the build
if quality regressed. We keep it *real* (so it genuinely exercises Secrets + the
judge) but *minimal* (so it costs ~cents per PR).

### 4.1 Postgres service container

The runner is built around persistence — it creates a run, inserts every result,
and finalizes the run — so the gate needs a database. GitHub Actions spins one up
as a service container:

```yaml
services:
  postgres:
    image: postgres:16
    env: { POSTGRES_USER: llm, POSTGRES_PASSWORD: llm, POSTGRES_DB: llm_eval }
    options: >-
      --health-cmd "pg_isready -U llm -d llm_eval" ...
```

`DATABASE_URL` points the app at `localhost:5432`.

### 4.2 Bringing up the schema

The DB starts empty, so we apply the SQL in the same order the team applies it
locally (documented in [`db/README.md`](../db/README.md)):

```bash
psql ... -f db/schema.sql          # base tables
for f in db/0*.sql; do psql ... -f "$f"; done   # migrations 002 → 017, in order
psql ... -f db/seed.sql            # model catalogue (incl. context_window)
```

`seed.sql` includes the `context_window` column added by migration 014, so the
migrations **must** run before the seed — the loop above guarantees it.

### 4.3 Syncing prompts

```bash
python -m app.prompts.cli sync
```

The runner loads its `eval_system` prompt **from the database**, so the versioned
YAML prompts have to be synced into the `prompts` table before a run.

### 4.4 The eval + gate

```bash
python -m app.runner \
  --dataset evaluator/datasets/sprint1_smoke.yaml \
  --models claude-haiku-4-5 deepseek-v4-flash \
  --judge --samples 1 --temperature 0 --fail-under 3.5
```

- **1 case × 2 low-cost models × 1 sample** = 2 model calls + 2 judge calls. The
  two models (`claude-haiku-4-5`, `deepseek-v4-flash`) are the cheapest seeded
  models; the judge defaults to `gemini-2.5-pro`. Only **three** secrets are
  therefore needed (no OpenAI).
- `--judge` scores each answer; `--fail-under 3.5` turns scores into a gate.

---

## 5. How the regression gate works (the code)

The runner previously only signalled *call failures* (exit 3); it never failed on
a low **score**. SCRUM-25 adds a score gate to
[`app/runner.py`](../app/runner.py):

**A pure, unit-tested helper** — no DB, no network — reused from the in-memory
run aggregates:

```python
def regression_failures(outcome, fail_under):
    """Models whose mean judge score is below fail_under (empty if gate off)."""
    if fail_under is None:
        return []
    return sorted(
        (model_key, mean)
        for model_key, (mean, _stddev, _n) in outcome.model_score_stats().items()
        if mean < fail_under
    )
```

**A new CLI flag** `--fail-under SCORE` (requires `--judge`; validated up front so
a misuse fails before spending a token).

**A new exit code** `EXIT_REGRESSION = 5`. In `main()`, after the run:

```python
failures = regression_failures(outcome, args.fail_under)
for model_key, mean in failures:
    log.error("regression gate failed", extra={"model": model_key, ...})
if failures:
    return EXIT_REGRESSION         # beats the partial-failure code 3
return EXIT_PARTIAL_FAILURE if outcome.failed else EXIT_OK
```

A below-threshold score is the most actionable signal, so code 5 takes precedence
over code 3. The structured log line (`model`, `run_id`, `mean_score`,
`threshold`) is JSON — see [`docs/`](./ARCHITECTURE.md) §5.8 (SCRUM-32) — so a
regression is queryable, not just visible in console scrollback.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | all calls succeeded |
| 1 | configuration error (missing env, DB unreachable, no system prompt) |
| 2 | bad CLI arguments (argparse) |
| 3 | run completed but ≥ 1 model call raised |
| **5** | **regression gate tripped — a model's mean judge score < `--fail-under`** |

---

## 6. Security

- API keys are **only** ever read from environment variables, injected from
  **GitHub Secrets** in CI (DoD #4). They are never written to the repo or to
  logs (the structured logger logs call *shape* — model, latency, tokens — never
  keys or prompt content).
- **Fork limitation:** GitHub does not expose Secrets to PRs opened from forks, so
  the `eval-gate` job cannot run on fork PRs. This repo is solo (PRs come from
  branches), so it is a non-issue today. If external contributors are added
  later, options are: gate the job behind a `pull_request_target` trigger with an
  explicit allow-list, or skip the eval-gate on forks and run it post-merge.

---

## 7. Running it yourself (local equivalents)

```bash
# Lint + tests + coverage (exactly what job 1 runs)
pip install -r requirements-dev.txt
ruff check app tests
pytest --cov=app --cov-fail-under=70

# Just the gate's unit tests
pytest tests/test_runner.py -k regression -q

# The full eval gate locally (needs Docker + a .env with the 3 keys)
docker compose up -d postgres
# apply db/schema.sql, db/0*.sql, db/seed.sql via psql, then:
python -m app.prompts.cli sync
python -m app.runner --dataset evaluator/datasets/sprint1_smoke.yaml \
  --models claude-haiku-4-5 deepseek-v4-flash \
  --judge --samples 1 --fail-under 3.5
echo $?     # 0 = pass, 5 = regression
```

To prove the gate actually bites, re-run with `--fail-under 5.1` (impossible to
satisfy) and confirm the exit code is `5`.

---

## 8. One-time setup for this repo

1. Add repository **Secrets** (Settings → Secrets and variables → Actions):
   `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`.
2. (Recommended) Make `lint-and-test` and `eval-gate` **required status checks**
   on `main` (Settings → Branches → branch protection). That is what makes the
   gate actually *block* merge and operationalizes the repo rule.

---

## 9. What's intentionally out of scope (follow-ups)

- **SCRUM-26** — post the comparative regression report as a PR comment.
- **SCRUM-28** — CD: auto-deploy to staging on merge, manual approval for prod.
- Widening the eval-gate dataset/models once cost/runtime budgets are agreed.
