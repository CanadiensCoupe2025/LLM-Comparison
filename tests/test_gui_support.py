"""Tests for the GUI support helpers (SCRUM-33/34).

Covers the two pieces of new code the Streamlit GUI leans on:
  - `app.datasets.discover_datasets` — folder scan that skips bad files.
  - `app.runner.launch_run` — library entry point that wires up a run and
    raises ordinary exceptions instead of `sys.exit`.

`launch_run` is exercised with the DB layer mocked, so no Postgres / network.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import runner
from app.datasets import discover_datasets

_VALID = "dataset:\n  name: {name}\n  version: 1\ncases:\n  - id: a\n    prompt: hi\n"


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# --- discover_datasets -------------------------------------------------------


def test_discover_datasets_skips_invalid(tmp_path: Path) -> None:
    _write(tmp_path / "good.yaml", _VALID.format(name="demo"))
    _write(tmp_path / "bad.yaml", "this: [is, not, closed")  # malformed YAML
    _write(tmp_path / "README.md", "not a dataset")  # not .yaml

    found = discover_datasets(tmp_path)

    assert [d.name for d in found] == ["demo"]
    assert found[0].cases[0].id == "a"


def test_discover_datasets_sorted_by_filename(tmp_path: Path) -> None:
    _write(tmp_path / "b.yaml", _VALID.format(name="bee"))
    _write(tmp_path / "a.yaml", _VALID.format(name="ay"))

    found = discover_datasets(tmp_path)

    assert [d.source_path.name for d in found] == ["a.yaml", "b.yaml"]


def test_discover_datasets_empty_dir(tmp_path: Path) -> None:
    assert discover_datasets(tmp_path) == []


# --- launch_run --------------------------------------------------------------


def _dataset_file(tmp_path: Path, fname: str = "smoke.yaml") -> Path:
    path = tmp_path / fname
    _write(path, _VALID.format(name="smoke"))
    return path


def test_launch_run_requires_database_url(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        runner.launch_run(_dataset_file(tmp_path), ["claude-haiku-4-5"])


def _mock_db(monkeypatch, *, prompt_row):
    """Patch psycopg + both repositories; return (conn, results_repo)."""
    conn = MagicMock()
    monkeypatch.setattr(runner.psycopg, "connect", lambda url: conn)

    results_repo = MagicMock()
    results_repo.create_run.return_value = 42
    monkeypatch.setattr(
        runner, "PostgresResultsRepository", lambda c: results_repo
    )

    prompt_repo = MagicMock()
    prompt_repo.latest_by_name.return_value = prompt_row
    monkeypatch.setattr(runner, "PostgresPromptRepository", lambda c: prompt_repo)
    return conn, results_repo


def test_launch_run_wires_pieces(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@localhost/y")
    conn, results_repo = _mock_db(
        monkeypatch, prompt_row=MagicMock(id=7, content="SYS")
    )

    sentinel = object()
    captured: dict = {}

    def fake_execute_run(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(runner, "execute_run", fake_execute_run)

    run_id, outcome = runner.launch_run(
        _dataset_file(tmp_path), ["claude-haiku-4-5"], samples=1
    )

    assert (run_id, outcome) == (42, sentinel)
    # runs.dataset stores the FILE name, matching main()
    results_repo.create_run.assert_called_once_with(7, "smoke.yaml")
    assert captured["system_prompt"] == "SYS"
    assert captured["run_id"] == 42
    conn.close.assert_called_once()


def test_launch_run_missing_prompt_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@localhost/y")
    conn, _ = _mock_db(monkeypatch, prompt_row=None)
    monkeypatch.setattr(runner, "execute_run", lambda **k: None)

    with pytest.raises(RuntimeError, match="System prompt"):
        runner.launch_run(_dataset_file(tmp_path), ["claude-haiku-4-5"])
    conn.close.assert_called_once()  # connection closed even on error


def test_launch_run_unknown_model_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@localhost/y")
    conn, _ = _mock_db(monkeypatch, prompt_row=MagicMock(id=7, content="SYS"))

    with pytest.raises(ValueError, match="Unknown model key"):
        runner.launch_run(_dataset_file(tmp_path), ["nope-not-a-model"])
    conn.close.assert_called_once()


def test_launch_run_auto_decides_when_judged(tmp_path, monkeypatch) -> None:
    """do_judge=True → the per-profile decision is recorded automatically,
    against the same open connection and the new run id (GUI path)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@localhost/y")
    conn, _ = _mock_db(monkeypatch, prompt_row=MagicMock(id=7, content="SYS"))
    monkeypatch.setattr(runner, "execute_run", lambda **k: MagicMock())

    decide_mock = MagicMock(return_value=[])
    monkeypatch.setattr(runner, "decide_run", decide_mock)

    runner.launch_run(
        _dataset_file(tmp_path), ["claude-haiku-4-5"], do_judge=True
    )

    decide_mock.assert_called_once_with(conn, 42)


def test_launch_run_no_auto_decide_when_unjudged(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@localhost/y")
    _mock_db(monkeypatch, prompt_row=MagicMock(id=7, content="SYS"))
    monkeypatch.setattr(runner, "execute_run", lambda **k: MagicMock())

    decide_mock = MagicMock(return_value=[])
    monkeypatch.setattr(runner, "decide_run", decide_mock)

    runner.launch_run(_dataset_file(tmp_path), ["claude-haiku-4-5"])

    decide_mock.assert_not_called()
