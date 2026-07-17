"""
Final model recommendation, per usage profile (SCRUM-38).

Hybrid design (validated with the user):
  * A deterministic weighted score ranks the models for the chosen profile
    (app/decision_scoring.py) → the recommended model AND the confidence are
    computed here in Python, so the SAME metrics + profile always give the SAME
    recommendation (DoD #6), independent of the LLM.
  * The judge LLM (Gemini) then only WRITES THE JUSTIFICATION of that ranking,
    citing the determinant metrics and the trade-offs (DoD #2/#4).

Reproducibility is further guaranteed by a cache keyed on (`input_hash`,
prompt id): `input_hash()` folds in the metrics, the profile name AND the
profile's weights, so editing a weight invalidates the cached decision.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from app.decision_scoring import ScoredModel, confidence_from_margin, rank_models
from app.llm_client import LLMResponse, call_llm
from app.profiles import Profile, profile_fingerprint
from app.prompts.hasher import compute_hash
from app.prompts.loader import Prompt, load_prompt
from app.retry import call_with_retry

DECISION_PROMPT_PATH = Path(__file__).parent / "prompts" / "templates" / "final_decision.yaml"


class DecisionParseError(ValueError):
    """Raised when the judge's justification JSON is missing or malformed."""


@dataclass(frozen=True)
class Decision:
    recommended_model: str          # from the deterministic scoring
    confidence: str                 # from the scoring margin
    profile: str
    determinant_metrics: list[str]  # from the LLM justification
    tradeoffs: str
    reasoning: str
    weighted_scores: list[dict]     # ranking snapshot: [{model, score}, ...]
    response: Any = field(repr=False, default=None)


def load_decision_prompt() -> Prompt:
    """Load the versioned final-decision (justification) prompt."""
    return load_prompt(DECISION_PROMPT_PATH)


def prompt_hash() -> str:
    """Content hash of the versioned prompt — the prompt side of the cache key."""
    return compute_hash(load_decision_prompt().content)


# --- Reproducibility: the cache key over metrics + profile -------------------

def canonical_metrics(metrics: Sequence[dict]) -> str:
    """Stable JSON of the metrics table: rows sorted by model, keys sorted."""
    rows = sorted((dict(r) for r in metrics), key=lambda r: str(r.get("model")))
    return json.dumps(rows, sort_keys=True, default=str, ensure_ascii=False)


def input_hash(
    metrics: Sequence[dict], profile: Profile, run_id: Optional[int] = None
) -> str:
    """SHA-256 over the metrics, the profile (name + weights), AND the run.

    Same metrics but a different profile → different hash → different cached
    decision. Same everything → cache hit → the stored decision is replayed.
    Folding in `run_id` keeps two runs that happen to have identical metric
    snapshots from colliding on the same cached decision.
    """
    payload = (
        canonical_metrics(metrics)
        + "\x1e"
        + profile_fingerprint(profile)
        + "\x1e"
        + ("" if run_id is None else str(run_id))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- Prompt assembly & parsing -----------------------------------------------

def _ranking_table(scored: Sequence[ScoredModel]) -> list[dict]:
    return [{"model": s.model, "score": round(s.score, 4)} for s in scored]


def build_justification_prompt(
    rubric: str, metrics: Sequence[dict], profile: Profile, scored: Sequence[ScoredModel]
) -> str:
    """Give the judge the profile, its weights, the computed ranking and the raw
    metrics, then ask it to justify the already-decided winner."""
    ranking = _ranking_table(scored)
    return (
        f"{rubric.strip()}\n\n"
        f"PROFIL : {profile.name} — {profile.description}\n"
        f"POIDS (métrique: poids) : {json.dumps(profile.weights, sort_keys=True, ensure_ascii=False)}\n\n"
        f"CLASSEMENT PONDÉRÉ CALCULÉ (score normalisé [0,1], meilleur en premier) :\n"
        f"{json.dumps(ranking, ensure_ascii=False)}\n\n"
        f"MÉTRIQUES BRUTES (une ligne par modèle) :\n{canonical_metrics(metrics)}\n\n"
        f"Le modèle retenu est « {scored[0].model} » (score le plus élevé pour ce profil)."
    )


def parse_justification(text: str) -> dict:
    """Parse and validate the judge's justification JSON (no model pick here)."""
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    cleaned = match.group(1) if match else text
    try:
        doc = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise DecisionParseError("justification JSON could not be parsed") from e

    if not isinstance(doc, dict):
        raise DecisionParseError(f"expected a JSON object, got {type(doc).__name__}")

    reasoning = doc.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise DecisionParseError("reasoning must be a non-empty string")

    determinant = doc.get("determinant_metrics", [])
    if not isinstance(determinant, list) or not all(isinstance(x, str) for x in determinant):
        raise DecisionParseError("determinant_metrics must be a list of strings")

    tradeoffs = doc.get("tradeoffs", "")
    if not isinstance(tradeoffs, str):
        raise DecisionParseError("tradeoffs must be a string")

    return {"determinant_metrics": determinant, "tradeoffs": tradeoffs, "reasoning": reasoning}


def decide(
    metrics: Sequence[dict],
    profile: Profile,
    *,
    rubric: str | None = None,
    model: str = "gemini-2.5-pro",
    # 8192 : même raison que le juge (app/judge.py) — le thinking de
    # gemini-2.5-pro consomme ce budget avant la justification.
    max_tokens: int = 8192,
    max_retries: int = 3,
    backoff_base: float = 2.0,
    call: Callable[..., LLMResponse] = call_llm,
    sleep: Callable[[float], None] = time.sleep,
) -> Decision:
    """Rank models for `profile` (deterministic) then have the judge justify it.

    The recommended model and confidence come from the scoring; only the prose
    comes from the LLM. Transient API failures (429/503) are retried with
    exponential backoff, like app/judge.py.
    """
    if not metrics:
        raise DecisionParseError("no metrics to decide on — is anything judged yet?")

    scored = rank_models(metrics, profile.weights)
    recommended = scored[0]
    confidence = confidence_from_margin(scored)

    rubric = rubric if rubric is not None else load_decision_prompt().content
    prompt = build_justification_prompt(rubric, metrics, profile, scored)

    response = call_with_retry(
        lambda: call("gemini", model, prompt, max_tokens=max_tokens),
        max_retries=max_retries,
        backoff_base=backoff_base,
        sleep=sleep,
    )
    justification = parse_justification(response.content)
    return Decision(
        recommended_model=recommended.model,
        confidence=confidence,
        profile=profile.name,
        determinant_metrics=justification["determinant_metrics"],
        tradeoffs=justification["tradeoffs"],
        reasoning=justification["reasoning"],
        weighted_scores=_ranking_table(scored),
        response=response.content,
    )
