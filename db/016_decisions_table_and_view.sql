-- -------------------------------------------------------------
-- Migration 016 — Final decision: table + summary view (SCRUM-38)
-- -------------------------------------------------------------
-- Persists the judge's final model recommendation (DoD #7) so it is an
-- auditable record over time, not a console print that vanishes.
--
-- Reproducibility (DoD #6) is enforced via the cache key
-- (input_hash, prompt_id):
--   * input_hash    = SHA-256 of the canonical aggregated metrics
--                     (app/decision.py → input_hash()).
--   * prompt_id     = the exact versioned final_decision prompt row
--                     (its `hash` is the prompt side of the key).
-- The CLI looks up an existing decision for that pair and REPLAYS it
-- instead of re-querying the LLM → same dataset + same prompt = same
-- recommendation, guaranteed.
--
-- input_snapshot stores the metrics the decision was made on, so a row
-- is self-explanatory without re-running the aggregation.
--
-- Apply after 015_model_decision_metrics_view.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/016_decisions_table_and_view.sql
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS decisions (
    id                   SERIAL PRIMARY KEY,
    recommended_model    VARCHAR(100)    NOT NULL,
    confidence           VARCHAR(20)     NOT NULL,
    determinant_metrics  JSONB           NOT NULL DEFAULT '[]'::jsonb,
    tradeoffs            TEXT,
    reasoning            TEXT            NOT NULL,
    prompt_id            INTEGER         REFERENCES prompts(id),
    input_hash           VARCHAR(64)     NOT NULL,
    input_snapshot       JSONB           NOT NULL,
    created_at           TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- The cache lookup: "is there already a decision for this dataset+prompt?"
CREATE INDEX IF NOT EXISTS idx_decisions_cache
    ON decisions (input_hash, prompt_id);

-- -------------------------------------------------------------
-- decision_summary — the latest decision, enriched with the metrics
-- of the model it recommends. One row, ready for the Grafana panels
-- (recommended model + confidence + justifying metrics + reasoning).
-- -------------------------------------------------------------
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
    mdm.n_judged            AS n_judged
FROM decisions d
LEFT JOIN model_decision_metrics mdm
       ON mdm.model = d.recommended_model
ORDER BY d.created_at DESC, d.id DESC
LIMIT 1;
