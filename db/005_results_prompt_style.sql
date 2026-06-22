-- -------------------------------------------------------------
-- Migration 005 — Per-result prompt style (SCRUM-37)
-- -------------------------------------------------------------
-- The prompt-style benchmark (prompt_style_benchmark.yaml) renders
-- the same base task in several styles (zero-shot, few-shot,
-- instructional, contextual, role-based). To compare quality BY
-- style across models, each result row must record which style it
-- came from — the dataset case's `style` field.
--
-- Nullable: regular (non-benchmark) runs leave it NULL.
--
-- Apply after 004_run_metrics_view.sql :
--   docker compose exec postgres psql -U llm -d llm_eval \
--     -f /db/005_results_prompt_style.sql
-- -------------------------------------------------------------

-- 1. Add the column. Nullable so non-benchmark runs (no `style` in
--    the dataset) don't violate the constraint.
ALTER TABLE results
    ADD COLUMN prompt_style VARCHAR(50);

-- 2. Index for the benchmark's aggregation shape :
--      SELECT model_id, prompt_style, AVG(judge_score)
--      FROM results GROUP BY model_id, prompt_style;
CREATE INDEX idx_results_model_style
    ON results (model_id, prompt_style);
