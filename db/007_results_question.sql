-- -------------------------------------------------------------
-- Migration 007 — Persist the asked question on each result
-- -------------------------------------------------------------
-- `results` records the model `response`, `judge_score`, and
-- `judge_reasoning`, but only a `case_id` reference back to the
-- dataset — not the actual QUESTION/PROMPT text. The Grafana
-- "Detailed results / logs" panel needs to show the question next
-- to the response and the judge's verdict, without joining back to
-- the YAML dataset files (which live outside the DB).
--
-- The runner's TaskResult already carries `question` (= case.prompt);
-- this column lets insert_result persist it.
--
-- Nullable: rows inserted before this migration leave it NULL. A
-- fresh benchmark run is needed to backfill displayed rows.
--
-- Apply after 006_style_metrics_view.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/007_results_question.sql
-- -------------------------------------------------------------

ALTER TABLE results
    ADD COLUMN question TEXT;
