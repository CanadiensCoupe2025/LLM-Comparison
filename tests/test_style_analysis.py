"""Unit tests for the OLS style-adjusted scoring core (pure numpy, no DB)."""
from __future__ import annotations

import math

import pytest

from app.style_analysis import Observation, fit_style_model


def _obs(model, case, score, tokens, headers=0, bold=0, ordered=0,
         unordered=0, code=0):
    return Observation(
        model=model, case_id=case, judge_score=score, output_tokens=tokens,
        headers=headers, bold=bold, ordered=ordered, unordered=unordered,
        code_blocks=code,
    )


def test_recovers_known_length_slope_and_neutralizes_it():
    """Construct data where score depends ONLY on length (judge bias), with
    both models equally good underneath. The adjusted scores should be ~equal
    even though raw means differ, and the length slope should be recovered."""
    obs = []
    # model A always writes short answers, model B always long — same true skill.
    # judge_score = 1.0 + 0.5 * log1p(tokens), identical rule for both models.
    for i, tokens in enumerate([10, 20, 30, 40, 50]):
        score = 1.0 + 0.5 * math.log1p(tokens)
        obs.append(_obs("A-short", f"c{i}", score, tokens))
    for i, tokens in enumerate([100, 200, 300, 400, 500]):
        score = 1.0 + 0.5 * math.log1p(tokens)
        obs.append(_obs("B-long", f"c{i}", score, tokens))

    m = fit_style_model(obs)

    # raw means differ purely because B writes longer answers.
    assert m.raw_mean["B-long"] > m.raw_mean["A-short"]
    # length slope ≈ 0.5 (the judge's verbosity bias).
    assert m.feature_slopes["log1p_tokens"] == pytest.approx(0.5, abs=1e-6)
    # once length is held at the global mean, the two models are equal.
    assert m.adjusted["A-short"] == pytest.approx(m.adjusted["B-long"], abs=1e-6)


def test_adjusted_preserves_genuine_quality_gap():
    """If one model is genuinely better at every length, the gap must survive
    style adjustment."""
    obs = []
    for i, tokens in enumerate([50, 100, 150, 200, 250]):
        base = 0.5 * math.log1p(tokens)
        obs.append(_obs("good", f"c{i}", 2.0 + base, tokens))
        obs.append(_obs("bad", f"c{i}", 1.0 + base, tokens))

    m = fit_style_model(obs)
    assert m.adjusted["good"] - m.adjusted["bad"] == pytest.approx(1.0, abs=1e-6)


def test_raises_on_empty():
    with pytest.raises(ValueError):
        fit_style_model([])
