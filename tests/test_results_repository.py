"""Unit tests for `PostgresResultsRepository` (SCRUM-19, SCRUM-20).

Mocks `psycopg.Connection` and `Cursor` with `MagicMock` so we exercise the
real SQL strings and method bodies without touching a live database. The
repository takes its connection via constructor injection — no patching of
`psycopg.connect` needed.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.results_repository import (
    ModelNotFoundError,
    ModelRow,
    PostgresResultsRepository,
)


def _mock_conn(fetchone_value=None):
    """A MagicMock that mimics a psycopg connection: `with conn.cursor() as cur:`
    yields a cursor whose `.fetchone()` returns `fetchone_value`."""
    cur = MagicMock()
    cur.fetchone.return_value = fetchone_value
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


# ---------------------------------------------------------------------------
# lookup_model
# ---------------------------------------------------------------------------


def test_lookup_model_returns_model_row_from_select():
    row = (
        1,
        "Anthropic",
        "claude-sonnet-4-6",
        "2025-09",
        Decimal("0.000003"),
        Decimal("0.000015"),
    )
    conn, cur = _mock_conn(fetchone_value=row)

    result = PostgresResultsRepository(conn).lookup_model("claude-sonnet-4-6")

    assert result == ModelRow(
        id=1,
        provider="Anthropic",
        name="claude-sonnet-4-6",
        version="2025-09",
        input_cost=Decimal("0.000003"),
        output_cost=Decimal("0.000015"),
    )
    # Confirm the SQL targets the right table and uses a parameter, not f-string.
    sql, params = cur.execute.call_args.args
    assert "FROM models" in sql
    assert "WHERE name = %s" in sql
    assert params == ("claude-sonnet-4-6",)


def test_lookup_model_raises_when_row_missing():
    conn, _ = _mock_conn(fetchone_value=None)
    with pytest.raises(ModelNotFoundError, match="claude-sonnet-4-6"):
        PostgresResultsRepository(conn).lookup_model("claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# create_run
# ---------------------------------------------------------------------------


def test_create_run_inserts_and_returns_id():
    conn, cur = _mock_conn(fetchone_value=(42,))

    run_id = PostgresResultsRepository(conn).create_run(
        prompt_id=7, dataset="regression_v1.yaml"
    )

    assert run_id == 42
    sql, params = cur.execute.call_args.args
    assert "INSERT INTO runs" in sql
    assert "RETURNING id" in sql
    assert params == (7, "regression_v1.yaml")
    conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# insert_result
# ---------------------------------------------------------------------------


def test_insert_result_writes_all_fields_in_order_and_commits():
    conn, cur = _mock_conn(fetchone_value=(123,))

    result_id = PostgresResultsRepository(conn).insert_result(
        run_id=10,
        model_id=3,
        case_id="canary-1",
        question="hello?",
        response="hello",
        latency_ms=420,
        input_tokens=12,
        output_tokens=8,
        cost=Decimal("0.000123"),
        prompt_style="few-shot",
        sample_idx=2,
        resp_style_headers=1,
        resp_style_bold=2,
        resp_style_ordered=3,
        resp_style_unordered=4,
        resp_style_code_blocks=5,
    )

    assert result_id == 123
    sql, params = cur.execute.call_args.args
    assert "INSERT INTO results" in sql
    assert "RETURNING id" in sql
    # Column list must be in the order the runner expects.
    assert "run_id, model_id, case_id, question, response," in sql
    assert "latency_ms, input_tokens, output_tokens, cost" in sql
    assert "prompt_style, sample_idx" in sql
    assert "resp_style_headers, resp_style_bold, resp_style_ordered" in sql
    assert params == (
        10, 3, "canary-1", "hello?", "hello", 420, 12, 8, Decimal("0.000123"),
        "few-shot", 2, 1, 2, 3, 4, 5,
    )
    conn.commit.assert_called_once()


def test_insert_result_defaults_prompt_style_to_none():
    """Non-benchmark runs don't pass a style — it must persist as NULL."""
    conn, cur = _mock_conn(fetchone_value=(1,))
    PostgresResultsRepository(conn).insert_result(
        run_id=1, model_id=1, case_id="c", response="r",
        latency_ms=1, input_tokens=1, output_tokens=1, cost=Decimal("0"),
    )
    _, params = cur.execute.call_args.args
    # The 5 trailing resp_style_* params default to None (not extracted here).
    assert params[-5:] == (None, None, None, None, None)
    assert params[-6] == 0       # sample_idx defaults to 0 (single-shot)
    assert params[-7] is None    # prompt_style


# ---------------------------------------------------------------------------
# update_judge
# ---------------------------------------------------------------------------


def test_update_judge_sets_score_and_reasoning_for_one_row_and_commits():
    conn, cur = _mock_conn()

    PostgresResultsRepository(conn).update_judge(
        result_id=55,
        judge_score=Decimal("3.5"),
        judge_reasoning="presque correct",
    )

    sql, params = cur.execute.call_args.args
    assert "UPDATE results" in sql
    assert "judge_score = %s" in sql
    assert "judge_reasoning = %s" in sql
    assert "WHERE id = %s" in sql
    # order must match the placeholders: score, reasoning, then the row id
    assert params == (Decimal("3.5"), "presque correct", 55)
    conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# finalize_run
# ---------------------------------------------------------------------------


def test_finalize_run_updates_finished_at_and_commits():
    conn, cur = _mock_conn()

    PostgresResultsRepository(conn).finalize_run(run_id=99)

    sql, params = cur.execute.call_args.args
    assert "UPDATE runs" in sql
    assert "finished_at = NOW()" in sql
    assert params == (99,)
    conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Final decision (SCRUM-38): find / insert / latest / metrics
# ---------------------------------------------------------------------------


def _decision_db_row():
    # Column order must match PostgresResultsRepository._DECISION_COLS.
    return (
        7,
        "claude-haiku-4-5",
        "élevée",
        ["efficiency"],
        "qualité ~égale, moins de tokens",
        "haiku rend plus de qualité par token.",
        3,
        "abc123",
        "etudiant",
        [{"model": "claude-haiku-4-5", "score": 0.82}],
        datetime(2026, 6, 25, 10, 0, 0),
    )


def test_find_decision_uses_not_distinct_from_for_null_keys():
    """The cache key must match NULL prompt_id/profile (plain `=` never matches NULL)."""
    conn, cur = _mock_conn(fetchone_value=_decision_db_row())

    row = PostgresResultsRepository(conn).find_decision(
        input_hash="abc123", prompt_id=None, profile="etudiant"
    )

    sql, params = cur.execute.call_args.args
    assert "FROM decisions" in sql
    assert "input_hash = %s" in sql
    assert "prompt_id IS NOT DISTINCT FROM %s" in sql
    assert "profile IS NOT DISTINCT FROM %s" in sql
    assert params == ("abc123", None, "etudiant")
    assert row.recommended_model == "claude-haiku-4-5"
    assert row.profile == "etudiant"
    assert row.weighted_scores[0]["score"] == 0.82


def test_find_decision_returns_none_when_absent():
    conn, _ = _mock_conn(fetchone_value=None)
    assert (
        PostgresResultsRepository(conn).find_decision(
            input_hash="x", prompt_id=1, profile="equilibre"
        )
        is None
    )


def test_insert_decision_wraps_json_columns_and_commits():
    from psycopg.types.json import Jsonb

    conn, cur = _mock_conn(fetchone_value=_decision_db_row())

    PostgresResultsRepository(conn).insert_decision(
        recommended_model="claude-haiku-4-5",
        confidence="élevée",
        determinant_metrics=["efficiency"],
        tradeoffs="moins de tokens",
        reasoning="x",
        prompt_id=3,
        input_hash="abc123",
        input_snapshot=[{"model": "gpt-5"}],
        profile="etudiant",
        weighted_scores=[{"model": "claude-haiku-4-5", "score": 0.82}],
    )

    sql, params = cur.execute.call_args.args
    assert "INSERT INTO decisions" in sql
    assert "profile" in sql and "weighted_scores" in sql
    # JSONB columns are wrapped so psycopg adapts list/dict correctly
    assert isinstance(params[2], Jsonb)   # determinant_metrics
    assert isinstance(params[7], Jsonb)   # input_snapshot
    assert isinstance(params[9], Jsonb)   # weighted_scores
    assert params[8] == "etudiant"        # profile
    conn.commit.assert_called_once()


def test_fetch_decision_metrics_returns_dicts_keyed_by_column():
    conn, cur = _mock_conn()
    cur.description = [MagicMock(name="col") for _ in range(2)]
    cur.description[0].name = "model"
    cur.description[1].name = "efficiency"
    cur.fetchall.return_value = [("gpt-5", Decimal("0.8"))]

    rows = PostgresResultsRepository(conn).fetch_decision_metrics()

    sql = cur.execute.call_args.args[0]
    assert "FROM model_decision_metrics" in sql
    assert rows == [{"model": "gpt-5", "efficiency": Decimal("0.8")}]
