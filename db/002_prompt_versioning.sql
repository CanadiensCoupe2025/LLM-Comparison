-- -------------------------------------------------------------
-- Migration 002 — Prompt versioning (SCRUM-18)
-- -------------------------------------------------------------
-- Adds explicit version chaining and a history view on top of the
-- prompts table created in 001 (schema.sql / SCRUM-16).
--
-- Apply after schema.sql:
--   docker compose exec postgres psql -U llm -d llm_eval \
--     -f /db/002_prompt_versioning.sql
-- -------------------------------------------------------------

-- 1. UNIQUE(hash) was too strict — it collided across unrelated
--    prompts that happen to share content. Switch to UNIQUE(name, hash)
--    so each prompt name owns its own version space.
ALTER TABLE prompts DROP CONSTRAINT prompts_hash_key;
ALTER TABLE prompts ADD CONSTRAINT prompts_name_hash_key UNIQUE (name, hash);

-- 2. Explicit chain : each row points to the prompt version it
--    replaces. NULL means "root version" (first time this name is seen).
--    Makes the version history traversable in O(N) instead of relying
--    on created_at ordering.
ALTER TABLE prompts
    ADD COLUMN previous_version_id INTEGER REFERENCES prompts(id);

CREATE INDEX idx_prompts_name_created
    ON prompts (name, created_at);

-- 3. Human-friendly view to consult history :
--      SELECT * FROM prompts_history WHERE name = 'judge_rubric';
CREATE VIEW prompts_history AS
SELECT
    id,
    name,
    version,
    SUBSTRING(hash FROM 1 FOR 12) AS hash_short,
    hash,
    previous_version_id,
    created_at
FROM prompts
ORDER BY name, created_at;
