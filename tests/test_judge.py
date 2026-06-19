from __future__ import annotations

from decimal import Decimal

import pytest

from app.judge import (
    JudgeParseError,
    JudgeVerdict,
    build_judge_prompt,
    judge,
    parse_verdict,
    to_db_scale,
)
from app.llm_client import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resp(content: str) -> LLMResponse:
    """A minimal LLMResponse carrying just the judge's text output."""
    return LLMResponse(
        content=content,
        reasoning=None,
        tokens_in=1,
        tokens_out=1,
        latency_ms=1.0,
        model_id="gemini-2.5-pro",
        raw=None,
    )


# ---------------------------------------------------------------------------
# to_db_scale — the 0..1 → 0..5 contract (must line up with the 3.5/5 alert)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (0.0, Decimal("0.0")),
        (0.6, Decimal("3.0")),
        (0.7, Decimal("3.5")),   # the alert boundary — must be exact
        (1.0, Decimal("5.0")),
    ],
)
def test_to_db_scale_maps_raw_to_db_scale(raw, expected):
    assert to_db_scale(raw) == expected


def test_to_db_scale_returns_decimal_not_float():
    assert isinstance(to_db_scale(0.5), Decimal)


def test_to_db_scale_quantizes_to_one_decimal():
    # 0.63 * 5 = 3.15 → rounded to one decimal place
    assert to_db_scale(0.63) == Decimal("3.2")


# ---------------------------------------------------------------------------
# parse_verdict — happy paths
# ---------------------------------------------------------------------------


def test_parse_verdict_bare_json():
    v = parse_verdict('{"score": 0.6, "reasoning": "presque correct"}')
    assert isinstance(v, JudgeVerdict)
    assert v.score == 0.6
    assert v.reasoning == "presque correct"


def test_parse_verdict_strips_markdown_fences():
    text = '```json\n{"score": 0.6, "reasoning": "ok"}\n```'
    v = parse_verdict(text)
    assert v.score == 0.6


def test_parse_verdict_accepts_integer_boundary_scores():
    """A perfect (1) or failing (0) score arrives as a JSON int, not float."""
    assert parse_verdict('{"score": 1, "reasoning": "parfait"}').score == 1
    assert parse_verdict('{"score": 0, "reasoning": "hors-sujet"}').score == 0


# ---------------------------------------------------------------------------
# parse_verdict — rejection paths
# ---------------------------------------------------------------------------


def test_parse_verdict_rejects_non_json():
    with pytest.raises(JudgeParseError):
        parse_verdict("this is not json at all")


def test_parse_verdict_rejects_score_above_one():
    with pytest.raises(JudgeParseError):
        parse_verdict('{"score": 1.5, "reasoning": "x"}')


def test_parse_verdict_rejects_negative_score():
    with pytest.raises(JudgeParseError):
        parse_verdict('{"score": -0.1, "reasoning": "x"}')


def test_parse_verdict_rejects_missing_score():
    with pytest.raises(JudgeParseError):
        parse_verdict('{"reasoning": "x"}')


def test_parse_verdict_rejects_bool_score():
    """bool is a subclass of int — must not sneak through as a number."""
    with pytest.raises(JudgeParseError):
        parse_verdict('{"score": true, "reasoning": "x"}')


def test_parse_verdict_rejects_empty_reasoning():
    with pytest.raises(JudgeParseError):
        parse_verdict('{"score": 0.5, "reasoning": "   "}')


def test_parse_verdict_rejects_non_object_json():
    """Valid JSON that isn't an object (array/number) must raise cleanly."""
    with pytest.raises(JudgeParseError):
        parse_verdict("[1, 2]")
    with pytest.raises(JudgeParseError):
        parse_verdict("42")


# ---------------------------------------------------------------------------
# build_judge_prompt — pure string assembly
# ---------------------------------------------------------------------------


def test_build_judge_prompt_includes_labelled_sections():
    prompt = build_judge_prompt("RUBRIC TEXT", "ma question", "ma réponse")
    assert "RUBRIC TEXT" in prompt
    assert "Q: ma question" in prompt
    assert "R: ma réponse" in prompt


def test_build_judge_prompt_strips_rubric_whitespace():
    prompt = build_judge_prompt("  padded rubric  ", "q", "a")
    assert prompt.startswith("padded rubric")


# ---------------------------------------------------------------------------
# judge — orchestration with an injected fake `call` (zero tokens, no network)
# ---------------------------------------------------------------------------


def test_judge_returns_parsed_verdict_via_injected_call():
    captured = {}

    def fake_call(provider, model, prompt, **kwargs):
        captured["provider"] = provider
        captured["model"] = model
        captured["prompt"] = prompt
        return _resp('{"score": 0.8, "reasoning": "bonne réponse"}')

    verdict = judge(
        "Quelle est la capitale de l'Australie ?",
        "Canberra.",
        rubric="RUBRIC",
        call=fake_call,
    )

    assert verdict.score == 0.8
    assert verdict.reasoning == "bonne réponse"
    # the judge always routes through the gemini provider
    assert captured["provider"] == "gemini"
    assert captured["model"] == "gemini-2.5-flash"
    # the candidate answer is embedded in what we send the judge
    assert "Canberra." in captured["prompt"]


def test_judge_propagates_parse_error_on_garbage_output():
    def fake_call(provider, model, prompt, **kwargs):
        return _resp("the judge rambled instead of returning JSON")

    with pytest.raises(JudgeParseError):
        judge("q", "a", rubric="RUBRIC", call=fake_call)
