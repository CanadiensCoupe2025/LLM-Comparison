"""Tests for the final-decision library seam (SCRUM-38).

`decide_run` is the function shared by the CLI (`python -m app.decide`) and
the runner's auto-decide hook. Everything external is faked: the repositories
are in-memory stand-ins, `decide()` (the Gemini justification call) is
patched, and the versioned prompt lookup is stubbed — no DB, no network.
The profiles YAML is the real one, so the "all profiles by default" contract
is exercised against the actual catalogue.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.decide import EXIT_NO_DATA, EXIT_OK, decide_run, main
from app.decision import Decision
from app.profiles import load_profiles
from app.results_repository import DecisionRow

RUN_ID = 7

# A minimal-but-realistic model_decision_metrics row set (JSON-serializable:
# input_hash canonicalizes the metrics, so the values must survive json.dumps).
_METRICS = [
    {
        "model": "claude-haiku-4-5", "n_judged": 2, "n_cases": 2,
        "avg_input_tokens": 10.0, "avg_output_tokens": 20.0,
        "avg_total_tokens": 30.0, "avg_latency_ms": 100.0,
        "mean_judge_score": 4.5, "stddev_judge_score": None,
        "efficiency": 150.0, "ctx_pct": 0.01, "avg_cost": 0.0001,
        "run_id": RUN_ID,
    },
    {
        "model": "gpt-5.4", "n_judged": 2, "n_cases": 2,
        "avg_input_tokens": 12.0, "avg_output_tokens": 40.0,
        "avg_total_tokens": 52.0, "avg_latency_ms": 900.0,
        "mean_judge_score": 4.0, "stddev_judge_score": None,
        "efficiency": 76.9, "ctx_pct": 0.003, "avg_cost": 0.0002,
        "run_id": RUN_ID,
    },
]


class FakeDecisionRepo:
    """In-memory PostgresResultsRepository — just the decision surface."""

    def __init__(self, metrics=None, cached=None, latest_run=RUN_ID):
        self.metrics = _METRICS if metrics is None else metrics
        self.cached = cached          # returned by every find_decision call
        self.latest_run = latest_run
        self.inserts: list[dict] = []

    def fetch_decision_metrics(self, run_id):
        return self.metrics

    def find_decision(self, *, input_hash, prompt_id, profile):
        return self.cached

    def insert_decision(self, **kw):
        self.inserts.append(kw)

    def latest_run_id(self):
        return self.latest_run


def _fake_decision(metrics, profile, *, rubric, model):
    """Stub for app.decide.decide — deterministic, no LLM."""
    return Decision(
        recommended_model="claude-haiku-4-5",
        confidence="haute",
        profile=profile.name,
        determinant_metrics=["mean_judge_score"],
        tradeoffs="aucun",
        reasoning="stub",
        weighted_scores=[{"model": "claude-haiku-4-5", "score": 0.9}],
    )


def _cached_row(profile="equilibre") -> DecisionRow:
    return DecisionRow(
        id=1, recommended_model="gpt-5.4", confidence="moyenne",
        determinant_metrics=["efficiency"], tradeoffs=None, reasoning="cached",
        prompt_id=1, input_hash="deadbeef", profile=profile,
        weighted_scores=[{"model": "gpt-5.4", "score": 0.8}],
        created_at=datetime.now(timezone.utc), run_id=RUN_ID,
    )


def _run(repo, **kwargs):
    """decide_run with the module-level collaborators stubbed out."""
    with patch("app.decide.PostgresResultsRepository", return_value=repo), \
         patch("app.decide.PostgresPromptRepository", return_value=object()), \
         patch("app.decide._resolve_prompt_id", return_value=1), \
         patch("app.decide.load_decision_prompt",
               return_value=SimpleNamespace(content="RUBRIC")), \
         patch("app.decide.decide", side_effect=_fake_decision) as mock_decide:
        results = decide_run(object(), RUN_ID, **kwargs)
    return results, mock_decide


# --- decide_run ---------------------------------------------------------------


def test_decide_run_decides_every_profile_by_default():
    """profiles=None → one fresh decision per profile in the YAML catalogue."""
    repo = FakeDecisionRepo()
    results, mock_decide = _run(repo)

    all_profiles = sorted(load_profiles())
    assert sorted(d.profile for d, _ in results) == all_profiles
    assert all(replayed is False for _, replayed in results)
    assert len(repo.inserts) == len(all_profiles)
    assert mock_decide.call_count == len(all_profiles)
    # every insert is stamped with the run so the dashboard's $run filter hits
    assert all(kw["run_id"] == RUN_ID for kw in repo.inserts)


def test_decide_run_replays_cached_decision():
    """Cache hit → the stored row comes back replayed; no LLM call, no insert."""
    repo = FakeDecisionRepo(cached=_cached_row())
    results, mock_decide = _run(repo)

    assert all(replayed is True for _, replayed in results)
    assert all(d.recommended_model == "gpt-5.4" for d, _ in results)
    mock_decide.assert_not_called()
    assert repo.inserts == []


def test_decide_run_force_bypasses_cache():
    """force=True ignores the cached row and regenerates + persists."""
    repo = FakeDecisionRepo(cached=_cached_row())
    results, mock_decide = _run(repo, force=True)

    assert all(replayed is False for _, replayed in results)
    assert mock_decide.call_count == len(load_profiles())
    assert len(repo.inserts) == len(load_profiles())


def test_decide_run_returns_empty_when_no_judged_metrics():
    """Unjudged run → [] and zero LLM calls (the runner logs and moves on)."""
    repo = FakeDecisionRepo(metrics=[])
    results, mock_decide = _run(repo)

    assert results == []
    mock_decide.assert_not_called()
    assert repo.inserts == []


# --- main() CLI wrapper --------------------------------------------------------


def test_main_exits_no_data_when_no_runs():
    repo = FakeDecisionRepo(latest_run=None)
    with patch("app.decide._connect_db", return_value=_FakeConn()), \
         patch("app.decide.PostgresResultsRepository", return_value=repo):
        assert main([]) == EXIT_NO_DATA


def test_main_prints_decisions_and_exits_ok(capsys):
    """Default CLI (single profile) → EXIT_OK + a readable summary."""
    repo = FakeDecisionRepo()
    with patch("app.decide._connect_db", return_value=_FakeConn()), \
         patch("app.decide.PostgresResultsRepository", return_value=repo), \
         patch("app.decide.PostgresPromptRepository", return_value=object()), \
         patch("app.decide._resolve_prompt_id", return_value=1), \
         patch("app.decide.load_decision_prompt",
               return_value=SimpleNamespace(content="RUBRIC")), \
         patch("app.decide.decide", side_effect=_fake_decision):
        assert main([]) == EXIT_OK

    out = capsys.readouterr().out
    assert "equilibre" in out
    assert "claude-haiku-4-5" in out
    assert len(repo.inserts) == 1  # --profile default = one decision


class _FakeConn:
    def close(self):
        pass
