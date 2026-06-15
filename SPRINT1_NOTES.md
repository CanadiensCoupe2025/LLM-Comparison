# Sprint 1 — Notes de validation

**Épopée :** [SCRUM-10 — Fondations](https://pideon.atlassian.net/browse/SCRUM-10)
**Ticket de validation :** [SCRUM-21](https://pideon.atlassian.net/browse/SCRUM-21)
**Date de validation :** _<!-- TODO: AAAA-MM-JJ -->_
**Validé par :** _<!-- TODO: nom -->_

---

## 1. Ce qui a été livré

| Ticket | Sujet | PR | Status |
|---|---|---|---|
| SCRUM-14 | Initialisation du monorepo | — | ✅ Done |
| SCRUM-15 | Docker Compose (app + Postgres + Grafana) | — | ✅ Done |
| SCRUM-16 | Schéma PostgreSQL (4 tables) | [#2](https://github.com/CanadiensCoupe2025/LLM-Comparison/pull/2) | ✅ Done |
| SCRUM-17 | `app/llm_client.py` (Claude, OpenAI, DeepSeek) | [#5](https://github.com/CanadiensCoupe2025/LLM-Comparison/pull/5) | ✅ Done |
| SCRUM-18 | Versioning des prompts en YAML | [#3](https://github.com/CanadiensCoupe2025/LLM-Comparison/pull/3), [#4](https://github.com/CanadiensCoupe2025/LLM-Comparison/pull/4) | ✅ Done |
| SCRUM-19 | Runner multi-modèles + persistance | [#7](https://github.com/CanadiensCoupe2025/LLM-Comparison/pull/7) | ✅ Done |
| SCRUM-20 | Pytest + couverture ≥70% | [#8](https://github.com/CanadiensCoupe2025/LLM-Comparison/pull/8) | ✅ Done |
| SCRUM-35 | Prompts de test | — | ✅ Done |
| SCRUM-21 | Validation end-to-end | _ce document_ | 🟡 En cours |

---

## 2. Validation end-to-end (SCRUM-21)

### 2.1 Préconditions vérifiées

- [ ] `.env` présent avec `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (et optionnellement `DEEPSEEK_API_KEY`)
- [ ] `docker compose up -d postgres` → conteneur sain
- [ ] Migrations appliquées dans l'ordre : `schema.sql` → `002_prompt_versioning.sql` → `003_results_case_id.sql`
- [ ] Seed appliqué : `seed.sql` (5 modèles dans `models`)
- [ ] Prompts synchronisés : `python -m app.prompts.cli sync` → `eval_system` v1.0 + `judge_rubric` v1.0 en base

### 2.2 Commande exécutée

```bash
DATABASE_URL=postgresql://llm:changeme_local@localhost:5432/llm_eval \
  python runner.py \
    --dataset evaluator/datasets/sprint1_smoke.yaml \
    --models claude-sonnet-4-6 claude-opus-4-8 gpt-5 o3 \
    --max-workers 4
```

_<!-- TODO: coller la sortie stdout/stderr ci-dessous -->_

```
<sortie>
```

### 2.3 Résultats observés en base

Requête :
```sql
SELECT r.id, m.name AS model, r.case_id, r.latency_ms,
       r.input_tokens, r.output_tokens, r.cost,
       LEFT(r.response, 80) AS response_preview
FROM results r
JOIN models m ON m.id = r.model_id
WHERE r.run_id = (SELECT MAX(id) FROM runs)
ORDER BY m.name;
```

_<!-- TODO: copier le tableau -->_

| id | model | case_id | latency_ms | in | out | cost | response_preview |
|---|---|---|---|---|---|---|---|
| ? | claude-sonnet-4-6 | smoke-capital-canada | ? | ? | ? | ? | ? |
| ? | claude-opus-4-8 | smoke-capital-canada | ? | ? | ? | ? | ? |
| ? | gpt-5 | smoke-capital-canada | ? | ? | ? | ? | ? |
| ? | o3 | smoke-capital-canada | ? | ? | ? | ? | ? |

**Coût total du run :** _<!-- TODO: $X.XXXXXX -->_
**Run id :** _<!-- TODO: -->_
**`runs.finished_at` rempli ?** _<!-- TODO: oui/non -->_

### 2.4 Smoke DeepSeek (au-delà du DoD)

DoD officiel = Claude + OpenAI uniquement. Smoke DeepSeek exécuté en plus pour vérifier les IDs `deepseek-v4-flash` / `deepseek-v4-pro` :

```bash
DATABASE_URL=postgresql://llm:changeme_local@localhost:5432/llm_eval \
  python runner.py \
    --dataset evaluator/datasets/sprint1_smoke.yaml \
    --models deepseek-v4-flash deepseek-v4-pro \
    --max-workers 2
```

_<!-- TODO: résultat / "non exécuté car pas de DEEPSEEK_API_KEY" -->_

### 2.5 Audit des clés API

DoD : « Aucune clé API n'est visible dans les logs ou le code. »

**Logs du runner :**
```bash
python runner.py ... 2>&1 | grep -iE "sk-|api[_-]?key" || echo "OK — rien de suspect"
```
_<!-- TODO: résultat -->_

**Code source :**
```bash
git grep -niE "sk-[a-z0-9]{20,}|api[_-]?key\s*=\s*['\"]" -- ':!*.example' ':!*.md' ':!SPRINT1_NOTES.md'
```
_<!-- TODO: résultat -->_

**Fichiers ignorés par git :**
```bash
grep -E "^\.env$|^\.env\." .gitignore
```
_<!-- TODO: confirmer .env est bien ignoré -->_

---

## 3. Ce qui a fonctionné

_<!-- À remplir après la validation. Exemples : -->_
- _<!-- Le runner a appelé les N modèles en parallèle sans bug -->_
- _<!-- Les résultats sont arrivés en base avec `case_id` correct -->_
- _<!-- Les coûts calculés correspondent aux pricings dans `models.input_cost` / `output_cost` -->_
- _<!-- Le système de prompts versionnés fonctionne : `runs.prompt_id` pointe vers eval_system v1.0 -->_

---

## 4. Ce qui a bloqué

_<!-- À remplir après la validation. Format : un problème + impact + workaround. -->_
- _<!-- Exemple : « ID `gpt-5-2025-08-07` rejeté par OpenAI. Workaround : … » -->_

---

## 5. Gaps connus à traiter au sprint 2

Identifiés pendant l'épopée mais hors scope SCRUM-10 :

1. **`docker compose exec app pytest` ne marche pas.** Le `Dockerfile` ne `COPY` que `app/`, donc `tests/` et `pyproject.toml` ne sont pas dans l'image. Workaround actuel : pytest en local. Fix : élargir le contexte du build OU ajouter un bind-mount dans `docker-compose.yml`.
2. **Aucun dashboard Grafana.** Le service est up, mais `dashboard/` contient juste un `.gitkeep`. Bloque la visualisation des résultats. Ticket à créer pour le sprint 2.
3. **Aucun workflow GitHub Actions.** `.github/workflows/` contient un `.gitkeep`. La couverture (`pytest --cov-fail-under=70`) est mesurée localement mais pas appliquée à la merge. Ticket à créer (SCRUM-22 ?).
4. **Adaptateur OpenAI CHAT_COMPLETIONS jamais exercé.** Aucun modèle du `MODEL_REGISTRY` n'utilise la surface Chat aujourd'hui (`gpt-5` et `o3` sont sur Responses). Code dormant ; à brancher quand un modèle gpt-4o-class sera ajouté.
5. **CLI `app/prompts/cli.py` à 0% de couverture.** Le UX est fonctionnel mais aucun test ne le pin. Suite logique de SCRUM-20.
6. **`runner.py` impose `DATABASE_URL=…@localhost…` quand exécuté depuis l'hôte.** Le `.env` par défaut pointe vers `postgres:5432` (hostname Docker interne). Devrait être documenté dans le README ou résolu via une seconde variable d'env.

---

## 6. Checklist pour le prochain développeur

Pour rejouer ce flux end-to-end depuis zéro :

```bash
# 1. Cloner et entrer dans le repo
git clone https://github.com/CanadiensCoupe2025/LLM-Comparison.git
cd LLM-Comparison

# 2. Préparer le .env
cp .env.example .env
# Éditer .env et remplir ANTHROPIC_API_KEY, OPENAI_API_KEY
# (+ DEEPSEEK_API_KEY si on veut tester DeepSeek)

# 3. Démarrer Postgres
docker compose up -d postgres

# 4. Appliquer schéma + migrations + seed (dans l'ordre !)
docker compose cp db postgres:/db   # copie /db dans le conteneur
docker compose exec postgres psql -U llm -d llm_eval -f /db/schema.sql
docker compose exec postgres psql -U llm -d llm_eval -f /db/002_prompt_versioning.sql
docker compose exec postgres psql -U llm -d llm_eval -f /db/003_results_case_id.sql
docker compose exec postgres psql -U llm -d llm_eval -f /db/seed.sql

# 5. Installer les deps Python en local
python3 -m venv .venv
source .venv/bin/activate
pip install -r app/requirements.txt

# 6. Sync des prompts (eval_system + judge_rubric en base)
DATABASE_URL=postgresql://llm:changeme_local@localhost:5432/llm_eval \
  python -m app.prompts.cli sync

# 7. Smoke run
DATABASE_URL=postgresql://llm:changeme_local@localhost:5432/llm_eval \
  python runner.py \
    --dataset evaluator/datasets/sprint1_smoke.yaml \
    --models claude-sonnet-4-6 gpt-5

# 8. Vérifier en base
docker compose exec postgres psql -U llm -d llm_eval -c \
  "SELECT case_id, model_id, latency_ms, cost FROM results \
   WHERE run_id = (SELECT MAX(id) FROM runs);"
```

---

## 7. Bilan de l'épopée

_<!-- À remplir une fois la validation terminée. 2-3 phrases sur l'état général. -->_
