#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# LLMeter — lanceur du GUI Streamlit (SCRUM-33/34).
# Reprend la gestion d'environnement éprouvée de demo.sh :
# charge .env puis réécrit DATABASE_URL pour un accès depuis l'hôte.
#
#   bash gui.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
  echo "✗ .env manquant. Copie .env.example vers .env et remplis les clés." >&2
  exit 1
fi
set -a; source .env; set +a

# .env pointe DATABASE_URL sur postgres:5432 (hostname Docker), injoignable
# depuis l'hôte. On réécrit vers localhost — le port est publié par docker-compose.
DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}"
export DATABASE_URL

# Active le venv s'il existe (même convention que demo.sh).
if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

exec streamlit run gui.py
