-- -------------------------------------------------------------
-- Migration 010 — Per-pair variance view (repeated sampling)
-- -------------------------------------------------------------
-- With `--samples N`, each (run, model, case) has N judged rows. A
-- mean alone hides how reliable a model is run-to-run, so this view
-- exposes the spread: mean, sample standard deviation, and range of
-- the judge score, plus average latency/cost across the replicates.
--
-- The regression alert (avg judge < 3.5) now evaluates `mean_score`,
-- an N-sample mean — far less likely to fire on one unlucky draw.
-- `stddev_score` lets the dashboard show a regression next to its
-- noise, and supports a stricter gate (mean_score + stddev_score < 3.5).
--
-- STDDEV_SAMP is NULL when n_samples < 2 (no spread from one point) —
-- so single-shot runs simply get a NULL stddev, which is correct.
--
-- Apply after 009_results_sample_idx.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/010_result_variance_view.sql
--
-- Example :
--   SELECT * FROM result_variance WHERE run_id = 42 ORDER BY model, case_id;
-- -------------------------------------------------------------

CREATE OR REPLACE VIEW result_variance AS
SELECT
    res.run_id                              AS run_id,
    m.name                                  AS model,
    res.case_id                             AS case_id,
    COUNT(res.judge_score)                  AS n_samples,
    AVG(res.judge_score)::NUMERIC(3, 1)     AS mean_score,
    STDDEV_SAMP(res.judge_score)::NUMERIC(4, 2) AS stddev_score,
    MIN(res.judge_score)                    AS min_score,
    MAX(res.judge_score)                    AS max_score,
    AVG(res.latency_ms)::NUMERIC(10, 2)     AS avg_latency_ms,
    AVG(res.cost)::NUMERIC(10, 6)           AS avg_cost
FROM results res
JOIN models m ON m.id = res.model_id
GROUP BY res.run_id, m.name, res.case_id;
