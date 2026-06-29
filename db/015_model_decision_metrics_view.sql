-- -------------------------------------------------------------
-- Migration 015 — Per-model decision metrics view (SCRUM-38)
-- -------------------------------------------------------------
-- The aggregation layer feeding the final decision (SCRUM-38, DoD #1).
-- Rolls EVERY judged result up by model into the exact metric set the
-- judge needs to pick a winner:
--   * avg_input_tokens / avg_output_tokens / avg_total_tokens
--       → tokens are the primary operational metric (DoD #3).
--   * avg_latency_ms
--   * mean_judge_score (+ stddev)  → quality, on the 0–5 scale.
--   * efficiency = mean_judge_score / avg_total_tokens
--       → quality bought per token; scaled ×1000 so the number is
--         readable (score points per 1k tokens). NULL if no tokens.
--   * ctx_pct = avg_input_tokens / context_window
--       → prompt size as a share of capacity (DoD #1). NULL when the
--         window is unknown (migration 014 may leave it NULL).
--   * avg_cost (USD) → DERIVED reference only (DoD #3), never primary.
--
-- Only JUDGED rows count (judge_score IS NOT NULL): a model can't be
-- recommended on quality it was never scored on. Models with zero
-- judged results simply don't appear — the decision step treats an
-- empty table as "not enough data".
--
-- Idempotent (CREATE OR REPLACE).
--
-- Apply after 014_models_context_window.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/015_model_decision_metrics_view.sql
--
-- Example :
--   SELECT * FROM model_decision_metrics ORDER BY efficiency DESC;
-- -------------------------------------------------------------

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
    -- score points per 1k total tokens (higher = more quality per token)
    (AVG(res.judge_score)
        / NULLIF(AVG(res.input_tokens + res.output_tokens), 0)
        * 1000)::NUMERIC(10, 4)                     AS efficiency,
    -- avg prompt size as % of the model's context window (NULL if unknown)
    (AVG(res.input_tokens)
        / NULLIF(m.context_window, 0)
        * 100)::NUMERIC(6, 3)                       AS ctx_pct,
    AVG(res.cost)::NUMERIC(10, 6)                   AS avg_cost
FROM results res
JOIN models m ON m.id = res.model_id
WHERE res.judge_score IS NOT NULL
GROUP BY m.name, m.context_window;
