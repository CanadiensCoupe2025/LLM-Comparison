#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# LLMeter — nettoyage des données d'évaluation (backup d'abord, TOUJOURS).
#
# Supprime des runs de la base SANS toucher au catalogue (models, prompts)
# ni aux dashboards Grafana (provisionnés par fichiers — ils reflètent
# simplement l'état de la base).
#
# Usage (depuis la racine du repo) :
#   bash scripts/reset_db.sh --today          # runs d'aujourd'hui (tests) + leurs results
#   bash scripts/reset_db.sh --run 42         # un run précis + ses results
#   bash scripts/reset_db.sh --dataset d.yaml # tous les runs d'un dataset donné
#   bash scripts/reset_db.sh --all            # vide runs/results/decisions (base vierge)
#   ... --yes                                  # saute la confirmation (scripts/CI)
#
# Symétrique de scripts/dataset_snapshot.sh (export/restore par dataset) :
# snapshot d'abord, puis --dataset pour libérer la place.
#
# Chaque exécution commence par un pg_dump --clean horodaté dans
# eval_backups/ ; la commande de restauration est affichée à la fin.
# ─────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
  echo "✗ .env manquant. Copie .env.example vers .env et remplis les clés." >&2
  exit 1
fi
set -a; source .env; set +a

PSQL=(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -v ON_ERROR_STOP=1 -P pager=off)

# ─── Arguments ───────────────────────────────────────────────
MODE="${1:-}"
YES=0
RUN_ID=""
for a in "$@"; do [[ "$a" == "--yes" ]] && YES=1; done

case "$MODE" in
  --today)
    WHERE="started_at::date = CURRENT_DATE"
    LABEL="les runs d'AUJOURD'HUI"
    ;;
  --run)
    RUN_ID="${2:-}"
    if [[ ! "$RUN_ID" =~ ^[0-9]+$ ]]; then
      echo "usage: bash scripts/reset_db.sh --run <id numérique>" >&2; exit 2
    fi
    WHERE="id = $RUN_ID"
    LABEL="le run #$RUN_ID"
    ;;
  --dataset)
    DS="${2:-}"
    if [[ -z "$DS" ]]; then
      echo "usage: bash scripts/reset_db.sh --dataset <nom-de-dataset>" >&2; exit 2
    fi
    DS_ESC="${DS//\'/\'\'}"       # échappe les apostrophes pour le SQL
    WHERE="dataset = '$DS_ESC'"
    LABEL="les runs du dataset « $DS »"
    ;;
  --all)
    WHERE=""
    LABEL="TOUTES les données (runs, results, decisions)"
    ;;
  *)
    echo "usage: bash scripts/reset_db.sh --today | --run <id> | --dataset <nom> | --all [--yes]" >&2
    exit 2
    ;;
esac

# ─── 1. Backup systématique ──────────────────────────────────
# --clean --if-exists : le dump contient les DROP, donc il se restaure
# par-dessus une base existante sans manipulation préalable.
mkdir -p eval_backups
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="eval_backups/llm_eval_${STAMP}.sql"
docker compose exec -T postgres pg_dump --clean --if-exists \
  -U "$POSTGRES_USER" "$POSTGRES_DB" > "$BACKUP"
echo "✓ backup → $BACKUP ($(du -h "$BACKUP" | cut -f1 | tr -d ' '))"

# ─── 2. Aperçu de ce qui sera supprimé ───────────────────────
echo
echo "Cible : $LABEL"
if [[ "$MODE" == "--all" ]]; then
  "${PSQL[@]}" -c "
    SELECT (SELECT COUNT(*) FROM runs)      AS runs,
           (SELECT COUNT(*) FROM results)   AS results,
           (SELECT COUNT(*) FROM decisions) AS decisions;"
else
  "${PSQL[@]}" -c "
    SELECT r.id, r.dataset, r.started_at::timestamp(0),
           (SELECT COUNT(*) FROM results WHERE run_id = r.id) AS results
      FROM runs r WHERE $WHERE ORDER BY r.id;"
fi

# ─── 3. Confirmation (sauf --yes) ────────────────────────────
if [[ $YES -ne 1 ]]; then
  if [[ ! -t 0 ]]; then
    echo "✗ pas de terminal interactif — relance avec --yes pour confirmer." >&2
    exit 1
  fi
  read -r -p "Confirmer la suppression ? [y/N] " ok
  [[ "$ok" == "y" || "$ok" == "Y" ]] || { echo "annulé — rien n'a été supprimé."; exit 0; }
fi

# ─── 4. Suppression ──────────────────────────────────────────
if [[ "$MODE" == "--all" ]]; then
  # RESTART IDENTITY : les ids repartent à 1 — vraie base « fraîche ».
  # models et prompts (catalogue) ne sont PAS touchés.
  "${PSQL[@]}" -c "TRUNCATE results, runs, decisions RESTART IDENTITY CASCADE;"
else
  # results d'abord (FK vers runs), puis les runs eux-mêmes.
  "${PSQL[@]}" -c "
    DELETE FROM results WHERE run_id IN (SELECT id FROM runs WHERE $WHERE);
    DELETE FROM runs WHERE $WHERE;"
fi

echo
echo "✓ nettoyage terminé. État actuel :"
"${PSQL[@]}" -c "
  SELECT (SELECT COUNT(*) FROM runs)      AS runs,
         (SELECT COUNT(*) FROM results)   AS results,
         (SELECT COUNT(*) FROM decisions) AS decisions;"
echo "Restauration si besoin :"
echo "  docker compose exec -T postgres psql -U $POSTGRES_USER -d $POSTGRES_DB < $BACKUP"
