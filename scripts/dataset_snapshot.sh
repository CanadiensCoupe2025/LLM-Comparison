#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# LLMeter — snapshot & restore des données d'ÉVALUATION par DATASET.
#
# Permet d'archiver tout ce qui a été testé avec un jeu de questions
# donné (un dataset) puis de le recharger plus tard — pour comparer des
# modèles sur exactement les mêmes questions à travers le temps.
#
#   bash scripts/dataset_snapshot.sh export demo_v1.yaml
#       → écrit eval_backups/dataset_demo_v1_<stamp>.sql
#         (runs + results + decisions de ce dataset, restaurable tel quel)
#
#   bash scripts/dataset_snapshot.sh restore eval_backups/dataset_demo_v1_….sql
#       → backup complet d'abord (filet de sécurité), puis recharge le
#         snapshot : remplace les lignes existantes de CE dataset (idempotent).
#
# Le catalogue (models, prompts) n'est jamais touché et est semé de façon
# déterministe : les ids restent stables, donc les results/decisions se
# rechargent avec leurs ids d'origine sans casser les clés étrangères.
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

MODE="${1:-}"
ARG="${2:-}"

# SQL-escape single quotes in the dataset name (defensive).
esc() { printf '%s' "$1" | sed "s/'/''/g"; }

case "$MODE" in
  export)
    NAME="$ARG"
    [[ -n "$NAME" ]] || { echo "usage: dataset_snapshot.sh export <dataset>" >&2; exit 2; }
    E="$(esc "$NAME")"

    # Refuse to write an empty snapshot — surfaces a typo in the dataset name.
    N_RUNS="$("${PSQL[@]}" -tAc "SELECT COUNT(*) FROM runs WHERE dataset = '$E';" | tr -d '[:space:]')"
    if [[ "$N_RUNS" == "0" ]]; then
      echo "✗ aucun run pour le dataset '$NAME' — rien à exporter." >&2
      echo "  datasets présents :" >&2
      "${PSQL[@]}" -c "SELECT DISTINCT dataset FROM runs ORDER BY 1;" >&2
      exit 1
    fi

    mkdir -p eval_backups
    SLUG="$(printf '%s' "$NAME" | sed -E 's/\.ya?ml$//' | tr -c 'A-Za-z0-9_.-' '_')"
    STAMP="$(date +%Y%m%d_%H%M%S)"
    OUT="eval_backups/dataset_${SLUG}_${STAMP}.sql"

    # The file is self-contained and psql-restorable: it wipes this dataset's
    # rows then COPYs the snapshot back (pg_dump data-only format). `SELECT *`
    # emits columns in table order, which `COPY <table> FROM stdin` expects.
    {
      echo "-- llmeter dataset snapshot"
      echo "-- dataset: $NAME"
      echo "-- created: $STAMP"
      echo "BEGIN;"
      echo "DELETE FROM decisions WHERE run_id IN (SELECT id FROM runs WHERE dataset = '$E');"
      echo "DELETE FROM results  WHERE run_id IN (SELECT id FROM runs WHERE dataset = '$E');"
      echo "DELETE FROM runs     WHERE dataset = '$E';"
      echo "COPY runs FROM stdin;"
      "${PSQL[@]}" -c "COPY (SELECT * FROM runs WHERE dataset = '$E') TO STDOUT"
      echo "\\."
      echo "COPY results FROM stdin;"
      "${PSQL[@]}" -c "COPY (SELECT r.* FROM results r JOIN runs ru ON ru.id = r.run_id WHERE ru.dataset = '$E') TO STDOUT"
      echo "\\."
      echo "COPY decisions FROM stdin;"
      "${PSQL[@]}" -c "COPY (SELECT d.* FROM decisions d JOIN runs ru ON ru.id = d.run_id WHERE ru.dataset = '$E') TO STDOUT"
      echo "\\."
      # Bump the sequences so future inserts don't collide with restored ids.
      echo "SELECT setval('runs_id_seq',      GREATEST((SELECT COALESCE(MAX(id),1) FROM runs),      1));"
      echo "SELECT setval('results_id_seq',   GREATEST((SELECT COALESCE(MAX(id),1) FROM results),   1));"
      echo "SELECT setval('decisions_id_seq', GREATEST((SELECT COALESCE(MAX(id),1) FROM decisions), 1));"
      echo "COMMIT;"
    } > "$OUT"

    echo "✓ snapshot ($N_RUNS run(s)) → $OUT ($(du -h "$OUT" | cut -f1 | tr -d ' '))"
    echo "Restauration : bash scripts/dataset_snapshot.sh restore $OUT"
    ;;

  restore)
    FILE="$ARG"
    [[ -f "$FILE" ]] || { echo "usage: dataset_snapshot.sh restore <fichier.sql>" >&2; exit 2; }

    # Filet de sécurité : dump complet avant de modifier la base (comme reset_db.sh).
    mkdir -p eval_backups
    STAMP="$(date +%Y%m%d_%H%M%S)"
    BACKUP="eval_backups/pre_restore_${STAMP}.sql"
    docker compose exec -T postgres pg_dump --clean --if-exists \
      -U "$POSTGRES_USER" "$POSTGRES_DB" > "$BACKUP"
    echo "✓ backup complet → $BACKUP ($(du -h "$BACKUP" | cut -f1 | tr -d ' '))"

    head -3 "$FILE" | sed 's/^/  /'
    # Le fichier est transactionnel (BEGIN…COMMIT) : une erreur annule tout.
    "${PSQL[@]}" < "$FILE"
    echo "✓ restauration terminée depuis $FILE"
    "${PSQL[@]}" -c "
      SELECT (SELECT COUNT(*) FROM runs)      AS runs,
             (SELECT COUNT(*) FROM results)   AS results,
             (SELECT COUNT(*) FROM decisions) AS decisions;"
    ;;

  *)
    echo "usage: bash scripts/dataset_snapshot.sh export <dataset> | restore <fichier.sql>" >&2
    exit 2
    ;;
esac
