# LLM Evaluation Platform

A tool that compares Claude vs OpenAI on the same prompts, scores responses,
and catches quality regressions in CI/CD.

## Architecture
- ARCHITECTURE.md → Full technical architecture of the project. Read this first
                    before making any changes. It contains the solution design,
                    component responsibilities, data model, tech choices, and
                    the full backlog traceability matrix.

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
- ARCHITECTURE.md     → Full technical architecture. Read this before anything else.
- db/schema.sql       → PostgreSQL schema (4 tables: models, prompts, runs, results)
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
- 4 tables: models → prompts → runs → results
- Foreign keys enforce referential integrity
- Judge score scale: the LLM judge returns a raw float in [0.0, 1.0]; it is
  scaled ×5 at persist time and stored in `results.judge_score` on a 0–5 scale.
- Alert threshold: average judge score below 3.5/5 triggers regression alert
  (i.e. a raw judge score below 0.7 before scaling).


## Rules
- Never commit .env
- API keys go in .env only, never hardcoded
- All SQL changes go through versioned files in db/
- Every PR must pass the eval suite before merge
