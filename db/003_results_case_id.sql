-- -------------------------------------------------------------
-- Migration 003 — Per-case result tracking (SCRUM-19)
-- -------------------------------------------------------------
-- The `results` table from 001 (SCRUM-16) could only link a row
-- back to its `run` and its `model`, not to which case inside the
-- dataset produced the response. The runner (SCRUM-19) inserts one
-- result per (case, model) pair, so we need a stable per-row case
-- identifier — the dataset case `id` from the YAML.
--
-- Apply after 002_prompt_versioning.sql :
--   docker compose exec postgres psql -U llm -d llm_eval \
--     -f /db/003_results_case_id.sql
-- -------------------------------------------------------------

-- 1. Add the column. Nullable so pre-migration rows (e.g. seed data
--    inserted before SCRUM-19) don't violate the constraint.
ALTER TABLE results
    ADD COLUMN case_id VARCHAR(100);

-- 2. Index for the runner's main query shape :
--      SELECT * FROM results WHERE run_id = %s ORDER BY case_id;
--    Lets Grafana drill into one run's case-by-case breakdown without
--    a full table scan.
CREATE INDEX idx_results_run_case
    ON results (run_id, case_id);
