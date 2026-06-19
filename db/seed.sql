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
--   DeepSeek  : https://api-docs.deepseek.com/quick_start/pricing
-- À revérifier régulièrement — les prix bougent.
-- -------------------------------------------------------------
INSERT INTO models (provider, name, version, input_cost, output_cost) VALUES
    ('Anthropic', 'claude-sonnet-4-6',   '2025-09', 0.0000030,  0.0000150),
    ('Anthropic', 'claude-opus-4-8',     '2026-01', 0.0000150,  0.0000750),
    ('OpenAI',    'gpt-5',               '2025-08', 0.0000050,  0.0000150),
    ('OpenAI',    'o3',                  '2025-04', 0.0000150,  0.0000600),
    ('DeepSeek',  'deepseek-v4-flash',   '2026-05', 0.00000030, 0.0000010),
    ('DeepSeek',  'deepseek-v4-pro',     '2026-05', 0.0000010,  0.0000030),
    ('Google', 'gemini-2.5-pro',    '2025',     0.00000125,  0.0000100),
    ('Google', 'gemini-2.5-flash',  '2025',     0.00000030,  0.0000025)
ON CONFLICT (provider, name, version) DO NOTHING;

-- Note : les prompts sont gérés par SCRUM-18 (sync depuis YAML) :
--   python -m app.prompts.cli sync
-- Les runs et results sont créés par le runner (SCRUM-19) :
--   python runner.py --dataset … --models …
-- Pas de seed pour ces tables — elles se remplissent au fil de l'usage.
