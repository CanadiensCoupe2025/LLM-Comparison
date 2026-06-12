# Database Schema

PostgreSQL schema for the LLM Evaluation Platform.

## Tables

### `models`
Catalogue of every LLM being evaluated.

| Column | Type | Description |
|---|---|---|
| id | SERIAL | Auto-generated unique ID |
| provider | VARCHAR(50) | Company name e.g. `Anthropic`, `OpenAI` |
| name | VARCHAR(100) | Model name e.g. `claude-sonnet-4-6` |
| version | VARCHAR(50) | Optional version string |
| input_cost | NUMERIC(10,6) | Cost per input token in USD |
| output_cost | NUMERIC(10,6) | Cost per output token in USD |
| created_at | TIMESTAMP | Row creation time |

### `prompts`
Versioned prompts with hash for traceability.

| Column | Type | Description |
|---|---|---|
| id | SERIAL | Auto-generated unique ID |
| name | VARCHAR(100) | Short prompt identifier |
| content | TEXT | Full prompt text |
| version | VARCHAR(50) | Version string e.g. `v1.0` |
| hash | VARCHAR(64) | SHA-256 hash of content for traceability |
| created_at | TIMESTAMP | Row creation time |

### `runs`
One evaluation session against a prompt and dataset.

| Column | Type | Description |
|---|---|---|
| id | SERIAL | Auto-generated unique ID |
| prompt_id | INTEGER | Foreign key → prompts.id |
| dataset | VARCHAR(100) | YAML dataset filename used |
| started_at | TIMESTAMP | When the run began |
| finished_at | TIMESTAMP | When the run completed (nullable) |

### `results`
One model's response for a given run.

| Column | Type | Description |
|---|---|---|
| id | SERIAL | Auto-generated unique ID |
| run_id | INTEGER | Foreign key → runs.id |
| model_id | INTEGER | Foreign key → models.id |
| response | TEXT | Full text response from the model |
| latency_ms | INTEGER | API response time in milliseconds |
| input_tokens | INTEGER | Number of tokens in the prompt |
| output_tokens | INTEGER | Number of tokens in the response |
| cost | NUMERIC(10,6) | Total cost of the call in USD |
| judge_score | NUMERIC(3,1) | LLM-as-judge quality score 1.0–5.0 (nullable) |
| judge_reasoning | TEXT | Judge explanation for the score (nullable) |
| created_at | TIMESTAMP | Row creation time |

## Relationships
- One prompt can have many runs
- One run can have many results
- One model can appear in many results

## How to run

Apply the schema (creates all 4 tables):
```bash
docker compose exec postgres psql -U llm -d llm_eval -f /schema.sql
```

Insert seed data:
```bash
docker compose exec postgres psql -U llm -d llm_eval -f /seed.sql
```

Verify tables exist:
```bash
docker compose exec postgres psql -U llm -d llm_eval -c "\dt"
```