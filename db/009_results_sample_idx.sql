-- -------------------------------------------------------------
-- Migration 009 — Repeated-sampling replicate index
-- -------------------------------------------------------------
-- LLM outputs are stochastic, so a single result per (case, model)
-- is one noisy draw, not an estimate. The runner can now evaluate
-- each (case, model) pair N times (`--samples N`); every draw is
-- persisted as its own `results` row. There is no uniqueness
-- constraint on (run_id, model_id, case_id), so N rows already store
-- cleanly — `sample_idx` just labels which replicate a row is, for
-- traceability and for spotting partial failures (fewer than N rows).
--
-- Apply after 008_model_metrics_view.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/009_results_sample_idx.sql
-- -------------------------------------------------------------

-- 1. Add the column. NOT NULL DEFAULT 0 so every pre-migration row
--    becomes "sample 0" — fully backward compatible with single-shot
--    runs (which keep producing exactly one row, sample_idx 0).
ALTER TABLE results
    ADD COLUMN sample_idx SMALLINT NOT NULL DEFAULT 0;

-- 2. Index for the variance query shape (migration 010) :
--      SELECT ... FROM results GROUP BY run_id, model_id, case_id
--    so per-pair roll-ups don't full-scan as sample counts grow.
CREATE INDEX idx_results_pair
    ON results (run_id, model_id, case_id);
