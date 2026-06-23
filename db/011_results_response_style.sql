-- -------------------------------------------------------------
-- Migration 011 — Response-style features (arena-hard style control)
-- -------------------------------------------------------------
-- Captures the markdown/formatting shape of each response so we can
-- measure whether judge scores are confounded by verbosity/formatting
-- (a model winning by being long or heavily formatted, not better).
-- Ported from arena-hard-auto's add_markdown_info.py; see
-- app/style_features.py.
--
-- NOT to be confused with `results.prompt_style` (migration 005), which
-- is the prompt *phrasing* style (zero-shot/few-shot/…) — a different
-- axis. These columns are RESPONSE style, hence the `resp_style_` prefix.
--
-- Length is intentionally absent: `output_tokens` already captures it and
-- is the canonical length covariate for the style-control regression.
--
-- All columns nullable so pre-migration rows (and any row whose response
-- wasn't feature-extracted) stay NULL — same posture as 003/005/007.
--
-- Apply after 010_result_variance_view.sql :
--   docker compose exec -T postgres psql -U llm -d llm_eval \
--     < db/011_results_response_style.sql
-- -------------------------------------------------------------

ALTER TABLE results
    ADD COLUMN resp_style_headers     SMALLINT,
    ADD COLUMN resp_style_bold        SMALLINT,
    ADD COLUMN resp_style_ordered     SMALLINT,
    ADD COLUMN resp_style_unordered   SMALLINT,
    ADD COLUMN resp_style_code_blocks SMALLINT;
