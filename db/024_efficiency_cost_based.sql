-- -------------------------------------------------------------
-- Migration 024 — efficiency passe de « pts / 1k tokens » à « pts / $ ».
--
-- Motivation : les tokenizers diffèrent entre fournisseurs (~30 % d'écart
-- pour le même texte) et les tokens de raisonnement cachés des GPT-5.x
-- gonflent leur dénominateur — le ratio par tokens n'est donc pas
-- comparable inter-fournisseurs. Le prix par token étant propre à chaque
-- fournisseur, le coût absorbe la différence de tokenizer :
--     efficiency = mean_judge_score / avg_cost   (points de qualité par USD)
-- Plus haut = mieux (inchangé — les poids des profils restent valides,
-- le scoring min-max normalise l'échelle).
--
-- Dépend de la justesse de la table models (input_cost/output_cost) —
-- cf. migration 023. Body-only : mêmes 13 colonnes/types que 022
-- (CREATE OR REPLACE exige la même liste ; run_id reste dernier).
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
    (AVG(res.judge_score)
        / NULLIF(AVG(res.cost), 0))::NUMERIC(10, 4) AS efficiency,
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
