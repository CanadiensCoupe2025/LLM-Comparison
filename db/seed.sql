-- -------------------------------------------------------------
-- Seed data for the LLM Evaluation Platform.
-- Run AFTER schema.sql + 002_prompt_versioning.sql + 003_results_case_id.sql.
-- -------------------------------------------------------------
-- Idempotent : `ON CONFLICT DO NOTHING` permet de re-jouer le seed
-- sans casser une base déjà peuplée. Important pour le workflow CI
-- et pour les développeurs qui itèrent localement.
-- -------------------------------------------------------------

-- -------------------------------------------------------------
-- 1. models — catalogue aligné sur MODEL_REGISTRY (app/llm_client.py)
-- -------------------------------------------------------------
-- Tarifs en USD par token (input / output). Sources :
--   Anthropic : https://www.anthropic.com/pricing
--   OpenAI    : https://openai.com/api/pricing/
-- À revérifier régulièrement — les prix bougent.
-- -------------------------------------------------------------
-- context_window (tokens) added by migration 014 — run seed AFTER it on a
-- fresh DB. Values mirror MODEL_REGISTRY (app/llm_client.py).
INSERT INTO models (provider, name, version, input_cost, output_cost, context_window) VALUES
    ('Anthropic', 'claude-sonnet-4-6',   '2025-09', 0.0000030,  0.0000150,  200000),
    ('Anthropic', 'claude-opus-4-8',     '2026-01', 0.0000150,  0.0000750,  200000),
    ('Anthropic', 'claude-haiku-4-5',    '2025-10', 0.0000010,  0.0000050,  200000),
    ('OpenAI',    'gpt-5',               '2025-08', 0.0000050,  0.0000150,  400000),
    ('OpenAI',    'o3',                  '2025-04', 0.0000150,  0.0000600,  200000),
    ('Google', 'gemini-2.5-pro',    '2025',     0.00000125,  0.0000100,  1048576),
    ('Google', 'gemini-2.5-flash',  '2025',     0.00000030,  0.0000025,  1048576),
    -- Modèles ajoutés pour la démo (SCRUM-33/34) — aussi dans db/018_seed_new_models.sql
    ('Anthropic', 'claude-sonnet-5',  '2026-01', 0.0000030,   0.0000150,   1000000),
    ('OpenAI',    'gpt-5.5',          '2026-04', 0.0000050,   0.0000300,   400000),
    ('OpenAI',    'gpt-5.4',          '2026',    0.0000025,   0.0000150,   400000),
    ('OpenAI',    'gpt-5.4-mini',     '2026',    0.00000075,  0.0000045,   400000),
    ('OpenAI',    'gpt-5.4-nano',     '2026',    0.00000020,  0.00000125,  400000)
ON CONFLICT (provider, name, version) DO NOTHING;

-- Note : les prompts sont gérés par SCRUM-18 (sync depuis YAML) :
--   python -m app.prompts.cli sync
-- Les runs et results sont créés par le runner (SCRUM-19) :
--   python runner.py --dataset … --models …
-- Pas de seed pour ces tables — elles se remplissent au fil de l'usage.
