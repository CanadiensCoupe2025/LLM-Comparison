-- -------------------------------------------------------------
-- Migration 019 — Trace each decision back to its run (run-scoping)
-- -------------------------------------------------------------
-- The final-decision chain was global: `model_decision_metrics`
-- averaged EVERY judged result across every run, and `decisions`
-- stored no `run_id`, so a persisted decision could not be tied to
-- the test that produced it. That is why the Grafana board showed a
-- stale model (e.g. Sonnet-4-6) after a fresh Opus-vs-gpt-5.5 run.
--
-- This adds `run_id` to `decisions` so a decision is scoped to one
-- run. `app/decide.py` now computes over a single run's metrics
-- (default: the latest) and stamps the run here; the run-scoped views
-- in migration 020 join on it. Historical decisions predating this
-- column keep a NULL run_id (and are hidden by the dashboards' run
-- filter). ON DELETE CASCADE: a decision is a derived artifact of its
-- run, so wiping a run (reset_db.sh --today/--run/--dataset) removes
-- its decisions too — no orphans, and no FK error blocking the delete.
--
-- Apply after 018_seed_new_models.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/019_decisions_run_id.sql
-- -------------------------------------------------------------

ALTER TABLE decisions
    ADD COLUMN IF NOT EXISTS run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE;

-- The cache/lookup is now per run as well as per profile.
CREATE INDEX IF NOT EXISTS idx_decisions_run_profile
    ON decisions (run_id, profile);
