-- -------------------------------------------------------------
-- Migration 012 — Style-confound diagnostic view
-- -------------------------------------------------------------
-- Surfaces whether the judge's quality scores move WITH response
-- length / markdown formatting, per model. High positive correlation
-- ⇒ a model's scores MAY be inflated by being long or heavily
-- formatted rather than better.
--
-- IMPORTANT — association, NOT causation. Length is confounded with
-- difficulty (hard prompts legitimately need longer answers), and the
-- absolute judge can't difference difficulty out the way arena-hard's
-- pairwise design does. Read these as a diagnostic, not a de-biased
-- ranking. The OLS style-adjusted score (app/style_analysis.py) layers
-- on top of this.
--
-- corr()/regr_slope() are Postgres-native, so this stays dependency-free
-- and always-correct. NULLs (unjudged rows, un-extracted style) are
-- skipped by the aggregates automatically.
--
-- Apply after 011_results_response_style.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/012_style_confound_view.sql
--
-- Example :
--   SELECT * FROM style_confound ORDER BY corr_score_len DESC NULLS LAST;
-- -------------------------------------------------------------

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
                                                                AS corr_score_lists
FROM results res
JOIN models m ON m.id = res.model_id
WHERE res.judge_score IS NOT NULL
GROUP BY m.name;
