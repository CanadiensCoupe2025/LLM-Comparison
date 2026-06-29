"""Unit tests for the deterministic weighted scoring (SCRUM-38)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.decision_scoring import (
    CONFIDENCE_HIGH_MARGIN,
    ScoredModel,
    confidence_from_margin,
    normalize_metric,
    rank_models,
)

# --- normalize_metric ---------------------------------------------------------

def test_normalize_higher_better_maps_max_to_one():
    assert normalize_metric([0.0, 5.0, 10.0], higher_better=True) == [0.0, 0.5, 1.0]


def test_normalize_lower_better_flips():
    # lowest value is best → gets 1.0
    assert normalize_metric([100.0, 200.0, 300.0], higher_better=False) == [1.0, 0.5, 0.0]


def test_normalize_all_equal_is_neutral():
    assert normalize_metric([4.0, 4.0], higher_better=True) == [0.5, 0.5]


def test_normalize_none_is_neutral():
    out = normalize_metric([None, 0.0, 10.0], higher_better=True)
    assert out[0] == 0.5 and out[1] == 0.0 and out[2] == 1.0


# --- rank_models --------------------------------------------------------------

METRICS = [
    {"model": "fast", "mean_judge_score": Decimal("3.0"), "avg_latency_ms": Decimal("500")},
    {"model": "good", "mean_judge_score": Decimal("5.0"), "avg_latency_ms": Decimal("5000")},
]


def test_rank_prioritises_latency_for_fast_weights():
    scored = rank_models(METRICS, {"avg_latency_ms": 1.0})
    assert [s.model for s in scored] == ["fast", "good"]


def test_rank_prioritises_quality_for_quality_weights():
    scored = rank_models(METRICS, {"mean_judge_score": 1.0})
    assert scored[0].model == "good"


def test_rank_blends_weights():
    # equal weights → quality(good=1, fast=0) + latency(good=0, fast=1) → tie 0.5
    scored = rank_models(METRICS, {"mean_judge_score": 1.0, "avg_latency_ms": 1.0})
    assert scored[0].score == pytest.approx(0.5)
    assert scored[1].score == pytest.approx(0.5)


def test_rank_weights_need_not_sum_to_one():
    a = rank_models(METRICS, {"avg_latency_ms": 1.0})
    b = rank_models(METRICS, {"avg_latency_ms": 5.0})  # scaled → same normalised scores
    assert [s.score for s in a] == [s.score for s in b]


def test_rank_empty_metrics_returns_empty():
    assert rank_models([], {"avg_latency_ms": 1.0}) == []


def test_rank_rejects_all_zero_weights():
    with pytest.raises(ValueError):
        rank_models(METRICS, {"avg_latency_ms": 0.0})


def test_rank_rejects_unknown_metric():
    with pytest.raises(ValueError):
        rank_models(METRICS, {"nonsense": 1.0})


def test_rank_handles_missing_value_as_neutral():
    # With a spread among present values, a missing one sits at neutral 0.5 —
    # below the best (1.0) and above the worst (0.0).
    metrics = [
        {"model": "low", "mean_judge_score": Decimal("2.0")},
        {"model": "missing", "mean_judge_score": None},
        {"model": "high", "mean_judge_score": Decimal("10.0")},
    ]
    scored = rank_models(metrics, {"mean_judge_score": 1.0})
    assert [s.model for s in scored] == ["high", "missing", "low"]
    assert dict((s.model, s.score) for s in scored)["missing"] == pytest.approx(0.5)


# --- confidence_from_margin ---------------------------------------------------

def _sm(model, score):
    return ScoredModel(model=model, score=score, normalized={})


def test_confidence_high_when_margin_wide():
    assert confidence_from_margin([_sm("a", 0.9), _sm("b", 0.5)]) == "élevée"


def test_confidence_medium_for_mid_margin():
    assert confidence_from_margin([_sm("a", 0.50), _sm("b", 0.44)]) == "moyenne"


def test_confidence_low_for_tight_margin():
    assert confidence_from_margin([_sm("a", 0.50), _sm("b", 0.49)]) == "faible"


def test_confidence_low_for_single_model():
    assert confidence_from_margin([_sm("a", 0.9)]) == "faible"


def test_high_margin_threshold_boundary_is_inclusive():
    assert confidence_from_margin([_sm("a", CONFIDENCE_HIGH_MARGIN), _sm("b", 0.0)]) == "élevée"
