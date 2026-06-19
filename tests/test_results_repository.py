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


def test_insert_result_writes_all_eight_fields_in_order_and_commits():
    conn, cur = _mock_conn(fetchone_value=(123,))

    result_id = PostgresResultsRepository(conn).insert_result(
        run_id=10,
        model_id=3,
        case_id="canary-1",
        response="hello",
        latency_ms=420,
        input_tokens=12,
        output_tokens=8,
        cost=Decimal("0.000123"),
    )

    assert result_id == 123
    sql, params = cur.execute.call_args.args
    assert "INSERT INTO results" in sql
    assert "RETURNING id" in sql
    # Column list must be in the order the runner expects.
    assert "run_id, model_id, case_id, response," in sql
    assert "latency_ms, input_tokens, output_tokens, cost" in sql
    assert params == (10, 3, "canary-1", "hello", 420, 12, 8, Decimal("0.000123"))
    conn.commit.assert_called_once()


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
