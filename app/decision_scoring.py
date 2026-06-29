"""
Deterministic weighted ranking of models for a usage profile (SCRUM-38).

Given the per-model metrics (view `model_decision_metrics`) and a profile's
numeric weights, this produces a reproducible ranking — the recommended model
and the confidence are computed here in pure Python, NOT by the LLM. The judge
only writes the justification afterwards (see app/decision.py).

Scoring recipe
--------------
1. For each weighted metric, min-max normalise its values across the models to
   [0, 1]. Metrics where "lower is better" are flipped (1 - x) so that 1 always
   means "best". A missing value, or a metric where every model is equal, maps
   to a neutral 0.5 (no signal either way).
2. score = Σ(weight × normalised) / Σ(weight)  → always in [0, 1].
3. Rank by score; confidence comes from the top1−top2 margin.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

# True  = higher is better (quality, efficiency)
# False = lower is better  (tokens, latency, context pressure, cost, variance)
METRIC_DIRECTION: dict[str, bool] = {
    "mean_judge_score": True,
    "efficiency": True,
    "avg_total_tokens": False,
    "avg_latency_ms": False,
    "ctx_pct": False,
    "avg_cost": False,
    "stddev_judge_score": False,
}

# Margin (in normalised score points) between the top two models.
CONFIDENCE_HIGH_MARGIN = 0.15
CONFIDENCE_MED_MARGIN = 0.05


@dataclass(frozen=True)
class ScoredModel:
    model: str
    score: float
    normalized: dict[str, float]  # oriented, normalised value per weighted metric


def _to_float(value) -> Optional[float]:
    return None if value is None else float(value)


def normalize_metric(values: Sequence[Optional[float]], higher_better: bool) -> list[float]:
    """Min-max normalise to [0,1], orient so 1 = best; None / no-spread → 0.5."""
    present = [v for v in values if v is not None]
    if not present:
        return [0.5] * len(values)
    lo, hi = min(present), max(present)
    out: list[float] = []
    for v in values:
        if v is None or hi == lo:
            out.append(0.5)
            continue
        x = (v - lo) / (hi - lo)
        out.append(x if higher_better else 1.0 - x)
    return out


def rank_models(metrics: Sequence[dict], weights: dict[str, float]) -> list[ScoredModel]:
    """Rank models (best first) by the profile's weighted, normalised metrics."""
    if not metrics:
        return []
    used = {m: float(w) for m, w in weights.items() if w and float(w) > 0}
    total_w = sum(used.values())
    if total_w <= 0:
        raise ValueError("profile has no positive weights")

    models = [str(r.get("model")) for r in metrics]
    norm_cols: dict[str, list[float]] = {}
    for metric in used:
        if metric not in METRIC_DIRECTION:
            raise ValueError(f"unknown metric {metric!r}")
        column = [_to_float(r.get(metric)) for r in metrics]
        norm_cols[metric] = normalize_metric(column, METRIC_DIRECTION[metric])

    scored: list[ScoredModel] = []
    for i, model in enumerate(models):
        per = {metric: norm_cols[metric][i] for metric in used}
        raw = sum(used[metric] * per[metric] for metric in used)
        scored.append(ScoredModel(model=model, score=raw / total_w, normalized=per))

    # Deterministic order: score desc, then model name asc to break exact ties.
    scored.sort(key=lambda sm: (-sm.score, sm.model))
    return scored


def confidence_from_margin(scored: Sequence[ScoredModel]) -> str:
    """Confidence from the gap between the best and the runner-up."""
    if len(scored) < 2:
        return "faible"  # a single candidate isn't a real comparison
    margin = scored[0].score - scored[1].score
    if margin >= CONFIDENCE_HIGH_MARGIN:
        return "élevée"
    if margin >= CONFIDENCE_MED_MARGIN:
        return "moyenne"
    return "faible"
