"""Unit tests for the final-decision module (SCRUM-38, per-profile hybrid).

The pick + confidence are deterministic (scoring); the LLM only justifies and is
always injected as a fake `call`, so these tests spend zero tokens.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.decision import (
    Decision,
    DecisionParseError,
    build_justification_prompt,
    canonical_metrics,
    decide,
    input_hash,
    load_decision_prompt,
    parse_justification,
    prompt_hash,
)
from app.decision_scoring import rank_models
from app.llm_client import LLMResponse
from app.profiles import Profile
from app.prompts.hasher import compute_hash


def _resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, reasoning=None, tokens_in=1, tokens_out=1,
                       latency_ms=1.0, model_id="gemini-2.5-pro", raw=None)


# fast = low latency, lower quality ; slow_good = high quality, slow.
METRICS = [
    {"model": "fast", "mean_judge_score": Decimal("3.5"), "avg_latency_ms": Decimal("500"),
     "efficiency": Decimal("1.0"), "avg_total_tokens": Decimal("400"), "avg_cost": Decimal("0.001")},
    {"model": "slow_good", "mean_judge_score": Decimal("4.5"), "avg_latency_ms": Decimal("5000"),
     "efficiency": Decimal("0.5"), "avg_total_tokens": Decimal("900"), "avg_cost": Decimal("0.01")},
]

P_FAST = Profile("rapide", "vitesse", {"avg_latency_ms": 0.8, "mean_judge_score": 0.2})
P_QUALITY = Profile("qualite", "q", {"mean_judge_score": 1.0})

_GOOD_JSON = ('{"determinant_metrics": ["avg_latency_ms"], '
              '"tradeoffs": "vitesse priorisée sur la qualité", '
              '"reasoning": "fast répond en 500ms contre 5000ms."}')


# --- input_hash: reproducibility key over metrics + profile ------------------

def test_input_hash_is_order_independent():
    assert input_hash(METRICS, P_FAST) == input_hash(list(reversed(METRICS)), P_FAST)


def test_input_hash_changes_with_profile():
    assert input_hash(METRICS, P_FAST) != input_hash(METRICS, P_QUALITY)


def test_input_hash_changes_when_a_weight_changes():
    tweaked = Profile("rapide", "vitesse", {"avg_latency_ms": 0.7, "mean_judge_score": 0.3})
    assert input_hash(METRICS, P_FAST) != input_hash(METRICS, tweaked)


def test_input_hash_changes_with_run_id():
    # Same metrics + profile but a different run must not collide in the cache.
    assert input_hash(METRICS, P_FAST, 1) != input_hash(METRICS, P_FAST, 2)
    # Back-compat: omitting run_id stays stable (None).
    assert input_hash(METRICS, P_FAST) == input_hash(METRICS, P_FAST, None)


def test_canonical_metrics_is_deterministic_and_sorted_by_model():
    import json
    once = canonical_metrics(METRICS)
    assert once == canonical_metrics(list(reversed(METRICS)))
    assert json.loads(once)[0]["model"] == "fast"


# --- parse_justification ------------------------------------------------------

def test_parse_justification_happy():
    j = parse_justification(_GOOD_JSON)
    assert j["determinant_metrics"] == ["avg_latency_ms"]
    assert "500ms" in j["reasoning"]


def test_parse_justification_strips_fences():
    assert parse_justification(f"```json\n{_GOOD_JSON}\n```")["tradeoffs"]


def test_parse_justification_rejects_non_json():
    with pytest.raises(DecisionParseError):
        parse_justification("not json")


def test_parse_justification_rejects_empty_reasoning():
    with pytest.raises(DecisionParseError):
        parse_justification('{"reasoning": "  "}')


def test_parse_justification_rejects_non_list_determinant():
    with pytest.raises(DecisionParseError):
        parse_justification('{"reasoning": "ok", "determinant_metrics": "x"}')


# --- build_justification_prompt ----------------------------------------------

def test_build_prompt_embeds_profile_weights_and_ranking():
    scored = rank_models(METRICS, P_FAST.weights)
    prompt = build_justification_prompt("RUBRIC", METRICS, P_FAST, scored)
    assert "RUBRIC" in prompt
    assert "rapide" in prompt
    assert "avg_latency_ms" in prompt        # the weights are shown
    assert "fast" in prompt                  # the computed winner is named


# --- decide: pick is deterministic from scoring, LLM only justifies ----------

def test_decide_recommends_fast_for_fast_profile():
    d = decide(METRICS, P_FAST, rubric="R", call=lambda *a, **k: _resp(_GOOD_JSON))
    assert isinstance(d, Decision)
    assert d.recommended_model == "fast"     # lowest latency, deterministic
    assert d.profile == "rapide"
    assert d.confidence == "élevée"          # wide margin
    assert d.weighted_scores[0]["model"] == "fast"
    assert d.reasoning                        # justification came from the LLM


def test_decide_recommends_quality_model_for_quality_profile():
    d = decide(METRICS, P_QUALITY, rubric="R", call=lambda *a, **k: _resp(_GOOD_JSON))
    assert d.recommended_model == "slow_good"


def test_decide_raises_on_empty_metrics():
    with pytest.raises(DecisionParseError):
        decide([], P_FAST, rubric="R", call=lambda *a, **k: _resp(_GOOD_JSON))


def test_decide_retries_transient_then_succeeds():
    calls, slept = {"n": 0}, []

    def flaky(provider, model, prompt, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("503 UNAVAILABLE")
        return _resp(_GOOD_JSON)

    d = decide(METRICS, P_FAST, rubric="R", call=flaky, sleep=slept.append)
    assert d.recommended_model == "fast"
    assert calls["n"] == 3 and len(slept) == 2


def test_decide_does_not_retry_non_transient():
    calls = {"n": 0}

    def bad(provider, model, prompt, **kwargs):
        calls["n"] += 1
        raise RuntimeError("400 INVALID_ARGUMENT")

    with pytest.raises(RuntimeError):
        decide(METRICS, P_FAST, rubric="R", call=bad, sleep=lambda _s: None)
    assert calls["n"] == 1


# --- prompt versioning --------------------------------------------------------

def test_prompt_hash_matches_registry_hash():
    assert prompt_hash() == compute_hash(load_decision_prompt().content)


def test_final_decision_prompt_is_v2():
    p = load_decision_prompt()
    assert p.name == "final_decision"
    assert p.version == "2.0"
