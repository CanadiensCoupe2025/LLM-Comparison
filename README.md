# LLM-Comparison

Plateforme d'évaluation de LLMs : compare Claude / OpenAI / DeepSeek / Gemini
sur les mêmes prompts, mesure qualité (juge LLM), latence, tokens et coût,
persiste tout dans PostgreSQL et détecte les régressions en CI.

> 📝 Le README complet (architecture, story, GIF de démo) est en cours de
> rédaction — voir SCRUM-33. Cette section couvre le démarrage et les commandes.

## Démarrage rapide

```bash
# 1. Lancer les services (Postgres + Grafana)
docker compose up -d

# 2. Synchroniser les prompts versionnés dans la base
python -m app.prompts.cli sync

# 3. Lancer la démo (run complet en ~10-15 s)
bash demo.sh
```

Grafana est sur http://localhost:3000 (dashboards comparaison modèles,
décision finale, triage qualité, styles).

## GUI — lancer un eval depuis le navigateur

Petite interface web (Streamlit) pour **choisir les modèles et le dataset**
puis lancer une évaluation d'un clic — les résultats alimentent Grafana en
direct.

```bash
# Prérequis : docker compose up -d, et un venv avec les deps du GUI
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-gui.txt
python -m app.prompts.cli sync      # une fois, si pas déjà fait

bash gui.sh                         # ouvre http://localhost:8501
```

`gui.sh` charge `.env` et pointe `DATABASE_URL` sur `localhost` (comme
`demo.sh`). Coche des modèles, choisis un dataset, clique **Run**, puis
bascule sur Grafana pour voir les métriques se remplir.

## Modèles disponibles

Les modèles testables sont ceux de `MODEL_REGISTRY`
([app/llm_client.py](app/llm_client.py)) — Claude (Opus 4.8, Sonnet 5,
Haiku 4.5, Sonnet 4.6), OpenAI (GPT-5.5, GPT-5.4, GPT-5.4-mini/nano, GPT-5,
o3), DeepSeek et Gemini. Ajouter un modèle = une entrée dans le registre +
une ligne dans `db/seed.sql` (et une migration `db/0NN_*.sql`).

## Commandes utiles

```bash
docker compose logs -f                       # logs des services
docker compose exec app pytest               # tests
docker compose exec postgres psql -U llm -d llm_eval   # accès base
python runner.py --dataset <yaml> --models <clés…>     # eval en CLI
```

## Nettoyer les données d'éval (runs de test)

Les dashboards Grafana affichent ce que contient Postgres — nettoyer la base
suffit à les « rafraîchir ». Le script fait **toujours** un backup
(`pg_dump --clean` horodaté dans `eval_backups/`) avant de supprimer, et
n'efface jamais le catalogue (`models`, `prompts`).

```bash
bash scripts/reset_db.sh --today     # supprime les runs d'AUJOURD'HUI (tests)
bash scripts/reset_db.sh --run 42    # supprime un run précis
bash scripts/reset_db.sh --all       # base vierge (runs/results/decisions)
# --yes pour sauter la confirmation ; la commande de restauration est affichée.
```
