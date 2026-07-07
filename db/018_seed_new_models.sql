-- -------------------------------------------------------------
-- Migration 018 — seed des nouveaux modèles ajoutés à MODEL_REGISTRY
-- (Claude Sonnet 5 + GPT-5.5 / 5.4 / 5.4-mini / 5.4-nano), SCRUM-33/34.
-- -------------------------------------------------------------
-- Idempotent : ON CONFLICT DO NOTHING. Migration dédiée (et pas juste
-- une édition de seed.sql) pour que les nouvelles lignes s'appliquent
-- aussi aux bases DÉJÀ provisionnées — seed.sql ne rejoue pas les
-- nouvelles VALUES sur un volume Postgres existant.
--
-- Tarifs en USD PAR TOKEN (input / output) = prix par 1M / 1e6.
-- context_window en tokens. Aligné sur MODEL_REGISTRY (app/llm_client.py).
--   GPT-5.3 Instant écarté : non exposé sur l'API OpenAI (produit ChatGPT)
--   → remplacé par gpt-5.4-mini / gpt-5.4-nano.
-- -------------------------------------------------------------
INSERT INTO models (provider, name, version, input_cost, output_cost, context_window) VALUES
    ('Anthropic', 'claude-sonnet-5',  '2026-01', 0.0000030,   0.0000150,   1000000),
    ('OpenAI',    'gpt-5.5',          '2026-04', 0.0000050,   0.0000300,   400000),
    ('OpenAI',    'gpt-5.4',          '2026',    0.0000025,   0.0000150,   400000),
    ('OpenAI',    'gpt-5.4-mini',     '2026',    0.00000075,  0.0000045,   400000),
    ('OpenAI',    'gpt-5.4-nano',     '2026',    0.00000020,  0.00000125,  400000)
ON CONFLICT (provider, name, version) DO NOTHING;
