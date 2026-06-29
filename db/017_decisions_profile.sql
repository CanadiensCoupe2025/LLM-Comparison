-- -------------------------------------------------------------
-- Migration 017 — Per-profile decisions (SCRUM-38, extension)
-- -------------------------------------------------------------
-- The decision is now made for a USAGE PROFILE (student, fast, …): a
-- deterministic weighted score ranks the models per profile and the
-- recommended model can differ from one profile to another.
--
--   * profile         : which profile produced this decision (e.g.
--                       'equilibre' = the SCRUM-38 default single rec).
--   * weighted_scores : the ranking snapshot [{model, score}, …], best
--                       first, so a row is self-explanatory.
--
-- View `decision_by_profile` exposes the LATEST decision PER profile,
-- enriched with the recommended model's metrics — one row per profile
-- for the Grafana table. `decision_summary` (migration 016) is left as
-- is: it keeps showing the most recent decision overall.
--
-- Apply after 016_decisions_table_and_view.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/017_decisions_profile.sql
-- -------------------------------------------------------------

ALTER TABLE decisions ADD COLUMN IF NOT EXISTS profile         VARCHAR(50);
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS weighted_scores JSONB;

-- The cache lookup is now per profile too.
CREATE INDEX IF NOT EXISTS idx_decisions_profile
    ON decisions (input_hash, prompt_id, profile);

CREATE OR REPLACE VIEW decision_by_profile AS
SELECT DISTINCT ON (d.profile)
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
    mdm.n_judged                                AS n_judged
FROM decisions d
LEFT JOIN model_decision_metrics mdm ON mdm.model = d.recommended_model
WHERE d.profile IS NOT NULL
ORDER BY d.profile, d.created_at DESC, d.id DESC;
