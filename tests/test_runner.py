"""Tests for the runner orchestrator.

The runner is mocked at two seams:
- `app.runner.call_llm` is replaced with a fake that returns synthetic
  `LLMResponse` instances — no API key, no network.
- `ResultsRepository` is replaced with `FakeResultsRepository`, a tiny
  in-memory store that records every insert. Pattern matches
  `tests/test_sync.py`'s FakeRepo for prompts.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest

from app.datasets import Case, Dataset
from app.llm_client import LLMResponse
from app.results_repository import ModelNotFoundError, ModelRow
from app.runner import (
    RunOutcome,
    build_prompt,
    compute_cost,
    execute_run,
    main,
    regression_failures,
    resolve_models,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _InsertedRow:
    run_id: int
    model_id: int
    case_id: str
    response: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost: Decimal
    question: str | None = None
    prompt_style: str | None = None
    sample_idx: int = 0
    resp_style_headers: int | None = None
    resp_style_bold: int | None = None
    resp_style_ordered: int | None = None
    resp_style_unordered: int | None = None
    resp_style_code_blocks: int | None = None


class FakeResultsRepository:
    """In-memory ResultsRepository — list-of-rows pretending to be tables."""

    def __init__(self, models: dict[str, ModelRow]):
        self._models = models
        self.inserts: list[_InsertedRow] = []
        self.judge_updates: list[dict[str, Any]] = []
        self.runs: dict[int, dict[str, Any]] = {}
        self.finalized: set[int] = set()
        self._next_run_id = 100
        self._next_result_id = 1

    def lookup_model(self, name: str) -> ModelRow:
        try:
            return self._models[name]
        except KeyError as e:
            raise ModelNotFoundError(name) from e

    def create_run(self, prompt_id: int, dataset: str) -> int:
        rid = self._next_run_id
        self._next_run_id += 1
        self.runs[rid] = {"prompt_id": prompt_id, "dataset": dataset}
        return rid

    def insert_result(self, **kw) -> int:
        self.inserts.append(_InsertedRow(**kw))
        rid = self._next_result_id
        self._next_result_id += 1
        return rid

    def update_judge(self, *, result_id: int, judge_score, judge_reasoning: str) -> None:
        self.judge_updates.append(
            {
                "result_id": result_id,
                "judge_score": judge_score,
                "judge_reasoning": judge_reasoning,
            }
        )

    def finalize_run(self, run_id: int) -> None:
        self.finalized.add(run_id)


def _model(*, id: int, name: str, in_cost: str, out_cost: str) -> ModelRow:
    return ModelRow(
        id=id,
        provider="anthropic" if "claude" in name else ("openai" if "gpt" in name or name == "o3" else "deepseek"),
        name=name,
        version=None,
        input_cost=Decimal(in_cost),
        output_cost=Decimal(out_cost),
    )


def _case(cid: str, prompt: str) -> Case:
    return Case(id=cid, prompt=prompt, raw={"id": cid, "prompt": prompt})


def _dataset(cases: list[Case]) -> Dataset:
    from pathlib import Path
    return Dataset(name="t", version=1, cases=cases, source_path=Path("/tmp/t.yaml"), raw={})


def _fake_response(content: str, tokens_in: int, tokens_out: int) -> LLMResponse:
    return LLMResponse(
        content=content,
        reasoning=None,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=42.0,
        model_id="stub",
        raw={},
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_build_prompt_prepends_system():
    out = build_prompt("SYS", "USER")
    assert out.startswith("SYS")
    assert out.endswith("USER")
    assert "\n\n" in out


def test_compute_cost_is_input_times_in_plus_output_times_out():
    m = _model(id=1, name="claude-sonnet-4-6", in_cost="0.000003", out_cost="0.000015")
    cost = compute_cost(input_tokens=1000, output_tokens=500, model=m)
    assert cost == Decimal("0.000003") * 1000 + Decimal("0.000015") * 500


def test_resolve_models_returns_pairs_in_order():
    models = {
        "claude-sonnet-4-6": _model(id=1, name="claude-sonnet-4-6", in_cost="0.000003", out_cost="0.000015"),
        "deepseek-v4-flash": _model(id=2, name="deepseek-v4-flash", in_cost="0", out_cost="0"),
    }
    repo = FakeResultsRepository(models)
    pairs = resolve_models(["deepseek-v4-flash", "claude-sonnet-4-6"], repo)
    assert [k for k, _ in pairs] == ["deepseek-v4-flash", "claude-sonnet-4-6"]


def test_resolve_models_rejects_unknown_registry_key():
    repo = FakeResultsRepository({})
    with pytest.raises(ValueError, match="Unknown model key"):
        resolve_models(["bogus-model"], repo)


def test_resolve_models_surfaces_missing_db_row():
    repo = FakeResultsRepository({})  # registry knows the key, DB doesn't.
    with pytest.raises(ModelNotFoundError):
        resolve_models(["claude-sonnet-4-6"], repo)


# ---------------------------------------------------------------------------
# execute_run — the orchestrator loop
# ---------------------------------------------------------------------------


def test_execute_run_inserts_one_row_per_case_model_pair():
    cases = [_case("c1", "prompt one"), _case("c2", "prompt two")]
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0.000003", out_cost="0.000015")),
        ("deepseek-v4-flash", _model(id=2, name="deepseek-v4-flash", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})
    fake_call = lambda *_a, **_k: _fake_response("ok", 10, 5)

    outcome = execute_run(
        dataset=_dataset(cases),
        model_pairs=pairs,
        system_prompt="SYS",
        run_id=999,
        repo=repo,
        max_workers=4,
        temperature=0.0,
        call=fake_call,
    )

    assert (outcome.inserted, outcome.failed) == (4, 0)
    keys = {(r.case_id, r.model_id) for r in repo.inserts}
    assert keys == {("c1", 1), ("c1", 2), ("c2", 1), ("c2", 2)}


def test_execute_run_inserts_n_rows_per_pair_with_distinct_sample_idx():
    """--samples N evaluates each (case, model) pair N times, persisting one
    row per draw, each tagged with a sample_idx in 0..N-1."""
    cases = [_case("c1", "prompt one")]
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})
    fake_call = lambda *_a, **_k: _fake_response("ok", 10, 5)

    outcome = execute_run(
        dataset=_dataset(cases),
        model_pairs=pairs,
        system_prompt="SYS",
        run_id=999,
        repo=repo,
        max_workers=1,
        temperature=0.7,
        samples=3,
        call=fake_call,
    )

    assert outcome.inserted == 3
    rows = [r for r in repo.inserts if (r.case_id, r.model_id) == ("c1", 1)]
    assert len(rows) == 3
    assert sorted(r.sample_idx for r in rows) == [0, 1, 2]


def test_execute_run_extracts_and_persists_response_style_features():
    """Each inserted row carries the markdown/style feature counts of its
    response, extracted regardless of whether judging is enabled."""
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})
    styled = "# Title\n\nHere is **bold** text.\n\n1. one\n2. two\n- bullet\n"
    call = lambda *_a, **_k: _fake_response(styled, 5, 3)

    execute_run(
        dataset=_dataset([_case("c1", "hi")]),
        model_pairs=pairs,
        system_prompt="SYS",
        run_id=1,
        repo=repo,
        max_workers=1,
        temperature=0.0,
        call=call,
    )

    row = repo.inserts[0]
    assert row.resp_style_headers == 1
    assert row.resp_style_bold == 1
    assert row.resp_style_ordered == 2
    assert row.resp_style_unordered == 1
    assert row.resp_style_code_blocks == 0


def test_execute_run_defaults_to_single_sample():
    """Omitting samples reproduces single-shot behaviour: one row, sample_idx 0."""
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})
    fake_call = lambda *_a, **_k: _fake_response("ok", 10, 5)

    outcome = execute_run(
        dataset=_dataset([_case("c1", "hi")]),
        model_pairs=pairs,
        system_prompt="SYS",
        run_id=1,
        repo=repo,
        max_workers=1,
        temperature=0.0,
        call=fake_call,
    )

    assert outcome.inserted == 1
    assert repo.inserts[0].sample_idx == 0


def test_execute_run_judges_every_sample_and_reports_mean_stddev():
    """Each sample is judged independently, so N samples → N judge calls per
    pair, and RunOutcome exposes per-model mean ± stddev over those scores."""
    from app.judge import JudgeVerdict

    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})
    call = lambda *_a, **_k: _fake_response("Canberra.", 5, 3)

    # Three distinct raw scores → scaled ×5 to 1.0, 3.0, 5.0 (mean 3.0).
    verdicts = iter([
        JudgeVerdict(score=0.2, reasoning="r", response="raw"),
        JudgeVerdict(score=0.6, reasoning="r", response="raw"),
        JudgeVerdict(score=1.0, reasoning="r", response="raw"),
    ])
    with patch("app.runner.judge", side_effect=lambda *_a, **_k: next(verdicts)) as mock_judge:
        outcome = execute_run(
            dataset=_dataset([_case("c1", "Quelle est la capitale ?")]),
            model_pairs=pairs,
            system_prompt="SYS",
            run_id=1,
            repo=repo,
            max_workers=1,  # serialize so the verdict iter is deterministic
            temperature=0.7,
            samples=3,
            do_judge=True,
            call=call,
        )

    assert outcome.inserted == 3
    assert mock_judge.call_count == 3  # one judge call per sample
    mean, stddev, n = outcome.model_score_stats()["claude-sonnet-4-6"]
    assert n == 3
    assert mean == pytest.approx(3.0)      # (1.0 + 3.0 + 5.0) / 3
    assert stddev == pytest.approx(2.0)    # sample stddev of {1, 3, 5}


def test_execute_run_computes_cost_from_tokens_and_prices():
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0.000003", out_cost="0.000015")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})
    call = lambda *_a, **_k: _fake_response("ok", 1000, 500)

    execute_run(
        dataset=_dataset([_case("c1", "hi")]),
        model_pairs=pairs,
        system_prompt="SYS",
        run_id=1,
        repo=repo,
        max_workers=1,
        temperature=0.0,
        call=call,
    )

    expected = Decimal("0.000003") * 1000 + Decimal("0.000015") * 500
    assert repo.inserts[0].cost == expected


def test_execute_run_aggregates_metrics_into_run_outcome():
    """SCRUM-22: RunOutcome should sum cost + tokens and collect latencies."""
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0.000003", out_cost="0.000015")),
        ("deepseek-v4-flash", _model(id=2, name="deepseek-v4-flash", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})

    responses = iter([
        _fake_response("a", 100, 50),
        _fake_response("b", 200, 80),
    ])
    fake_call = lambda *_a, **_k: next(responses)

    outcome = execute_run(
        dataset=_dataset([_case("c1", "hi")]),
        model_pairs=pairs,
        system_prompt="SYS",
        run_id=1,
        repo=repo,
        max_workers=1,  # serialize so the responses iter is deterministic
        temperature=0.0,
        call=fake_call,
    )

    assert outcome.inserted == 2 and outcome.failed == 0
    assert outcome.total_input_tokens == 300
    assert outcome.total_output_tokens == 130
    # Both calls use the same fake latency (42 ms), so min/avg/max all = 42.
    assert outcome.latencies_ms == [42, 42]
    assert outcome.min_latency_ms == 42
    assert outcome.max_latency_ms == 42
    assert outcome.avg_latency_ms == 42.0
    # Total cost = sum of per-call costs computed from each model's prices.
    expected_cost = (Decimal("0.000003") * 100 + Decimal("0.000015") * 50)  # sonnet
    assert outcome.total_cost == expected_cost


def test_execute_run_prepends_system_prompt_to_user_prompt():
    captured: list[str] = []

    def spy(provider, model, prompt, **kw):
        captured.append(prompt)
        return _fake_response("ok", 1, 1)

    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    execute_run(
        dataset=_dataset([_case("c1", "USER_TEXT")]),
        model_pairs=pairs,
        system_prompt="SYSTEM_TEXT",
        run_id=1,
        repo=FakeResultsRepository({k: v for k, v in pairs}),
        max_workers=1,
        temperature=0.0,
        call=spy,
    )

    assert captured == ["SYSTEM_TEXT\n\nUSER_TEXT"]


def test_execute_run_records_partial_failures_and_continues():
    cases = [_case("c1", "p1"), _case("c2", "p2")]
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})

    def flaky(provider, model, prompt, **kw):
        if "p1" in prompt:
            raise RuntimeError("boom")
        return _fake_response("ok", 1, 1)

    outcome = execute_run(
        dataset=_dataset(cases),
        model_pairs=pairs,
        system_prompt="SYS",
        run_id=1,
        repo=repo,
        max_workers=2,
        temperature=0.0,
        call=flaky,
    )

    assert (outcome.inserted, outcome.failed) == (1, 1)
    assert [r.case_id for r in repo.inserts] == ["c2"]


def test_execute_run_judges_and_persists_scaled_score_when_enabled():
    """With do_judge=True, the runner judges each answer and persists the
    ×5-scaled score via update_judge. `app.runner.judge` is patched so no
    Gemini call happens — this tests the runner's wiring, not the judge."""
    from app.judge import JudgeVerdict, to_db_scale

    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})
    call = lambda *_a, **_k: _fake_response("Canberra.", 5, 3)

    fake_verdict = JudgeVerdict(score=0.6, reasoning="presque correct", response="raw")
    with patch("app.runner.judge", return_value=fake_verdict) as mock_judge:
        outcome = execute_run(
            dataset=_dataset([_case("c1", "Quelle est la capitale ?")]),
            model_pairs=pairs,
            system_prompt="SYS",
            run_id=1,
            repo=repo,
            max_workers=1,
            temperature=0.0,
            do_judge=True,
            call=call,
        )

    assert outcome.inserted == 1
    # judge graded the candidate answer against the original question
    mock_judge.assert_called_once()
    q, a = mock_judge.call_args.args
    assert q == "Quelle est la capitale ?"
    assert a == "Canberra."
    # the 0..1 score was scaled to 0..5 and persisted onto the inserted row
    assert len(repo.judge_updates) == 1
    assert repo.judge_updates[0]["judge_score"] == to_db_scale(0.6)  # Decimal("3.0")
    assert repo.judge_updates[0]["judge_reasoning"] == "presque correct"


def test_execute_run_keeps_row_but_skips_score_when_judge_fails():
    """A JudgeParseError must not lose the response row — it's already
    inserted; only the score is skipped (judge_score stays unset)."""
    from app.judge import JudgeParseError

    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})
    call = lambda *_a, **_k: _fake_response("garbled", 1, 1)

    with patch("app.runner.judge", side_effect=JudgeParseError("bad verdict")):
        outcome = execute_run(
            dataset=_dataset([_case("c1", "p")]),
            model_pairs=pairs,
            system_prompt="SYS",
            run_id=1,
            repo=repo,
            max_workers=1,
            temperature=0.0,
            do_judge=True,
            call=call,
        )

    assert outcome.inserted == 1        # response persisted despite judge failure
    assert repo.judge_updates == []     # no score written


def test_execute_run_survives_judge_api_error():
    """A non-parse failure (e.g. a 429 from the judge API) must also be
    swallowed — judging is best-effort and must never kill the run."""
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})
    call = lambda *_a, **_k: _fake_response("ok", 1, 1)

    with patch("app.runner.judge", side_effect=RuntimeError("429 RESOURCE_EXHAUSTED")):
        outcome = execute_run(
            dataset=_dataset([_case("c1", "p")]),
            model_pairs=pairs,
            system_prompt="SYS",
            run_id=1,
            repo=repo,
            max_workers=1,
            temperature=0.0,
            do_judge=True,
            call=call,
        )

    assert outcome.inserted == 1        # run completed, response kept
    assert repo.judge_updates == []     # no score written


def test_execute_run_persists_prompt_style_from_case():
    """SCRUM-37: the runner reads `style` off each case (Case.raw) and
    persists it on the result row so quality can be aggregated by style."""
    styled_case = Case(
        id="math-muffins-fewshot",
        prompt="Q: ...\nA:",
        raw={"id": "math-muffins-fewshot", "prompt": "Q: ...\nA:", "style": "few-shot"},
    )
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})

    execute_run(
        dataset=_dataset([styled_case]),
        model_pairs=pairs,
        system_prompt="SYS",
        run_id=1,
        repo=repo,
        max_workers=1,
        temperature=0.0,
        call=lambda *_a, **_k: _fake_response("ok", 1, 1),
    )

    assert repo.inserts[0].prompt_style == "few-shot"


def test_execute_run_prompt_style_none_for_unstyled_case():
    """A plain case (no `style` key) persists prompt_style = None."""
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})

    execute_run(
        dataset=_dataset([_case("c1", "plain prompt")]),
        model_pairs=pairs,
        system_prompt="SYS",
        run_id=1,
        repo=repo,
        max_workers=1,
        temperature=0.0,
        call=lambda *_a, **_k: _fake_response("ok", 1, 1),
    )

    assert repo.inserts[0].prompt_style is None


def test_execute_run_aggregates_judge_scores_by_style():
    """SCRUM-37 Phase 3: judged benchmark scores are bucketed by
    (model, prompt_style) and averaged for the per-style summary."""
    from app.judge import JudgeVerdict, to_db_scale

    def _styled(cid, style):
        # prompt == style, so the patched judge (which receives the case
        # prompt as `question`) can map each case to a deterministic score.
        return Case(id=cid, prompt=style, raw={"id": cid, "prompt": style, "style": style})

    cases = [_styled("a-zero", "zero-shot"), _styled("b-zero", "zero-shot"), _styled("c-few", "few-shot")]
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})

    # zero-shot cases score raw 0.6 (→ 3.0); few-shot scores raw 0.8 (→ 4.0)
    style_score = {"zero-shot": 0.6, "few-shot": 0.8}

    def fake_judge(question, answer, **kw):
        return JudgeVerdict(score=style_score[question], reasoning="ok", response="raw")

    call = lambda *_a, **_k: _fake_response("ok", 1, 1)

    with patch("app.runner.judge", side_effect=fake_judge):
        outcome = execute_run(
            dataset=_dataset(cases),
            model_pairs=pairs,
            system_prompt="SYS",
            run_id=1,
            repo=repo,
            max_workers=1,
            temperature=0.0,
            do_judge=True,
            call=call,
        )

    avgs = outcome.style_averages()
    assert avgs[("claude-sonnet-4-6", "zero-shot")] == float(to_db_scale(0.6))  # 3.0
    assert avgs[("claude-sonnet-4-6", "few-shot")] == float(to_db_scale(0.8))   # 4.0
    # two zero-shot results bucketed together
    assert len(outcome.style_scores[("claude-sonnet-4-6", "zero-shot")]) == 2


def test_style_averages_empty_when_no_judge():
    outcome = execute_run(
        dataset=_dataset([_case("c1", "p")]),
        model_pairs=[("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0"))],
        system_prompt="SYS",
        run_id=1,
        repo=FakeResultsRepository({"claude-sonnet-4-6": _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")}),
        max_workers=1,
        temperature=0.0,
        call=lambda *_a, **_k: _fake_response("ok", 1, 1),
    )
    assert outcome.style_averages() == {}


def test_execute_run_throttles_judge_calls_when_interval_set():
    """With judge_min_interval > 0, the runner sleeps between judge calls to
    respect the provider rate limit. time.sleep is mocked so the test is instant."""
    from app.judge import JudgeVerdict

    pairs = [("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0"))]
    repo = FakeResultsRepository({k: v for k, v in pairs})
    cases = [_case("c1", "p"), _case("c2", "p"), _case("c3", "p")]
    fake_verdict = JudgeVerdict(score=0.6, reasoning="ok", response="raw")

    with patch("app.runner.judge", return_value=fake_verdict), \
         patch("app.runner.time.sleep") as mock_sleep:
        execute_run(
            dataset=_dataset(cases),
            model_pairs=pairs,
            system_prompt="SYS",
            run_id=1,
            repo=repo,
            max_workers=1,
            temperature=0.0,
            do_judge=True,
            judge_min_interval=13,
            call=lambda *_a, **_k: _fake_response("ok", 1, 1),
        )

    # First judge call isn't throttled (no prior call); the next two are.
    assert mock_sleep.call_count >= 2


def test_execute_run_no_throttle_when_interval_zero():
    from app.judge import JudgeVerdict

    pairs = [("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0"))]
    repo = FakeResultsRepository({k: v for k, v in pairs})
    fake_verdict = JudgeVerdict(score=0.6, reasoning="ok", response="raw")

    with patch("app.runner.judge", return_value=fake_verdict), \
         patch("app.runner.time.sleep") as mock_sleep:
        execute_run(
            dataset=_dataset([_case("c1", "p"), _case("c2", "p")]),
            model_pairs=pairs,
            system_prompt="SYS",
            run_id=1,
            repo=repo,
            max_workers=1,
            temperature=0.0,
            do_judge=True,  # interval defaults to 0.0 → no throttle
            call=lambda *_a, **_k: _fake_response("ok", 1, 1),
        )

    mock_sleep.assert_not_called()


def test_execute_run_does_not_judge_when_flag_off():
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})

    with patch("app.runner.judge") as mock_judge:
        execute_run(
            dataset=_dataset([_case("c1", "p")]),
            model_pairs=pairs,
            system_prompt="SYS",
            run_id=1,
            repo=repo,
            max_workers=1,
            temperature=0.0,
            call=lambda *_a, **_k: _fake_response("ok", 1, 1),
        )

    mock_judge.assert_not_called()      # do_judge defaults to False
    assert repo.judge_updates == []


def test_execute_run_finalizes_run_even_when_everything_fails():
    pairs = [
        ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
    ]
    repo = FakeResultsRepository({k: v for k, v in pairs})

    def always_fail(*a, **k):
        raise RuntimeError("nope")

    execute_run(
        dataset=_dataset([_case("c1", "p")]),
        model_pairs=pairs,
        system_prompt="SYS",
        run_id=777,
        repo=repo,
        max_workers=1,
        temperature=0.0,
        call=always_fail,
    )
    assert 777 in repo.finalized


def test_execute_run_passes_max_workers_to_threadpool():
    """Patch ThreadPoolExecutor to confirm `max_workers` is forwarded — but
    let the task callable run synchronously so the future carries a real
    TaskResult, not a raw LLMResponse."""
    with patch("app.runner.concurrent.futures.ThreadPoolExecutor") as mock_pool:
        mock_pool.return_value.__enter__.return_value.submit.side_effect = (
            lambda fn, *args, **kw: _ImmediateFuture(fn(*args, **kw))
        )
        with patch(
            "app.runner.concurrent.futures.as_completed",
            side_effect=lambda fs: iter(list(fs.keys())),
        ):
            pairs = [
                ("claude-sonnet-4-6", _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0")),
            ]
            execute_run(
                dataset=_dataset([_case("c1", "p")]),
                model_pairs=pairs,
                system_prompt="SYS",
                run_id=1,
                repo=FakeResultsRepository({k: v for k, v in pairs}),
                max_workers=42,
                temperature=0.0,
                call=lambda *_a, **_k: _fake_response("ok", 1, 1),
            )
            mock_pool.assert_called_once_with(max_workers=42)


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_main_unknown_model_key_exits_before_any_db_or_api_call(tmp_path):
    ds = tmp_path / "tiny.yaml"
    ds.write_text(
        "dataset:\n  name: t\n  version: 1\ncases:\n  - id: a\n    prompt: hi\n",
        encoding="utf-8",
    )
    with patch("app.runner._connect_db") as mock_connect:
        exit_code = main(["--dataset", str(ds), "--models", "bogus"])
        assert exit_code == 1
        mock_connect.assert_not_called()


def test_main_missing_dataset_file_exits_one_before_db_connect(tmp_path):
    with patch("app.runner._connect_db") as mock_connect:
        exit_code = main(
            ["--dataset", str(tmp_path / "does-not-exist.yaml"), "--models", "claude-sonnet-4-6"]
        )
        assert exit_code == 1
        mock_connect.assert_not_called()


def test_load_system_prompt_exits_when_eval_system_missing():
    """`_load_system_prompt` is the runner's only guard against running
    against an un-synced DB. If it returns None, we must `sys.exit` so the
    user gets a clear error instead of a NoneType.id crash later."""
    from app.runner import _load_system_prompt

    class _EmptyPromptRepo:
        def latest_by_name(self, name):  # noqa: D401
            return None

    with pytest.raises(SystemExit) as exc:
        _load_system_prompt(_EmptyPromptRepo())
    assert "eval_system" in str(exc.value)


def test_parse_args_accepts_temperature_and_max_workers():
    """Confirm the non-default CLI flags actually propagate through argparse."""
    from app.runner import _parse_args

    args = _parse_args(
        [
            "--dataset", "x.yaml",
            "--models", "claude-sonnet-4-6", "gpt-5",
            "--max-workers", "12",
            "--temperature", "0.7",
            "--samples", "5",
        ]
    )
    assert args.max_workers == 12
    assert args.temperature == 0.7
    assert args.samples == 5
    assert args.models == ["claude-sonnet-4-6", "gpt-5"]


def test_parse_args_samples_defaults_to_high():
    """--samples defaults to DEFAULT_SAMPLES (high) so runs carry a spread
    out of the box; omitting it must not silently fall back to single-shot."""
    from app.runner import DEFAULT_SAMPLES, _parse_args

    args = _parse_args(["--dataset", "x.yaml", "--models", "claude-sonnet-4-6"])
    assert args.samples == DEFAULT_SAMPLES
    assert DEFAULT_SAMPLES > 1


def test_main_happy_path_persists_results_to_db(tmp_path):
    """End-to-end CLI smoke with every external surface mocked.

    Note: we can't simply `patch("app.runner.call_llm", ...)` because
    `execute_run` captures `call_llm` as a default parameter at *definition*
    time. Instead we mock at the SDK seam (`anthropic.Anthropic`), the same
    place `tests/test_llm_client.py` mocks, so the real `call_llm` runs but
    talks to a fake SDK.
    """
    import os
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    ds = tmp_path / "tiny.yaml"
    ds.write_text(
        "dataset:\n  name: t\n  version: 1\ncases:\n  - id: a\n    prompt: hi\n",
        encoding="utf-8",
    )

    fake_prompt_row = MagicMock(id=5, content="SYSTEM")
    fake_prompt_repo = MagicMock()
    fake_prompt_repo.latest_by_name.return_value = fake_prompt_row

    fake_results_repo = FakeResultsRepository({
        "claude-sonnet-4-6": _model(id=1, name="claude-sonnet-4-6", in_cost="0", out_cost="0"),
    })

    fake_anthropic_response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hello back")],
        usage=SimpleNamespace(input_tokens=4, output_tokens=2),
    )

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), \
         patch("anthropic.Anthropic") as mock_anthropic_cls, \
         patch("app.runner._connect_db", return_value=MagicMock()) as mock_connect, \
         patch("app.runner.PostgresResultsRepository", return_value=fake_results_repo), \
         patch("app.runner.PostgresPromptRepository", return_value=fake_prompt_repo):
        mock_anthropic_cls.return_value.messages.create.return_value = fake_anthropic_response
        # --samples 1: this smoke test asserts a single persisted row, so pin
        # single-shot rather than ride the high default.
        exit_code = main(
            ["--dataset", str(ds), "--models", "claude-sonnet-4-6", "--samples", "1"]
        )

    assert exit_code == 0
    mock_connect.assert_called_once()
    assert len(fake_results_repo.inserts) == 1
    assert fake_results_repo.inserts[0].case_id == "a"
    assert fake_results_repo.inserts[0].question == "hi"
    assert fake_results_repo.inserts[0].response == "hello back"
    assert fake_results_repo.finalized  # run was finalized


# ---------------------------------------------------------------------------
# Regression gate (SCRUM-25)
# ---------------------------------------------------------------------------


def _outcome_with_scores(model_scores: dict[str, list[Decimal]]) -> RunOutcome:
    """A minimal RunOutcome carrying only per-model judge scores — enough to
    exercise the gate, which reads `model_score_stats()`."""
    return RunOutcome(
        inserted=sum(len(v) for v in model_scores.values()),
        failed=0,
        total_cost=Decimal(0),
        total_input_tokens=0,
        total_output_tokens=0,
        latencies_ms=[],
        model_scores=model_scores,
    )


def test_regression_failures_off_when_threshold_is_none():
    outcome = _outcome_with_scores({"m": [Decimal("1.0")]})
    # Gate disabled → no failures regardless of how low the score is.
    assert regression_failures(outcome, None) == []


def test_regression_failures_flags_model_below_threshold():
    outcome = _outcome_with_scores(
        {
            "good": [Decimal("4.0"), Decimal("4.0")],  # mean 4.0
            "bad": [Decimal("2.0"), Decimal("4.0")],   # mean 3.0
        }
    )
    assert regression_failures(outcome, 3.5) == [("bad", 3.0)]


def test_regression_failures_boundary_is_exclusive():
    # Exactly at threshold passes (mean < fail_under is the failing condition).
    outcome = _outcome_with_scores({"edge": [Decimal("3.5")]})
    assert regression_failures(outcome, 3.5) == []


def test_regression_failures_passes_when_all_above():
    outcome = _outcome_with_scores(
        {"a": [Decimal("4.2")], "b": [Decimal("3.6")]}
    )
    assert regression_failures(outcome, 3.5) == []


def test_regression_failures_sorted_by_model_key():
    outcome = _outcome_with_scores(
        {"zeta": [Decimal("1.0")], "alpha": [Decimal("2.0")]}
    )
    assert [m for m, _ in regression_failures(outcome, 3.5)] == ["alpha", "zeta"]


def test_regression_failures_ignores_unjudged_models():
    # A model with no scores contributes no stats → can't fail the gate.
    outcome = _outcome_with_scores({"judged": [Decimal("4.0")], "unjudged": []})
    assert regression_failures(outcome, 3.5) == []
