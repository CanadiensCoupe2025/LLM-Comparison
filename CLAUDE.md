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
- db/0NN_*.sql        → Numbered migrations (currently through 013); add a new one
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
- All schema changes go through a new numbered migration in db/ (through 017),
  never by editing the base schema. The `models` table gains `context_window`
  (014) so prompt size can be expressed as a % of capacity.
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
  `decision_by_profile` (017).
- Quality triage: view `result_review` (013) exposes every non-perfect result
  with a heuristic `failure_type` (refus / erreur de fond / omission / forme),
  feeding the `llm_quality_triage` dashboard.
- Final decision (SCRUM-38): `python -m app.decide --profile <name>` (or
  `--all-profiles`) recommends the best model **per usage profile**. Profiles are
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
  `decision_by_profile`). Reproducibility (DoD #6) is enforced by a cache keyed
  on (`input_hash`, prompt id, profile) where `input_hash` folds in the metrics
  AND the profile weights: same data + same weights replays the stored decision;
  editing a weight regenerates it (`--force` to override).

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

## Rules
- Never commit .env
- API keys go in .env only, never hardcoded
- All SQL changes go through versioned files in db/
- Every PR must pass the eval suite before merge
