-- -------------------------------------------------------------
-- Migration 014 — Context window per model (SCRUM-38)
-- -------------------------------------------------------------
-- The final-decision metrics (SCRUM-38, DoD #1) must express each
-- model's average prompt size as a PERCENTAGE of its context window.
-- That capacity lives in MODEL_REGISTRY (app/llm_client.py) but the
-- aggregation view `model_decision_metrics` (migration 015) is pure
-- SQL, so the limit has to exist as a column here to be join-able.
--
-- `context_window` is the total window in TOKENS. Backfilled below to
-- match MODEL_REGISTRY; new models get their value from db/seed.sql.
-- NULL means "unknown" → the % is reported as NULL, never a wrong number.
--
-- Apply after 013_result_review_view.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/014_models_context_window.sql
-- -------------------------------------------------------------

ALTER TABLE models
    ADD COLUMN IF NOT EXISTS context_window INTEGER;

-- Backfill existing rows (matched by name — version-agnostic on purpose:
-- the window is a property of the model family, not of a pricing date).
UPDATE models SET context_window = 200000   WHERE name = 'claude-sonnet-4-6';
UPDATE models SET context_window = 200000   WHERE name = 'claude-opus-4-8';
UPDATE models SET context_window = 200000   WHERE name = 'claude-haiku-4-5';
UPDATE models SET context_window = 400000   WHERE name = 'gpt-5';
UPDATE models SET context_window = 200000   WHERE name = 'o3';
UPDATE models SET context_window = 128000   WHERE name = 'deepseek-v4-flash';
UPDATE models SET context_window = 128000   WHERE name = 'deepseek-v4-pro';
UPDATE models SET context_window = 1048576  WHERE name = 'gemini-2.5-pro';
UPDATE models SET context_window = 1048576  WHERE name = 'gemini-2.5-flash';
