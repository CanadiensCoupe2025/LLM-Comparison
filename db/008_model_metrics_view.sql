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
-- Apply after 007_results_question.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/008_model_metrics_view.sql
--
-- Example :
--   SELECT * FROM model_metrics ORDER BY total_cost DESC;
-- -------------------------------------------------------------

CREATE OR REPLACE VIEW model_metrics AS
SELECT
    m.name                              AS model,
    COUNT(*)                            AS n_results,
    AVG(res.latency_ms)::NUMERIC(10, 2) AS avg_latency_ms,
    COALESCE(SUM(res.cost), 0)          AS total_cost,
    AVG(res.judge_score)::NUMERIC(3, 1) AS avg_judge_score
FROM results res
JOIN models m ON m.id = res.model_id
GROUP BY m.name;
