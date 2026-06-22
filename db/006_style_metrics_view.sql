-- -------------------------------------------------------------
-- Migration 006 — Per-style quality aggregation view (SCRUM-37)
-- -------------------------------------------------------------
-- The prompt-style benchmark (SCRUM-37) needs to compare the mean
-- judge score of each PROMPT STYLE across MODELS. Each result row
-- now carries `prompt_style` (migration 005) and `judge_score`
-- (SCRUM-23); this view rolls them up by (model, style) so the
-- runner, ad-hoc analysis, and the Grafana panel (SCRUM-30) read
-- the same shape.
--
-- Only benchmark rows that were actually judged are included
-- (prompt_style IS NOT NULL AND judge_score IS NOT NULL).
--
-- Apply after 005_results_prompt_style.sql :
--   docker compose exec postgres psql -U llm -d llm_eval \
--     -f /db/006_style_metrics_view.sql
--
-- Example :
--   SELECT * FROM style_metrics ORDER BY model, prompt_style;
-- -------------------------------------------------------------

CREATE OR REPLACE VIEW style_metrics AS
SELECT
    m.name                              AS model,
    res.prompt_style                    AS prompt_style,
    COUNT(*)                            AS n_results,
    AVG(res.judge_score)::NUMERIC(3, 1) AS avg_judge_score,
    MIN(res.judge_score)                AS min_judge_score,
    MAX(res.judge_score)                AS max_judge_score
FROM results res
JOIN models m ON m.id = res.model_id
WHERE res.prompt_style IS NOT NULL
  AND res.judge_score IS NOT NULL
GROUP BY m.name, res.prompt_style;
