# Sprint 1 — Notes de validation

**Épopée :** [SCRUM-10 — Fondations](https://pideon.atlassian.net/browse/SCRUM-10)
**Ticket de validation :** [SCRUM-21](https://pideon.atlassian.net/browse/SCRUM-21)
**Date de validation :** 2026-06-16
**Validé par :** Olivier Pigeon (+ Claude Code)

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
| SCRUM-21 | Validation end-to-end | [#9](https://github.com/CanadiensCoupe2025/LLM-Comparison/pull/9) | 🟡 En cours |

---

## 2. Validation end-to-end (SCRUM-21)

### 2.1 Préconditions vérifiées

- [x] `.env` présent avec `ANTHROPIC_API_KEY` et `OPENAI_API_KEY` (DeepSeek non testé pour cette validation)
- [x] `docker compose up -d postgres` → conteneur sain
- [x] Migrations appliquées dans l'ordre : `schema.sql` → `002_prompt_versioning.sql` → `003_results_case_id.sql`
- [x] Seed appliqué : `seed.sql` (5 modèles à l'origine, corrigé pendant la validation — voir section 4)
- [x] Prompts synchronisés : `python -m app.prompts.cli sync` → `eval_system v1.0` + `judge_rubric v1.0` en base

### 2.2 Commande exécutée

```bash
export DATABASE_URL=postgresql://llm:<password>@localhost:5432/llm_eval
python runner.py \
  --dataset evaluator/datasets/sprint1_smoke.yaml \
  --models claude-sonnet-4-6 claude-opus-4-8 gpt-5 o3
```

Sortie finale (après les correctifs de la section 4) :

```
Run id=7  dataset=sprint1_smoke v1  cases=1  models=4
  ✓ case='smoke-capital-canada' model='claude-sonnet-4-6' tokens=135/5  cost=$0.000480
  ✓ case='smoke-capital-canada' model='claude-opus-4-8'   tokens=172/17 cost=$0.003855
  ✓ case='smoke-capital-canada' model='gpt-5'             tokens=109/67 cost=$0.001550
  ✓ case='smoke-capital-canada' model='o3'                tokens=109/24 cost=$0.003075

4/4 results inserted, 0 failed.
```

**Wall time : ~8 secondes** (parallélisme confirmé — o3 seul prend 7,3 s).
**Coût total du run : $0.008960** (≈ 1 cent).

### 2.3 Résultats observés en base

```sql
SELECT r.id, m.name AS model, r.case_id, r.latency_ms,
       r.input_tokens, r.output_tokens, r.cost,
       LEFT(r.response, 60) AS response_preview
FROM results r
JOIN models m ON m.id = r.model_id
WHERE r.run_id = 7
ORDER BY m.name;
```

| id | model | case_id | latency_ms | in | out | cost | response_preview |
|---|---|---|---|---|---|---|---|
| 18 | claude-opus-4-8 | smoke-capital-canada | 2 023 | 172 | 17 | 0.003855 | La capitale du Canada est Ottawa. |
| 17 | claude-sonnet-4-6 | smoke-capital-canada | 1 437 | 135 | 5 | 0.000480 | Ottawa. |
| 19 | gpt-5 | smoke-capital-canada | 2 525 | 109 | 67 | 0.001550 | Ottawa. |
| 20 | o3 | smoke-capital-canada | 7 288 | 109 | 24 | 0.003075 | Ottawa. |

**Les 4 modèles ont identifié Ottawa.** `runs.id=7`, `prompt_id=4` (→ `eval_system`), `started_at=2026-06-16 14:43:27`, `finished_at=2026-06-16 14:43:34` (rempli — `execute_run.finally` fonctionne).

### 2.4 Smoke DeepSeek (au-delà du DoD)

**Non exécuté.** Pas de `DEEPSEEK_API_KEY` configurée pour cette validation. Le code est prêt (adaptateur + entrées registre + entrées seed.sql) ; il suffira d'obtenir une clé DeepSeek et de relancer le runner avec `--models deepseek-v4-flash deepseek-v4-pro` pour fermer ce gap. Ticket de suivi recommandé pour le sprint 2.

### 2.5 Audit des clés API

DoD : « Aucune clé API n'est visible dans les logs ou le code. »

**Logs du runner** — recherche de patterns `sk-` ou `api_key` :
```
$ python runner.py … 2>&1 | grep -iE "sk-|api[_-]?key"
OK — rien de suspect dans la sortie du runner
```

**Code source** — recherche de clés en dur :
```
$ git grep -niE "sk-[a-zA-Z0-9_-]{20,}|api[_-]?key\s*=\s*['\"]" \
    -- ':!*.example' ':!*.md' ':!SPRINT1_NOTES.md'
OK — pas de clé en dur dans le code
```

**Fichiers ignorés par git** :
```
$ grep -E "^\.env$|^\.env\." .gitignore
.env
.env
.env.*
```

✅ Les 3 vérifications passent.

---

## 3. Ce qui a fonctionné

- **Architecture Adapter + Registry** : ajouter / corriger un modèle (Opus 4.8) se fait en une ligne du registre, sans toucher au runner ni aux tests.
- **Parallélisme ThreadPoolExecutor** : 4 appels concurrents, wall-time ≈ latence du modèle le plus lent (o3 à 7,3 s), pas la somme des latences.
- **Persistance main-thread via `as_completed`** : aucun bug de concurrence sur psycopg, INSERTs séquentiels propres.
- **Migration 003 (`results.case_id`)** : chaque ligne est traçable jusqu'au cas du dataset (`SELECT … WHERE case_id = 'smoke-capital-canada'` marche directement).
- **Système de prompts versionnés (SCRUM-18)** : `runs.prompt_id=4` pointe vers `eval_system v1.0`, hash visible en base, traçabilité OK.
- **Calcul du coût** : conforme aux tarifs publics (claude-opus-4-8 = $0.000015 × 172 + $0.000075 × 17 = $0.003855 ✓).
- **Audit clés API** : zéro fuite, ni dans les logs ni dans le code.

---

## 4. Ce qui a bloqué (résolu pendant la validation)

Tous les bugs ci-dessous ont été détectés ET corrigés dans la même PR (#9). Ce sont les vraies trouvailles de SCRUM-21.

### 4.1 `db/seed.sql` désaligné de `MODEL_REGISTRY`

**Symptôme** : `error: No row in 'models' table for name='claude-opus-4-8'.`

**Cause** : SCRUM-16 (seed) ne référençait aucun des modèles choisis par SCRUM-17 (registre). Le seed insérait `gpt-4o`, `gpt-4o-mini`, `gemini-1.5-pro` (jamais utilisés) ; il manquait `claude-opus-4-8`, `gpt-5`, `o3`, et les deux DeepSeek.

**Fix** : `db/seed.sql` réécrit pour refléter exactement les 6 entrées de `MODEL_REGISTRY`, avec `ON CONFLICT (provider, name, version) DO NOTHING` pour rester idempotent.

### 4.2 SDK `openai==1.55.0` incompatible avec `httpx ≥ 0.28`

**Symptôme** : `TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`

**Cause** : OpenAI 1.55 passait `proxies=` à httpx, paramètre retiré dans httpx 0.28.

**Fix** : `app/requirements.txt` bumpé à `openai==2.41.1`. Tous les tests mockés passent toujours (les mocks ciblent l'interface publique, pas le client SDK).

### 4.3 `claude-opus-4-8` n'accepte pas `temperature`

**Symptôme** : `BadRequestError: temperature is deprecated for this model`

**Cause** : Anthropic a retiré le paramètre `temperature` pour Opus 4.8 (le modèle utilise du raisonnement comme o3/gpt-5).

**Fix** : `MODEL_REGISTRY["claude-opus-4-8"].supports_temperature = False`. Le runner cesse de passer `temperature` pour ce modèle ; tout fonctionne. Une ligne.

### 4.4 OpenAI o3 — `reasoning.summary` exige une vérification d'org

**Symptôme** : `BadRequestError: Your organization must be verified to generate reasoning summaries.`

**Cause** : OpenAI gate la génération de résumés de raisonnement derrière une vérification d'organisation (KYC). Délai de 15 min après vérification.

**Fix** : retrait de `"summary": "concise"` dans l'appel `client.responses.create(...)` — on reste sur `reasoning={"effort": "medium"}`. L'effort de raisonnement est conservé (et facturé), seul le résumé exposé est désactivé. L'extracteur `_responses` retourne `reasoning=None`, le runner gère ce cas. Ré-activable plus tard quand l'org sera vérifiée.

---

## 5. Gaps connus à traiter au sprint 2

Identifiés pendant l'épopée mais hors scope SCRUM-10 :

1. **`docker compose exec app pytest` ne marche pas.** Le `Dockerfile` ne `COPY` que `app/`, donc `tests/` et `pyproject.toml` ne sont pas dans l'image. Workaround actuel : pytest en local. Fix : élargir le contexte du build OU ajouter un bind-mount dans `docker-compose.yml`.
2. **Aucun dashboard Grafana.** Le service est up, mais `dashboard/` contient juste un `.gitkeep`. Bloque la visualisation des résultats. À planifier au sprint 2.
3. **Aucun workflow GitHub Actions.** `.github/workflows/` contient un `.gitkeep`. La couverture (`pytest --cov-fail-under=70`) est mesurée localement mais pas appliquée à la merge. Recommandation : créer un ticket pour le sprint 2.
4. **DeepSeek jamais exercé en réel.** Code prêt, registry à jour, seed à jour, mais validation reportée (pas de clé API DeepSeek). Ticket de suivi recommandé.
5. **Adaptateur OpenAI CHAT_COMPLETIONS jamais exercé.** Aucun modèle du `MODEL_REGISTRY` n'utilise la surface Chat aujourd'hui (`gpt-5` et `o3` sont sur Responses). Code dormant ; à brancher quand un modèle gpt-4o-class sera ajouté.
6. **CLI `app/prompts/cli.py` à 0% de couverture.** Le UX est fonctionnel mais aucun test ne le pin. Suite logique de SCRUM-20.
7. **`runner.py` impose `DATABASE_URL=…@localhost…` quand exécuté depuis l'hôte.** Le `.env` par défaut pointe vers `postgres:5432` (hostname Docker interne). Devrait être documenté dans le README ou résolu via une seconde variable d'env.
8. **OpenAI o3 — vérifier l'org pour réactiver les `reasoning.summary`.** Une fois la vérification faite (https://platform.openai.com/settings/organization/general), on peut ré-ajouter `"summary": "concise"` dans `OpenAIAdapter._responses` pour stocker la chaîne de pensée résumée d'o3.

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
docker compose cp db postgres:/db
docker compose exec postgres psql -U llm -d llm_eval -f /db/schema.sql
docker compose exec postgres psql -U llm -d llm_eval -f /db/002_prompt_versioning.sql
docker compose exec postgres psql -U llm -d llm_eval -f /db/003_results_case_id.sql
docker compose exec postgres psql -U llm -d llm_eval -f /db/seed.sql

# 5. Installer les deps Python en local
python3 -m venv .venv
source .venv/bin/activate
pip install -r app/requirements.txt

# 6. Sync des prompts (eval_system + judge_rubric en base)
export DATABASE_URL=postgresql://llm:<password>@localhost:5432/llm_eval
python -m app.prompts.cli sync

# 7. Smoke run
set -a; source .env; set +a   # charge ANTHROPIC_API_KEY / OPENAI_API_KEY dans le shell
export DATABASE_URL=postgresql://llm:<password>@localhost:5432/llm_eval
python runner.py \
  --dataset evaluator/datasets/sprint1_smoke.yaml \
  --models claude-sonnet-4-6 claude-opus-4-8 gpt-5 o3

# 8. Vérifier en base
docker compose exec postgres psql -U llm -d llm_eval -c \
  "SELECT case_id, model_id, latency_ms, cost FROM results \
   WHERE run_id = (SELECT MAX(id) FROM runs);"
```

---

## 7. Bilan de l'épopée

**Épopée SCRUM-10 : foundation solide.** Le flux complet — prompt versionné → runner parallèle → 4 modèles de 2 providers → résultats persistés en Postgres avec coût/latence/tokens par cas — fonctionne en moins de 10 secondes pour 1 cent.

Trois bugs réels surfaceés et corrigés *pendant* SCRUM-21 (seed désaligné, SDK obsolète, paramètre déprécié sur Opus 4.8). C'est exactement le but de l'étape de validation : trouver les écarts entre ce qu'on a écrit en mockant et ce qui marche en réalité.

Prêt pour le sprint 2 : LLM-as-judge, dashboards Grafana, et CI/CD.
