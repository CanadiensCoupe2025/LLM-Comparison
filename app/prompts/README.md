# `app/prompts/` — Versioned prompt management (SCRUM-18)

Production prompts (the templates the runner sends to LLMs and to
the judge) live here as YAML files. Each file is the source of truth
for one prompt ; PostgreSQL stores its history.

> **Not to be confused with `evaluator/datasets/regression_v*.yaml`** —
> those are *test cases* (input + expected) consumed by the regression
> pipeline. The files in this directory are the *system prompts and
> task templates* that the runner injects into model calls.

---

## File format

```yaml
name: judge_rubric        # required — stable identifier (snake_case)
version: 1.0              # required — human-readable label
content: |                # required — the actual prompt text
  Tu es un évaluateur...

# Anything else is metadata, ignored by the hasher and the DB.
used_by:
  - evaluator/runner
```

`name` is the **identity** of the prompt across versions. Two YAML
files with the same `name` describe the same logical prompt at two
points in its history. The hash of `content` (SHA-256 of the
normalized text) is the **identity of a specific version**.

---

## Hash normalization

Before hashing, content is normalized to absorb cosmetic edits :

1. Unicode NFC (composed form).
2. CRLF / CR → LF (no editor-induced churn).
3. Strip trailing whitespace from each line.
4. Strip leading / trailing whitespace from the whole content.

A change inside any of these dimensions does **not** create a new
version. A change to a real character does. This rule is part of
the contract — see [`hasher.py`](hasher.py).

---

## CLI

Inside the app container (or with `DATABASE_URL` set locally) :

```bash
# Insert/refresh every prompt from app/prompts/templates/ into the DB
python -m app.prompts.cli sync

# List every prompt name with its latest version
python -m app.prompts.cli list

# Show the full version chain for one prompt
python -m app.prompts.cli history judge_rubric
```

`sync` is **idempotent** : running it twice in a row inserts nothing
the second time. A new version is created only when the hash of
`content` differs from every previous version of that `name`.

---

## SQL access

Same data via plain SQL (handy in Grafana / `psql`) :

```sql
-- All versions of one prompt, oldest first.
SELECT * FROM prompts_history WHERE name = 'judge_rubric';

-- Latest version of each prompt.
SELECT DISTINCT ON (name) name, version, hash, created_at
FROM prompts
ORDER BY name, created_at DESC;

-- Which prompt version produced a given run.
SELECT r.id AS run_id, p.name, p.version, p.hash
FROM runs r JOIN prompts p ON p.id = r.prompt_id
WHERE r.id = $1;
```

The view `prompts_history` and the `previous_version_id` column are
added by `db/002_prompt_versioning.sql`.

---

## Conventions for adding / modifying a prompt

- **Bump `version`** when you change `content`. The hash will force
  a new DB row regardless, but the human-readable label still matters
  for dashboards and run reports.
- **Never rename a prompt** by changing `name` — that creates a new
  identity and breaks the chain. If the prompt's purpose changed
  enough to deserve a new name, create a new file.
- **Don't manually edit DB rows** to "fix" a prompt — re-edit the
  YAML and re-run `sync`. The DB is downstream of the file.
- **Keep `content` self-contained** : no `{{ jinja }}` placeholders
  yet (out of scope for SCRUM-18 ; will come with the runner).

---

## Tests

Pytest covers the three guarantees that matter for the DoD :

- `tests/test_hasher.py` — determinism, normalization absorbs
  cosmetic edits, real edits produce different hashes.
- `tests/test_loader.py` — YAML validation (required fields, types).
- `tests/test_sync.py` — idempotency, new-version detection,
  `previous_version_id` chaining (with an in-memory fake repository,
  so no live DB needed for unit tests).
