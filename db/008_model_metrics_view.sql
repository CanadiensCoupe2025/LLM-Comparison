-- -------------------------------------------------------------
-- Migration 008 — Per-model metrics view (SCRUM-30)
-- -------------------------------------------------------------
-- The model-comparison dashboard (SCRUM-30) needs latency, quality
-- and cost rolled up BY MODEL (run_metrics, migration 004, is per
-- RUN — not reusable here). This view groups every result by model.
--
-- No judge_score filter on purpose: latency/cost must include every
-- model, even ones never judged. AVG(judge_score) skips NULLs on its
-- own, so unjudged models simply get a NULL avg_judge_score.
--
-- Repeated sampling (migration 009) inserts N rows per (case, model),
-- so `n_results` becomes cases × samples — `n_cases` is the distinct
-- case count, and `stddev_judge_score` exposes run-to-run spread next
-- to the mean. `avg_judge_score` is unchanged (now an N-sample mean,
-- which is exactly the more-robust number the alert wants).
--
-- Idempotent (CREATE OR REPLACE) — re-run after applying 009 to pick
-- up the new columns.
--
-- Apply after 007_results_question.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/008_model_metrics_view.sql
--
-- Example :
--   SELECT * FROM model_metrics ORDER BY total_cost DESC;
-- -------------------------------------------------------------

-- New columns are appended at the END: CREATE OR REPLACE VIEW only
-- permits adding columns after the existing ones (it cannot reorder or
-- retype them), so replacing the view on a live DB stays valid.
CREATE OR REPLACE VIEW model_metrics AS
SELECT
    m.name                              AS model,
    COUNT(*)                            AS n_results,
    AVG(res.latency_ms)::NUMERIC(10, 2) AS avg_latency_ms,
    COALESCE(SUM(res.cost), 0)          AS total_cost,
    AVG(res.judge_score)::NUMERIC(3, 1) AS avg_judge_score,
    COUNT(DISTINCT res.case_id)         AS n_cases,
    STDDEV_SAMP(res.judge_score)::NUMERIC(4, 2) AS stddev_judge_score
FROM results res
JOIN models m ON m.id = res.model_id
GROUP BY m.name;
