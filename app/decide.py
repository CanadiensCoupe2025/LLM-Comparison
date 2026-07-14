"""
CLI for the final model recommendation, per usage profile (SCRUM-38).

Pipeline per profile: aggregate (view `model_decision_metrics`) → rank
deterministically for the profile's weights → judge LLM justifies → persist
(`decisions`) → print a readable summary.

Reproducibility (DoD #6): before calling the LLM we look up an existing decision
for the same (input_hash, prompt_id, profile). `input_hash` folds in the metrics
AND the profile weights, so the stored decision is replayed whenever nothing
changed — and editing a weight regenerates it.

The runner calls `decide_run()` automatically after every judged run
(SCRUM-38 "always shows up"); this CLI remains for re-decides, `--force`,
or deciding an older `--run`.

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
from typing import Optional, Sequence, Union

import psycopg

from app.decision import (
    Decision,
    canonical_metrics,
    decide,
    input_hash,
    load_decision_prompt,
    prompt_hash,
)
from app.logging_setup import configure_logging, get_logger
from app.profiles import DEFAULT_PROFILE, Profile, get_profile, load_profiles
from app.prompts.repository import PostgresPromptRepository
from app.prompts.sync import sync_prompts
from app.results_repository import DecisionRow, PostgresResultsRepository

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_NO_DATA = 4

DEFAULT_DECISION_MODEL = "gemini-2.5-pro"

TEMPLATES_DIR = Path(__file__).parent / "prompts" / "templates"
PROMPT_NAME = "final_decision"

log = get_logger(__name__)


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
    run_id: Optional[int],
) -> tuple[Union[Decision, DecisionRow], bool]:
    """Decide (or replay from cache) for one profile; return (decision, replayed)."""
    h_in = input_hash(metrics, profile, run_id)
    if not force:
        cached = repo.find_decision(input_hash=h_in, prompt_id=prompt_id, profile=profile.name)
        if cached is not None:
            return cached, True

    decision = decide(metrics, profile, rubric=rubric, model=judge_model)
    log.info(
        "decision computed",
        extra={
            "model": decision.recommended_model,
            "profile": profile.name,
            "confidence": decision.confidence,
        },
    )
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
        run_id=run_id,
    )
    return decision, False


def decide_run(
    conn,
    run_id: int,
    *,
    profiles: Optional[Sequence[Profile]] = None,
    judge_model: str = DEFAULT_DECISION_MODEL,
    force: bool = False,
) -> list[tuple[Union[Decision, DecisionRow], bool]]:
    """Compute (or replay from cache) the final decision for one run.

    The library seam shared by this CLI and the runner's auto-decide hook:
    one decision per profile (default: every profile in
    decision_profiles.yaml). Returns [] when the run has no judged metrics
    in `model_decision_metrics`; LLM/DB errors propagate to the caller.
    """
    if profiles is None:
        profiles = list(load_profiles().values())

    repo = PostgresResultsRepository(conn)
    prompt_repo = PostgresPromptRepository(conn)

    metrics = repo.fetch_decision_metrics(run_id)
    if not metrics:
        return []

    prompt_id = _resolve_prompt_id(prompt_repo)
    rubric = load_decision_prompt().content
    return [
        _decide_one(
            repo, metrics, profile, prompt_id, rubric, judge_model,
            force, run_id,
        )
        for profile in profiles
    ]


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
        "--run",
        type=int,
        default=None,
        help="Run id to decide over (default: the most recent run). Scopes the "
             "metrics so the decision reflects only that test's models.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the reproducibility cache and ask the judge again.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_DECISION_MODEL,
        help="Judge model used to write the justification "
             f"(default: {DEFAULT_DECISION_MODEL}).",
    )
    args = parser.parse_args(argv)
    configure_logging()

    # Resolve the requested profile(s) before opening the DB so a typo fails fast.
    try:
        if args.all_profiles:
            profiles = list(load_profiles().values())
        else:
            profiles = [get_profile(args.profile)]
    except Exception as e:  # ProfileError, file issues
        log.error("invalid profile: %s", e)
        return EXIT_CONFIG

    conn = _connect_db()
    try:
        repo = PostgresResultsRepository(conn)

        run_id = args.run if args.run is not None else repo.latest_run_id()
        if run_id is None:
            log.error("no runs in the database — run an eval first.")
            return EXIT_NO_DATA

        results = decide_run(
            conn, run_id, profiles=profiles, judge_model=args.model,
            force=args.force,
        )
        if not results:
            log.error(
                "no judged results for run #%s in `model_decision_metrics` — "
                "run with --judge first (or pick another --run).",
                run_id,
            )
            return EXIT_NO_DATA

        for decision, replayed in results:
            _print_decision(decision, replayed=replayed)
        return EXIT_OK
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
