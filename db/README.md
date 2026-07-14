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
| case_id | VARCHAR(100) | Dataset case identifier (from migration 003, nullable) |
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

## Vues (SCRUM-22)

### `run_metrics`
Agrégation par run (totaux + percentiles latence). Source canonique pour
Grafana et l'analyse historique des coûts/perfs. Créée par migration 004.

| Column | Source | Description |
|---|---|---|
| run_id | runs.id | |
| dataset, prompt_id, started_at, finished_at | runs.* | |
| duration_ms | finished_at − started_at | Wall-time du run (NULL si pas fini) |
| n_results, n_models, n_cases | COUNT(results) | |
| total_cost | SUM(results.cost) | USD |
| total_input_tokens, total_output_tokens | SUM(...) | |
| avg_latency_ms, p50_latency_ms, p95_latency_ms | AGG(results.latency_ms) | |
| min_latency_ms, max_latency_ms | MIN/MAX(latency_ms) | |

Exemples :
```sql
SELECT * FROM run_metrics ORDER BY started_at DESC LIMIT 10;
SELECT dataset, AVG(total_cost) FROM run_metrics GROUP BY dataset;
```

### `complete_cases` (migration 022)
Les paires (run_id, case_id) comparables entre TOUS les modèles du run :
sur un run jugé, chaque modèle participant doit avoir ≥ 1 ligne notée pour
le cas ; sur un run non jugé, ≥ 1 ligne. Un appel modèle en échec ne laisse
AUCUNE ligne dans `results`, donc sans ce filtre chaque modèle était moyenné
sur un jeu de questions différent.

### `model_metrics` (008, réécrite par 021 puis 022)
Agrégat par (modèle, run) pour le dashboard `llm_model_comparison` —
**cas complets uniquement** (jointure sur `complete_cases`). Colonnes :
`model`, `n_results`, `avg_latency_ms`, `total_cost`, `avg_judge_score`,
`n_cases`, `stddev_judge_score`, `n_judged` (022 — masque l'écart-type sans
échantillonnage répété : `n_judged > n_cases`), `avg_total_tokens` (022 —
tokens in+out moyens par réponse), `run_id` (toujours en dernier).

## How to run

Apply the base schema (creates all 4 tables):
```bash
docker compose exec postgres psql -U llm -d llm_eval -f /schema.sql
```

Apply migrations in order:
```bash
docker compose exec postgres psql -U llm -d llm_eval -f /002_prompt_versioning.sql
docker compose exec postgres psql -U llm -d llm_eval -f /003_results_case_id.sql
docker compose exec postgres psql -U llm -d llm_eval -f /004_run_metrics_view.sql
```

Insert seed data:
```bash
docker compose exec postgres psql -U llm -d llm_eval -f /seed.sql
```

Verify tables exist:
```bash
docker compose exec postgres psql -U llm -d llm_eval -c "\dt"
```