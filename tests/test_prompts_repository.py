"""Unit tests for `PostgresPromptRepository` (SCRUM-18, SCRUM-20).

Same MagicMock-driven pattern as `tests/test_results_repository.py` —
exercises every method's SQL string, parameter tuple, and commit behavior
without a live database.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from app.prompts.repository import PostgresPromptRepository, PromptRow


_NOW = datetime(2026, 6, 15, 12, 0, 0)


def _mock_conn(*, fetchone=None, fetchall=()):
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = list(fetchall)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def _row(id: int = 1, name: str = "eval_system", version: str = "1.0") -> tuple:
    return (id, name, version, "content body", "abc123" * 10 + "abcd", None, _NOW)


# ---------------------------------------------------------------------------
# find_by_name_and_hash
# ---------------------------------------------------------------------------


def test_find_by_name_and_hash_returns_prompt_row_on_hit():
    conn, cur = _mock_conn(fetchone=_row(id=11))

    out = PostgresPromptRepository(conn).find_by_name_and_hash("eval_system", "abc")

    assert isinstance(out, PromptRow)
    assert out.id == 11
    sql, params = cur.execute.call_args.args
    assert "FROM prompts" in sql
    assert "WHERE name = %s AND hash = %s" in sql
    assert params == ("eval_system", "abc")


def test_find_by_name_and_hash_returns_none_on_miss():
    conn, _ = _mock_conn(fetchone=None)
    out = PostgresPromptRepository(conn).find_by_name_and_hash("nope", "abc")
    assert out is None


# ---------------------------------------------------------------------------
# latest_by_name
# ---------------------------------------------------------------------------


def test_latest_by_name_orders_by_created_at_desc():
    conn, cur = _mock_conn(fetchone=_row(id=7))

    out = PostgresPromptRepository(conn).latest_by_name("eval_system")

    assert out is not None
    assert out.id == 7
    sql, _ = cur.execute.call_args.args
    assert "ORDER BY created_at DESC" in sql
    assert "LIMIT 1" in sql


def test_latest_by_name_returns_none_when_no_row():
    conn, _ = _mock_conn(fetchone=None)
    assert PostgresPromptRepository(conn).latest_by_name("missing") is None


# ---------------------------------------------------------------------------
# insert
# ---------------------------------------------------------------------------


def test_insert_writes_all_fields_and_commits():
    conn, cur = _mock_conn(fetchone=_row(id=99))

    out = PostgresPromptRepository(conn).insert(
        name="eval_system",
        content="hello",
        version="1.0",
        hash_="deadbeef",
        previous_version_id=42,
    )

    assert out is not None
    assert out.id == 99
    sql, params = cur.execute.call_args.args
    assert "INSERT INTO prompts" in sql
    assert "RETURNING" in sql
    assert params == ("eval_system", "hello", "1.0", "deadbeef", 42)
    conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def test_history_returns_all_versions_in_chronological_order():
    rows = [_row(id=1), _row(id=2), _row(id=3)]
    conn, cur = _mock_conn(fetchall=rows)

    out = PostgresPromptRepository(conn).history("eval_system")

    assert [r.id for r in out] == [1, 2, 3]
    sql, params = cur.execute.call_args.args
    assert "ORDER BY created_at ASC" in sql
    assert params == ("eval_system",)


def test_history_returns_empty_list_when_no_rows():
    conn, _ = _mock_conn(fetchall=[])
    assert PostgresPromptRepository(conn).history("nope") == []


# ---------------------------------------------------------------------------
# list_names
# ---------------------------------------------------------------------------


def test_list_names_returns_distinct_names():
    conn, cur = _mock_conn(fetchall=[("eval_system",), ("judge_rubric",)])

    out = PostgresPromptRepository(conn).list_names()

    assert out == ["eval_system", "judge_rubric"]
    sql, _ = cur.execute.call_args
    assert "SELECT DISTINCT name FROM prompts" in sql[0]
