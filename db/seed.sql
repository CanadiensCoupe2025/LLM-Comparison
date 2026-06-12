-- -------------------------------------------------------------
-- Seed data for the LLM Evaluation Platform.
-- Run AFTER schema.sql.
-- -------------------------------------------------------------

-- -------------------------------------------------------------
-- 1. models
-- -------------------------------------------------------------
INSERT INTO models (provider, name, version, input_cost, output_cost) VALUES
    ('Anthropic', 'claude-sonnet-4-6',    '2025-09', 0.000003, 0.000015),
    ('Anthropic', 'claude-haiku-4-5',     '2025-10', 0.000001, 0.000005),
    ('OpenAI',    'gpt-4o',               '2024-08', 0.000005, 0.000015),
    ('OpenAI',    'gpt-4o-mini',          '2024-07', 0.000000, 0.000001),
    ('Google',    'gemini-1.5-pro',       '002',     0.000003, 0.000011);

-- -------------------------------------------------------------
-- 2. prompts
-- -------------------------------------------------------------
INSERT INTO prompts (name, content, version, hash) VALUES
    ('summarize_article',
     'Summarize the following article in three concise bullet points.',
     'v1.0',
     '9f1d3c7a4b2e8f0a1d6c5b4a3e2f1d0c9b8a7f6e5d4c3b2a1f0e9d8c7b6a5f4e'),
    ('classify_sentiment',
     'Classify the sentiment of the following text as positive, negative, or neutral. Respond with a single word.',
     'v1.0',
     'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678901234567890abcdef123456'),
    ('extract_entities',
     'Extract every named entity from the text below and return them as a JSON array of {name, type} objects.',
     'v0.3',
     'deadbeefcafef00d0123456789abcdef0123456789abcdef0123456789abcdef');

-- -------------------------------------------------------------
-- 3. runs
-- -------------------------------------------------------------
INSERT INTO runs (prompt_id, dataset, started_at, finished_at) VALUES
    (1, 'news_articles_50.yaml',  '2026-06-10 09:00:00', '2026-06-10 09:04:21'),
    (2, 'reviews_imdb_100.yaml',  '2026-06-10 14:30:00', '2026-06-10 14:36:12'),
    (3, 'wiki_paragraphs_25.yaml','2026-06-11 08:15:00', NULL);

-- -------------------------------------------------------------
-- 4. results
-- One result per (run, model) pair. Run 3 is still in progress
-- so only the fastest models have reported back so far.
-- -------------------------------------------------------------
INSERT INTO results
    (run_id, model_id, response, latency_ms, input_tokens, output_tokens, cost, judge_score, judge_reasoning) VALUES
    -- Run 1: summarize_article
    (1, 1, '- Central bank held rates steady.
- Inflation expected to ease in Q3.
- Markets reacted positively.',                           1820, 612, 38, 0.001406, 4.5, 'Accurate, concise, covers all key points.'),
    (1, 2, '- Rates held steady.
- Inflation easing.
- Markets up.',                                            780, 612, 22, 0.000111, 3.5, 'Correct but overly terse, missing nuance.'),
    (1, 3, '- The central bank kept interest rates unchanged at its June meeting.
- Officials project inflation will moderate in the third quarter.
- Equity markets rallied on the announcement.',          2110, 612, 48, 0.003780, 4.7, 'Thorough and well written.'),
    (1, 4, '- Rates unchanged.
- Inflation cooling.
- Stocks rose.',                                           640, 612, 19, 0.000019, 3.0, 'Too brief, omits the central bank actor.'),
    (1, 5, '- Central bank leaves rates flat.
- Q3 inflation seen lower.
- Positive market reaction.',                              1540, 612, 33, 0.002199, 4.0, 'Solid summary, slight loss of detail.'),

    -- Run 2: classify_sentiment
    (2, 1, 'positive',  410, 188, 1, 0.000579, 5.0, 'Correct label.'),
    (2, 2, 'positive',  190, 188, 1, 0.000193, 5.0, 'Correct label.'),
    (2, 3, 'Positive.', 520, 188, 3, 0.000985, 4.0, 'Correct but includes extra punctuation.'),
    (2, 4, 'positive',  170, 188, 1, 0.000001, 5.0, 'Correct label.'),
    (2, 5, 'positive',  430, 188, 1, 0.000575, 5.0, 'Correct label.'),

    -- Run 3: extract_entities (still in progress, only fast models done)
    (3, 2, '[{"name":"Marie Curie","type":"PERSON"},{"name":"Poland","type":"GPE"}]',
           880, 305, 30, 0.000455, 4.5, 'Both entities identified correctly.'),
    (3, 4, '[{"name":"Marie Curie","type":"PERSON"}]',
           720, 305, 14, 0.000014, 2.5, 'Missed the GPE entity.');
