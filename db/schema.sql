-- -------------------------------------------------------------
-- 1. models
-- Catalogue of every LLM being evaluated.
-- -------------------------------------------------------------
CREATE TABLE models (
    id          SERIAL PRIMARY KEY,
    provider    VARCHAR(50)     NOT NULL,
    name        VARCHAR(100)    NOT NULL,
    version     VARCHAR(50),
    input_cost  NUMERIC(10, 6)  NOT NULL DEFAULT 0,
    output_cost NUMERIC(10, 6)  NOT NULL DEFAULT 0,
    created_at  TIMESTAMP       NOT NULL DEFAULT NOW(),

    UNIQUE(provider, name, version)
);

-- -------------------------------------------------------------
-- 2. prompts
-- Versioned prompts with hash for traceability.
-- -------------------------------------------------------------
CREATE TABLE prompts (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100)    NOT NULL,
    content     TEXT            NOT NULL,
    version     VARCHAR(50)     NOT NULL,
    hash        VARCHAR(64)     NOT NULL UNIQUE,
    created_at  TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- -------------------------------------------------------------
-- 3. runs
-- One evaluation session against a prompt + dataset.
-- -------------------------------------------------------------
CREATE TABLE runs (
    id          SERIAL PRIMARY KEY,
    prompt_id   INTEGER         NOT NULL REFERENCES prompts(id),
    dataset     VARCHAR(100)    NOT NULL,
    started_at  TIMESTAMP       NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMP
);

-- -------------------------------------------------------------
-- 4. results
-- One model's response for a given run.
-- -------------------------------------------------------------
CREATE TABLE results (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER         NOT NULL REFERENCES runs(id),
    model_id        INTEGER         NOT NULL REFERENCES models(id),
    response        TEXT            NOT NULL,
    latency_ms      INTEGER         NOT NULL,
    input_tokens    INTEGER         NOT NULL,
    output_tokens   INTEGER         NOT NULL,
    cost            NUMERIC(10, 6)  NOT NULL,
    judge_score     NUMERIC(3, 1),
    judge_reasoning TEXT,
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW()
);