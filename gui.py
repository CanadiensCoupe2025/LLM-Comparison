"""Streamlit GUI to launch an evaluation — SCRUM-33/34 demo.

Pick which models to compare and which dataset to run, click Run, and the
results land in Postgres — the same rows the Grafana dashboards read live.
A thin front-end over `app.runner.launch_run`; it does not reimplement the
runner (no argv parsing, no `sys.exit`).

Placed at the repo ROOT (like `runner.py`) so `from app.xxx import ...`
resolves: Streamlit puts the script's directory on `sys.path`, and at the
root that makes the `app/` package importable.

Launch via the wrapper that loads .env and points Postgres at localhost:

    bash gui.sh
"""
from __future__ import annotations

import os

import streamlit as st

from app.datasets import Dataset, discover_datasets
from app.llm_client import MODEL_REGISTRY
from app.runner import launch_run

DATASETS_DIR = "evaluator/datasets"
GRAFANA_URL = "http://localhost:3000"

st.set_page_config(page_title="LLMeter — lancer un eval", page_icon="⚡")
st.title("⚡ LLMeter — lancer une évaluation")
st.caption(
    "Choisis les modèles et le dataset, puis lance le run. Chaque réponse est "
    "persistée dans Postgres et alimente les dashboards Grafana en direct."
)

# --- Préflight : DATABASE_URL présent ? --------------------------------------
if not os.environ.get("DATABASE_URL"):
    st.error(
        "`DATABASE_URL` n'est pas défini. Lance le GUI via **`bash gui.sh`** "
        "(il charge `.env` et pointe Postgres sur `localhost`), après "
        "`docker compose up -d`."
    )
    st.stop()


def _label(ds: Dataset) -> str:
    """One-line picker label: name (vN) — K cas — description."""
    desc = " ".join(((ds.raw.get("dataset") or {}).get("description") or "").split())
    tail = f" — {desc[:60]}…" if desc else ""
    return f"{ds.name} (v{ds.version}) — {len(ds.cases)} cas{tail}"


# --- Sélection ---------------------------------------------------------------
models = st.multiselect("Modèles à comparer", sorted(MODEL_REGISTRY))

datasets = discover_datasets(DATASETS_DIR)
if not datasets:
    st.error(f"Aucun dataset trouvé dans `{DATASETS_DIR}`.")
    st.stop()

# Index-based options so the (unhashable) Dataset objects never hit widget state.
idx = st.selectbox(
    "Dataset", range(len(datasets)), format_func=lambda i: _label(datasets[i])
)
dataset = datasets[idx]

col1, col2 = st.columns(2)
with col1:
    do_judge = st.checkbox(
        "Juger avec Gemini (score 0–5)",
        value=False,
        help="Chaque réponse est notée par le juge LLM (SCRUM-23) et alimente "
        "les dashboards qualité. Un appel Gemini par réponse — plus lent, un "
        "peu plus cher.",
    )
with col2:
    samples = st.number_input(
        "Samples par (cas, modèle)",
        min_value=1,
        max_value=10,
        value=1,
        help="N tirages par paire pour une moyenne ± écart-type au lieu d'un "
        "tirage unique (vue result_variance).",
    )

# --- Lancement ---------------------------------------------------------------
if st.button("▶ Run", type="primary"):
    if not models:
        st.warning("Coche au moins un modèle.")
        st.stop()

    n_calls = len(dataset.cases) * len(models) * int(samples)
    with st.spinner(
        f"Évaluation de {len(models)} modèle(s) sur {len(dataset.cases)} cas"
        f" × {int(samples)} sample(s) = {n_calls} appels"
        f"{' + jugement Gemini' if do_judge else ''}…"
    ):
        try:
            run_id, outcome = launch_run(
                dataset.source_path, models, samples=int(samples), do_judge=do_judge
            )
        except Exception as e:  # message propre plutôt qu'une stack trace en démo
            st.error(f"Échec du run : {e}")
            st.stop()

    st.success(
        f"✓ Run #{run_id} terminé — {outcome.inserted} réponse(s) persistée(s)"
        f"{f', {outcome.failed} échec(s)' if outcome.failed else ''}, "
        f"coût ~${outcome.total_cost:.4f}."
    )
    # Judged run: surface the per-model mean ± stddev right in the GUI.
    stats = outcome.model_score_stats()
    if stats:
        st.markdown("**Score juge (0–5), moyenne ± écart-type :**")
        for model_key in sorted(stats):
            mean, stddev, n = stats[model_key]
            st.markdown(f"- `{model_key}` : **{mean:.2f}** ± {stddev:.2f} (n={n})")
    st.markdown(
        f"→ Ouvre **[Grafana]({GRAFANA_URL})** (dashboard *llm_model_comparison*) "
        "pour comparer latence / qualité / coût par modèle."
    )
