# LLM Evaluation Platform

A tool that compares Claude vs OpenAI on the same prompts, scores responses,
and catches quality regressions in CI/CD.

## Architecture
- docs/ARCHITECTURE.md → Full technical architecture of the project. Read this
                    first before making any changes. It contains the solution
                    design, component responsibilities, data model, tech choices,
                    and the full backlog traceability matrix.

## Stack
- Python 3.12 (app/)
- PostgreSQL 16 (database)
- Grafana 11 (dashboards)
- Docker Compose (local dev)
- Azure Container Apps (production)
- GitHub Actions (CI/CD)

## Project Structure
- app/          → Python application (LLM client, runner, metrics)
- evaluator/    → Evaluation logic and scoring
- dashboard/    → Grafana dashboard configs
- db/           → SQL schema and seed data
- infra/        → Azure Bicep infrastructure
- .github/      → CI/CD workflows
- tests/        → pytest test suite

## Key Files
- docs/ARCHITECTURE.md → Full technical architecture. Read this before anything else.
- db/schema.sql       → PostgreSQL base schema (4 tables: models, prompts, runs,
                        results), extended in-place by numbered migrations in db/
- db/0NN_*.sql        → Numbered migrations (currently through 020); add a new one
                        for any schema change, never edit the base schema
- db/seed.sql         → Test data
- docker-compose.yml  → Local dev environment
- .env                → Local secrets (never commit this)

## Local Dev Commands
```bash
# Start all services
docker compose up -d

# Stop all services
docker compose down

# View logs
docker compose logs -f

# Run tests
docker compose exec app pytest

# Access database
docker compose exec postgres psql -U llm -d llm_eval
```

## Database
- 4 base tables: models → prompts → runs → results. The `results` table is
  extended in-place by numbered migrations: `question`, `case_id`, `prompt_style`,
  `sample_idx`, and the response-style features `resp_style_*`.
- Foreign keys enforce referential integrity
- All schema changes go through a new numbered migration in db/ (through 020),
  never by editing the base schema. The `models` table gains `context_window`
  (014) so prompt size can be expressed as a % of capacity.
- Run-scoping (019/020): every Grafana board is scoped to ONE run via a `$run`
  template variable so a fresh test isn't blended with history. Migration 019
  adds `decisions.run_id` (FK → runs, `ON DELETE CASCADE`); migration 020
  appends a `run_id` column to the views the dashboards read (`model_metrics`,
  `style_confound`, `model_decision_metrics`, `decision_summary`,
  `decision_by_profile`) — `CREATE OR REPLACE VIEW` only allows appending
  columns, so `run_id` is always last. `result_review` (013) already had it.
- Judge score scale: the LLM judge returns a raw float in [0.0, 1.0]; it is
  scaled ×5 at persist time and stored in `results.judge_score` on a 0–5 scale.
- Repeated sampling: the runner evaluates each (case, model) pair N times
  (`--samples N`, default 10); every draw is its own `results` row tagged with
  `sample_idx`, so scores carry a mean ± stddev instead of one noisy draw. Needs
  `--temperature > 0` for run-to-run variance on non-reasoning models.
- Alert threshold: average judge score below 3.5/5 triggers a regression alert
  (i.e. a raw judge score below 0.7 before scaling). With repeated sampling the
  alert evaluates the N-sample mean (view `result_variance`), not a single draw.
- Response style: `resp_style_*` columns capture each answer's markdown shape
  (headers/bold/lists/code) to diagnose whether judge scores are confounded by
  formatting — see view `style_confound` and the OLS style-adjusted score in
  app/style_analysis.py.
- Aggregation views Grafana reads: `run_metrics` (004), `style_metrics` (006),
  `model_metrics` (008), `result_variance` (010), `style_confound` (012),
  `model_decision_metrics` (015), `decision_summary` (016),
  `decision_by_profile` (017); the dashboard-facing ones carry `run_id` (020)
  so each board filters to a single run.
- Per-dataset snapshot/restore: `scripts/dataset_snapshot.sh export <dataset>`
  writes a self-contained, psql-restorable `.sql` (runs + results + decisions
  for that dataset) to `eval_backups/`; `... restore <file>` backs up the whole
  DB first, then reloads it (idempotent replace of that dataset's rows, ids
  preserved because the `models`/`prompts` catalogue is stable).
  `scripts/reset_db.sh --dataset <name>` wipes one dataset's runs symmetrically.
- Quality triage: view `result_review` (013) exposes every non-perfect result
  with a heuristic `failure_type` (refus / erreur de fond / omission / forme),
  feeding the `llm_quality_triage` dashboard.
- Final decision (SCRUM-38): `python -m app.decide --profile <name>` (or
  `--all-profiles`) recommends the best model **per usage profile**, scoped to
  ONE run via `--run <id>` (default: the latest run) so the decision reflects
  only that test's models — not every model ever judged. Profiles are
  versioned numeric weights in `app/decision_profiles.yaml` (`equilibre` default
  + `etudiant` / `rapide` / `economie`), loaded by `app/profiles.py` (kept out of
  `prompts/templates/` because that folder is scanned by the prompt sync). Hybrid design: `app/decision_scoring.py` ranks models with a
  deterministic min-max weighted score (the PICK + confidence), then the judge
  LLM (Gemini) only *writes the justification* via the versioned prompt
  `final_decision.yaml` (v2). Metrics come from view `model_decision_metrics`
  (tokens, latency, judge score, efficiency = score/1k tokens, % context window,
  USD as a *derived* reference — never decisive; tokens are primary). Decisions
  are persisted in `decisions` (with `profile`, `weighted_scores`) and shown by
  the `llm_final_decision` dashboard (views `decision_summary`,
  `decision_by_profile`, both run-scoped). Reproducibility (DoD #6) is enforced
  by a cache keyed on (`input_hash`, prompt id, profile) where `input_hash` folds
  in the metrics, the profile weights AND the run id: same data + same weights +
  same run replays the stored decision; editing a weight regenerates it
  (`--force` to override).

## Logging (SCRUM-32)
- All Python components emit **structured JSON logs, one line per event**, via
  `app/logging_setup.py` — no third-party dependency. Each event carries
  `timestamp` (ISO-8601 UTC), `level`, `logger`, `message`, plus `model` and
  `run_id`; errors logged with `exc_info`/`logger.exception` include the full
  stack trace under `exception`.
- Logs go to **stdout**, which Azure Container Apps ships into Azure Monitor
  Log Analytics (`ContainerAppConsoleLogs_CL`). The runner's end-of-run summary
  tables stay as `print()` — that's CLI report output, not log events.
- Bind context once with `log_context(run_id=…)` (a `contextvars`-backed MDC,
  à la SLF4J); a per-call `extra={"model": …}` overrides it. Every CLI
  entrypoint calls `configure_logging()` (idempotent) before doing work.
- **Never log API keys or prompt/response content** (B13) — only call shape and
  outcome (model, provider, latency, tokens).

## CI/CD (SCRUM-25)
- `.github/workflows/ci.yml` runs on every PR to `main`, in two jobs:
  - **lint-and-test** (offline, no secrets): `ruff check app tests` then
    `pytest --cov=app --cov-fail-under=70`. Tests mock the LLM SDKs and psycopg,
    so this needs no API keys and no database.
  - **eval-gate** (needs lint-and-test): Postgres 16 service → apply
    `db/schema.sql` + `db/0*.sql` + `db/seed.sql` → `prompts.cli sync` → a real
    but tiny eval (`sprint1_smoke.yaml` × `claude-haiku-4-5` + `gpt-5`,
    Gemini judge) with `--fail-under 3.5`. API keys come from GitHub Secrets
    (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`); the job fails
    fast with an explicit error if any of the three secrets is missing.
- **Regression gate** = `runner --fail-under SCORE` (requires `--judge`): exits
  code 5 (`EXIT_REGRESSION`, beats the partial-failure code 3) if any model's
  mean judge score is below SCORE on the 0–5 scale. Logic lives in the pure,
  unit-tested `regression_failures()` helper in `app/runner.py`.
- **Lint** is intentionally conservative: ruff `select = ["E","F","I"]`,
  `ignore = ["E501","E731"]` (see `[tool.ruff]` in `pyproject.toml`). Dev/CI
  tooling is in `requirements-dev.txt` (not in the runtime image).

## Rules
- Never commit .env
- API keys go in .env only, never hardcoded
- All SQL changes go through versioned files in db/
- Every PR must pass the eval suite before merge
