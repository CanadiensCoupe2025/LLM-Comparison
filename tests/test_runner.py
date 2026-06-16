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
    build_prompt,
    compute_cost,
    execute_run,
    main,
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


class FakeResultsRepository:
    """In-memory ResultsRepository — list-of-rows pretending to be tables."""

    def __init__(self, models: dict[str, ModelRow]):
        self._models = models
        self.inserts: list[_InsertedRow] = []
        self.runs: dict[int, dict[str, Any]] = {}
        self.finalized: set[int] = set()
        self._next_run_id = 100

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

    def insert_result(self, **kw) -> None:
        self.inserts.append(_InsertedRow(**kw))

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
        ]
    )
    assert args.max_workers == 12
    assert args.temperature == 0.7
    assert args.models == ["claude-sonnet-4-6", "gpt-5"]


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
        exit_code = main(["--dataset", str(ds), "--models", "claude-sonnet-4-6"])

    assert exit_code == 0
    mock_connect.assert_called_once()
    assert len(fake_results_repo.inserts) == 1
    assert fake_results_repo.inserts[0].case_id == "a"
    assert fake_results_repo.inserts[0].response == "hello back"
    assert fake_results_repo.finalized  # run was finalized
