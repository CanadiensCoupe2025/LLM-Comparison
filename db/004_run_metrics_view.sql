-- -------------------------------------------------------------
-- Migration 004 — Vue d'agrégation par run (SCRUM-22)
-- -------------------------------------------------------------
-- Les métriques par cas/modèle sont déjà stockées par le runner
-- dans `results` (latency_ms, input_tokens, output_tokens, cost).
-- Cette vue les remonte au niveau du run : totaux, moyennes et
-- percentiles utiles pour la comparaison de coût/efficacité entre
-- runs, et future visualisation dans Grafana.
--
-- Apply après 003_results_case_id.sql :
--   docker compose exec postgres psql -U llm -d llm_eval \
--     -f /db/004_run_metrics_view.sql
--
-- Usage type :
--   SELECT * FROM run_metrics ORDER BY started_at DESC LIMIT 10;
--   SELECT total_cost, avg_latency_ms FROM run_metrics
--    WHERE dataset = 'sprint1_smoke.yaml';
-- -------------------------------------------------------------

CREATE OR REPLACE VIEW run_metrics AS
SELECT
    r.id                AS run_id,
    r.dataset,
    r.prompt_id,
    r.started_at,
    r.finished_at,

    -- Wall-time du run (NULL si le run n'est pas fini).
    EXTRACT(EPOCH FROM (r.finished_at - r.started_at)) * 1000
                        AS duration_ms,

    -- Compteurs.
    COUNT(res.id)       AS n_results,
    COUNT(DISTINCT res.model_id)
                        AS n_models,
    COUNT(DISTINCT res.case_id)
                        AS n_cases,

    -- Totaux (NULL si pas de résultats — LEFT JOIN les fait passer).
    COALESCE(SUM(res.cost),          0)        AS total_cost,
    COALESCE(SUM(res.input_tokens),  0)::BIGINT AS total_input_tokens,
    COALESCE(SUM(res.output_tokens), 0)::BIGINT AS total_output_tokens,

    -- Latence par appel : moyenne + percentiles utiles.
    AVG(res.latency_ms)::NUMERIC(10, 2)
                        AS avg_latency_ms,
    PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY res.latency_ms)
                        AS p50_latency_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY res.latency_ms)
                        AS p95_latency_ms,
    MIN(res.latency_ms) AS min_latency_ms,
    MAX(res.latency_ms) AS max_latency_ms

FROM runs r
LEFT JOIN results res ON res.run_id = r.id
GROUP BY r.id, r.dataset, r.prompt_id, r.started_at, r.finished_at;
