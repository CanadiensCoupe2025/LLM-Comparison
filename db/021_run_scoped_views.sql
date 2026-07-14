-- -------------------------------------------------------------
-- Migration 021 — Run-scope the aggregation views (Grafana run picker)
-- -------------------------------------------------------------
-- The dashboards aggregated over the WHOLE results table, so a fresh
-- test never "reset" the boards — old runs' models kept showing up
-- (e.g. a stale Sonnet-4-6 on the final-decision board after an
-- Opus-vs-gpt-5.5 run). This adds a `run_id` column to the views the
-- dashboards read, so each board can filter to a single run via a
-- `$run` template variable. History is kept; nothing is deleted.
--
-- CREATE OR REPLACE VIEW can only APPEND columns (it cannot reorder or
-- retype existing ones), so `run_id` is added as the LAST column of
-- each view; the GROUP BY simply gains `res.run_id`.
--
-- `result_review` (013) already exposes run_id and `style_metrics`
-- (006) feeds no dashboard, so neither is touched here.
--
-- Apply after 020_decisions_run_id.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/021_run_scoped_views.sql
-- -------------------------------------------------------------

-- --- model_metrics (008): per (model, run) --------------------------
CREATE OR REPLACE VIEW model_metrics AS
SELECT
    m.name                              AS model,
    COUNT(*)                            AS n_results,
    AVG(res.latency_ms)::NUMERIC(10, 2) AS avg_latency_ms,
    COALESCE(SUM(res.cost), 0)          AS total_cost,
    AVG(res.judge_score)::NUMERIC(3, 1) AS avg_judge_score,
    COUNT(DISTINCT res.case_id)         AS n_cases,
    STDDEV_SAMP(res.judge_score)::NUMERIC(4, 2) AS stddev_judge_score,
    res.run_id                          AS run_id
FROM results res
JOIN models m ON m.id = res.model_id
GROUP BY m.name, res.run_id;

-- --- style_confound (012): per (model, run) -------------------------
CREATE OR REPLACE VIEW style_confound AS
SELECT
    m.name                                                      AS model,
    COUNT(res.judge_score)                                      AS n_judged,
    corr(res.judge_score, res.output_tokens)::NUMERIC(4, 3)     AS corr_score_len,
    regr_slope(res.judge_score, res.output_tokens)::NUMERIC(10, 8)
                                                                AS slope_score_per_token,
    corr(res.judge_score, res.resp_style_headers)::NUMERIC(4, 3) AS corr_score_headers,
    corr(res.judge_score, res.resp_style_bold)::NUMERIC(4, 3)    AS corr_score_bold,
    corr(res.judge_score,
         res.resp_style_ordered + res.resp_style_unordered)::NUMERIC(4, 3)
                                                                AS corr_score_lists,
    res.run_id                                                  AS run_id
FROM results res
JOIN models m ON m.id = res.model_id
WHERE res.judge_score IS NOT NULL
GROUP BY m.name, res.run_id;

-- --- model_decision_metrics (015): per (model, run) -----------------
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
WHERE res.judge_score IS NOT NULL
GROUP BY res.run_id, m.name, m.context_window;

-- --- decision_summary (016): latest decision, run-scoped join -------
CREATE OR REPLACE VIEW decision_summary AS
SELECT
    d.id                    AS decision_id,
    d.recommended_model     AS recommended_model,
    d.confidence            AS confidence,
    d.reasoning             AS reasoning,
    d.tradeoffs             AS tradeoffs,
    d.determinant_metrics   AS determinant_metrics,
    d.created_at            AS created_at,
    mdm.mean_judge_score    AS mean_judge_score,
    mdm.stddev_judge_score  AS stddev_judge_score,
    mdm.avg_total_tokens    AS avg_total_tokens,
    mdm.efficiency          AS efficiency,
    mdm.avg_latency_ms      AS avg_latency_ms,
    mdm.ctx_pct             AS ctx_pct,
    mdm.avg_cost            AS avg_cost,
    mdm.n_judged            AS n_judged,
    d.run_id                AS run_id
FROM decisions d
LEFT JOIN model_decision_metrics mdm
       ON mdm.model = d.recommended_model
      AND mdm.run_id IS NOT DISTINCT FROM d.run_id
ORDER BY d.created_at DESC, d.id DESC
LIMIT 1;

-- --- decision_by_profile (017): latest per (run, profile) ----------
CREATE OR REPLACE VIEW decision_by_profile AS
SELECT DISTINCT ON (d.run_id, d.profile)
    d.profile                                   AS profile,
    d.recommended_model                         AS recommended_model,
    d.confidence                                AS confidence,
    (d.weighted_scores -> 0 ->> 'score')::NUMERIC(6, 4)
                                                AS top_score,
    d.reasoning                                 AS reasoning,
    d.tradeoffs                                 AS tradeoffs,
    d.determinant_metrics                       AS determinant_metrics,
    d.weighted_scores                           AS weighted_scores,
    d.created_at                                AS created_at,
    mdm.mean_judge_score                        AS mean_judge_score,
    mdm.efficiency                              AS efficiency,
    mdm.avg_total_tokens                        AS avg_total_tokens,
    mdm.avg_latency_ms                          AS avg_latency_ms,
    mdm.ctx_pct                                 AS ctx_pct,
    mdm.avg_cost                                AS avg_cost,
    mdm.n_judged                                AS n_judged,
    d.run_id                                    AS run_id
FROM decisions d
LEFT JOIN model_decision_metrics mdm
       ON mdm.model = d.recommended_model
      AND mdm.run_id IS NOT DISTINCT FROM d.run_id
WHERE d.profile IS NOT NULL
ORDER BY d.run_id, d.profile, d.created_at DESC, d.id DESC;
