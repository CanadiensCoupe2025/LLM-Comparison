-- -------------------------------------------------------------
-- Migration 022 — Complete-cases filter + model_metrics tokens
-- -------------------------------------------------------------
-- Fairness fix: a failed model call leaves NO `results` row (the
-- runner drops the pair; the schema has no error column), and a
-- failed judge call leaves a row with judge_score NULL. Either way
-- the models' averages ended up covering DIFFERENT question sets —
-- a model that failed a hard case got an easier average, skewing
-- every cross-model comparison and the final decision.
--
-- 1) New view `complete_cases`: the (run_id, case_id) pairs that are
--    comparable across ALL models of the run. In a judged run every
--    participating model must have ≥1 SCORED row for the case; in an
--    unjudged run, ≥1 row. Edge case (accepted): a model whose every
--    call failed has no rows at all, so it silently drops out of the
--    per-run model denominator.
-- 2) `model_metrics` is rebuilt on complete cases only, and gains two
--    columns: `n_judged` (lets the dashboards blank the stddev when no
--    case has repeated judged draws — stddev pooled across single
--    draws measures question difficulty, not sampling noise) and
--    `avg_total_tokens` (tokens are the primary cost metric). DROP +
--    CREATE, not OR REPLACE, so `run_id` can STAY LAST with the new
--    columns before it — safe because no view depends on model_metrics
--    (only the Grafana comparison/benchmark boards read it).
-- 3) `model_decision_metrics` gets the same complete-cases join via
--    CREATE OR REPLACE (identical column list — decision_summary and
--    decision_by_profile join this view, so it must NOT be dropped).
--
-- Decisions stay reproducible: `input_hash` folds the metrics, so the
-- filtered metrics hash differently and decisions regenerate on the
-- next `app.decide` — the cache never replays a pre-filter decision.
--
-- Left raw on purpose: `result_review` (triage must show failures),
-- `run_metrics` (true spend/latency of the whole run), `result_variance`
-- and `style_confound`/`style_metrics` (within-model diagnostics).
--
-- Apply after 021_run_scoped_views.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/022_complete_cases_and_model_tokens.sql
-- -------------------------------------------------------------

-- --- complete_cases: (run, case) pairs scored by every model --------
CREATE OR REPLACE VIEW complete_cases AS
WITH run_info AS (
    SELECT run_id,
           COUNT(DISTINCT model_id) AS n_models,
           COUNT(judge_score) > 0   AS is_judged
    FROM results
    GROUP BY run_id
)
SELECT res.run_id, res.case_id
FROM results res
JOIN run_info ri ON ri.run_id = res.run_id
GROUP BY res.run_id, res.case_id, ri.n_models, ri.is_judged
HAVING COUNT(DISTINCT res.model_id) = ri.n_models
   AND (NOT ri.is_judged
        OR COUNT(DISTINCT res.model_id)
             FILTER (WHERE res.judge_score IS NOT NULL) = ri.n_models);

-- --- model_metrics (008/021): complete cases + n_judged + tokens ----
-- `case_id` is nullable (pre-003 rows), hence IS NOT DISTINCT FROM.
DROP VIEW model_metrics;
CREATE VIEW model_metrics AS
SELECT
    m.name                              AS model,
    COUNT(*)                            AS n_results,
    AVG(res.latency_ms)::NUMERIC(10, 2) AS avg_latency_ms,
    COALESCE(SUM(res.cost), 0)          AS total_cost,
    AVG(res.judge_score)::NUMERIC(3, 1) AS avg_judge_score,
    COUNT(DISTINCT res.case_id)         AS n_cases,
    STDDEV_SAMP(res.judge_score)::NUMERIC(4, 2) AS stddev_judge_score,
    COUNT(res.judge_score)              AS n_judged,
    AVG(res.input_tokens + res.output_tokens)::NUMERIC(10, 1)
                                        AS avg_total_tokens,
    res.run_id                          AS run_id
FROM results res
JOIN models m ON m.id = res.model_id
JOIN complete_cases cc ON cc.run_id = res.run_id
                      AND cc.case_id IS NOT DISTINCT FROM res.case_id
GROUP BY m.name, res.run_id;

-- --- model_decision_metrics (015/021): complete cases only ----------
-- Body-only change (same 13 columns as 021): add the complete_cases
-- join so the decision compares every model on the SAME question set.
CREATE OR REPLACE VIEW model_decision_metrics AS
SELECT
    m.name                                          AS model,
    COUNT(*)                                        AS n_judged,
    COUNT(DISTINCT res.case_id)                     AS n_cases,
    AVG(res.input_tokens)::NUMERIC(10, 1)           AS avg_input_tokens,
    AVG(res.output_tokens)::NUMERIC(10, 1)          AS avg_output_tokens,
    AVG(res.input_tokens + res.output_tokens)::NUMERIC(10, 1)
                                                    AS avg_total_tokens,
    AVG(res.latency_ms)::NUMERIC(10, 2)             AS avg_latency_ms,
    AVG(res.judge_score)::NUMERIC(3, 1)             AS mean_judge_score,
    STDDEV_SAMP(res.judge_score)::NUMERIC(4, 2)     AS stddev_judge_score,
    (AVG(res.judge_score)
        / NULLIF(AVG(res.input_tokens + res.output_tokens), 0)
        * 1000)::NUMERIC(10, 4)                     AS efficiency,
    (AVG(res.input_tokens)
        / NULLIF(m.context_window, 0)
        * 100)::NUMERIC(6, 3)                       AS ctx_pct,
    AVG(res.cost)::NUMERIC(10, 6)                   AS avg_cost,
    res.run_id                                      AS run_id
FROM results res
JOIN models m ON m.id = res.model_id
JOIN complete_cases cc ON cc.run_id = res.run_id
                      AND cc.case_id IS NOT DISTINCT FROM res.case_id
WHERE res.judge_score IS NOT NULL
GROUP BY res.run_id, m.name, m.context_window;
