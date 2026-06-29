"""
CLI for the final model recommendation, per usage profile (SCRUM-38).

Pipeline per profile: aggregate (view `model_decision_metrics`) → rank
deterministically for the profile's weights → judge LLM justifies → persist
(`decisions`) → print a readable summary.

Reproducibility (DoD #6): before calling the LLM we look up an existing decision
for the same (input_hash, prompt_id, profile). `input_hash` folds in the metrics
AND the profile weights, so the stored decision is replayed whenever nothing
changed — and editing a weight regenerates it.

Usage:
    python -m app.decide                      # default profile 'equilibre'
    python -m app.decide --profile rapide     # one named profile
    python -m app.decide --all-profiles       # one decision per profile
    python -m app.decide --force              # ignore the cache
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Union

import psycopg

from app.decision import (
    Decision,
    canonical_metrics,
    decide,
    input_hash,
    load_decision_prompt,
    prompt_hash,
)
from app.profiles import DEFAULT_PROFILE, Profile, get_profile, load_profiles
from app.prompts.repository import PostgresPromptRepository
from app.prompts.sync import sync_prompts
from app.results_repository import DecisionRow, PostgresResultsRepository

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_NO_DATA = 4

TEMPLATES_DIR = Path(__file__).parent / "prompts" / "templates"
PROMPT_NAME = "final_decision"


def _connect_db():
    """Open a psycopg connection from DATABASE_URL. Exits with code 1 if absent."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set — check your .env or compose env_file.")
    return psycopg.connect(url)


def _resolve_prompt_id(prompt_repo: PostgresPromptRepository) -> Optional[int]:
    """Ensure the versioned final_decision prompt is registered; return its id."""
    h = prompt_hash()
    row = prompt_repo.find_by_name_and_hash(PROMPT_NAME, h)
    if row is None:
        sync_prompts(TEMPLATES_DIR, prompt_repo)
        row = prompt_repo.find_by_name_and_hash(PROMPT_NAME, h)
    return row.id if row is not None else None


def _print_decision(d: Union[Decision, DecisionRow], *, replayed: bool) -> None:
    tag = "REJOUÉE depuis le cache (reproductible)" if replayed else "NOUVELLE décision"
    top = d.weighted_scores[0]["score"] if d.weighted_scores else None
    print(f"\n=== Profil « {d.profile} » — {tag} ===")
    print(f"  Modèle recommandé : {d.recommended_model}"
          + (f"  (score {top})" if top is not None else ""))
    print(f"  Confiance         : {d.confidence}")
    metrics = ", ".join(d.determinant_metrics) if d.determinant_metrics else "—"
    print(f"  Métriques clés    : {metrics}")
    if d.tradeoffs:
        print(f"  Compromis         : {d.tradeoffs}")
    print(f"  Justification     : {d.reasoning}")


def _decide_one(
    repo: PostgresResultsRepository,
    metrics: list[dict],
    profile: Profile,
    prompt_id: Optional[int],
    rubric: str,
    judge_model: str,
    force: bool,
) -> None:
    h_in = input_hash(metrics, profile)
    if not force:
        cached = repo.find_decision(input_hash=h_in, prompt_id=prompt_id, profile=profile.name)
        if cached is not None:
            _print_decision(cached, replayed=True)
            return

    decision = decide(metrics, profile, rubric=rubric, model=judge_model)
    repo.insert_decision(
        recommended_model=decision.recommended_model,
        confidence=decision.confidence,
        determinant_metrics=decision.determinant_metrics,
        tradeoffs=decision.tradeoffs,
        reasoning=decision.reasoning,
        prompt_id=prompt_id,
        input_hash=h_in,
        input_snapshot=json.loads(canonical_metrics(metrics)),
        profile=profile.name,
        weighted_scores=decision.weighted_scores,
    )
    _print_decision(decision, replayed=False)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="decide",
        description="Recommend the optimal model per usage profile (SCRUM-38).",
    )
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        help=f"Usage profile to decide for (default: {DEFAULT_PROFILE}).",
    )
    parser.add_argument(
        "--all-profiles",
        action="store_true",
        help="Decide for every profile in decision_profiles.yaml.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the reproducibility cache and ask the judge again.",
    )
    parser.add_argument(
        "--model",
        default="gemini-2.5-pro",
        help="Judge model used to write the justification (default: gemini-2.5-pro).",
    )
    args = parser.parse_args(argv)

    # Resolve the requested profile(s) before opening the DB so a typo fails fast.
    try:
        if args.all_profiles:
            profiles = list(load_profiles().values())
        else:
            profiles = [get_profile(args.profile)]
    except Exception as e:  # ProfileError, file issues
        print(f"Profil invalide : {e}", file=sys.stderr)
        return EXIT_CONFIG

    conn = _connect_db()
    try:
        repo = PostgresResultsRepository(conn)
        prompt_repo = PostgresPromptRepository(conn)

        metrics = repo.fetch_decision_metrics()
        if not metrics:
            print(
                "Aucun résultat jugé dans `model_decision_metrics`. "
                "Lance d'abord un run avec --judge.",
                file=sys.stderr,
            )
            return EXIT_NO_DATA

        prompt_id = _resolve_prompt_id(prompt_repo)
        rubric = load_decision_prompt().content
        for profile in profiles:
            _decide_one(repo, metrics, profile, prompt_id, rubric, args.model, args.force)
        return EXIT_OK
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
