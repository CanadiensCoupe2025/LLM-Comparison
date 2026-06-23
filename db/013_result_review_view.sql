-- -------------------------------------------------------------
-- Migration 013 — Quality-triage review view
-- -------------------------------------------------------------
-- Powers the "LLM Quality Triage" dashboard: every result that is
-- NOT a perfect 5, with the response, the judge's reasoning, and a
-- derived `failure_type` so the most common causes are chartable
-- instead of re-analysed by hand each time.
--
-- `failure_type` is a HEURISTIC over the French `judge_reasoning`
-- text (keyword match, first-match-wins in this order: refusal →
-- omission → form → substance). It is a triage aid, not ground
-- truth — tweak the keywords here as the judge's phrasing evolves.
-- The view exposes ALL rows (filtering to < 5 is done by the
-- dashboard via its $max_score variable, so the view stays reusable
-- for ad-hoc queries over any score band).
--
-- Apply after 012_style_confound_view.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/013_result_review_view.sql
--
-- Example :
--   SELECT failure_type, count(*) FROM result_review
--   WHERE judge_score < 5 GROUP BY failure_type ORDER BY 2 DESC;
-- -------------------------------------------------------------

CREATE OR REPLACE VIEW result_review AS
SELECT
    res.run_id                                  AS run_id,
    m.name                                      AS model,
    res.case_id                                 AS case_id,
    res.sample_idx                              AS sample_idx,
    res.judge_score                             AS judge_score,
    CASE
        WHEN res.judge_score IS NULL THEN 'Non jugé'
        WHEN res.judge_reasoning ILIKE '%refus%'
          OR res.judge_reasoning ILIKE '%décline%'
          OR res.judge_reasoning ILIKE '%clarification%'
          OR res.judge_reasoning ILIKE '%pose des questions%'
          OR res.judge_reasoning ILIKE '%précision%'        THEN 'Refus / clarification'
        WHEN res.judge_reasoning ILIKE '%omet%'
          OR res.judge_reasoning ILIKE '%incomplet%'
          OR res.judge_reasoning ILIKE '%manque%'
          OR res.judge_reasoning ILIKE '%oublie%'
          OR res.judge_reasoning ILIKE '%n''atteint pas%'   THEN 'Omission / incomplet'
        WHEN res.judge_reasoning ILIKE '%nombre de mots%'
          OR res.judge_reasoning ILIKE '%rime%'
          OR res.judge_reasoning ILIKE '%format%'
          OR res.judge_reasoning ILIKE '%longueur%'
          OR res.judge_reasoning ILIKE '%préambule%'
          OR res.judge_reasoning ILIKE '%consigne%'         THEN 'Non-respect de forme'
        WHEN res.judge_reasoning ILIKE '%erreur%'
          OR res.judge_reasoning ILIKE '%incorrect%'
          OR res.judge_reasoning ILIKE '%faux%'
          OR res.judge_reasoning ILIKE '%fausse%'
          OR res.judge_reasoning ILIKE '%incohér%'          THEN 'Erreur de fond'
        ELSE 'Autre'
    END                                         AS failure_type,
    res.output_tokens                           AS output_tokens,
    res.question                                AS question,
    res.response                                AS response,
    res.judge_reasoning                         AS judge_reasoning,
    res.created_at                              AS created_at
FROM results res
JOIN models m ON m.id = res.model_id;
