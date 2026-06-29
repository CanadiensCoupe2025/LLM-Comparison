"""Tests for the structured JSON logging (SCRUM-32).

Cover the DoD directly: every event is valid one-line JSON carrying timestamp,
level, message, model and run_id; errors include the full stack trace; bound
context rides along and per-call `extra` overrides it.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import logging

import pytest

from app.logging_setup import (
    ContextFilter,
    JsonFormatter,
    configure_logging,
    get_logger,
    log_context,
)


@pytest.fixture
def capture():
    """An isolated logger wired to the JSON handler, writing to a buffer.

    Avoids touching the root logger so tests don't interfere with each other.
    Returns (logger, read) where read() parses the emitted JSON lines.
    """
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ContextFilter())

    logger = logging.getLogger("scrum32.test")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    def read() -> list[dict]:
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        return [json.loads(ln) for ln in lines]

    return logger, read


def test_event_is_valid_single_line_json_with_required_fields(capture):
    logger, read = capture
    with log_context(run_id=42):
        logger.info("hello", extra={"model": "claude-sonnet-4-6"})

    raw = read()
    assert len(raw) == 1
    event = raw[0]
    # DoD #1: timestamp, level, message, model, run_id all present.
    for field in ("timestamp", "level", "message", "model", "run_id"):
        assert field in event
    assert event["level"] == "INFO"
    assert event["message"] == "hello"
    assert event["model"] == "claude-sonnet-4-6"
    assert event["run_id"] == 42


def test_one_event_is_one_line(capture):
    logger, read = capture
    logger.info("line one")
    logger.info("line two")
    # DoD #2: one JSON object per line — two events ⇒ two parseable lines.
    assert len(read()) == 2


def test_timestamp_is_iso8601_utc(capture):
    logger, read = capture
    logger.info("when")
    ts = read()[0]["timestamp"]
    parsed = _dt.datetime.fromisoformat(ts)
    assert parsed.utcoffset() == _dt.timedelta(0)


def test_context_fields_default_to_none_when_unbound(capture):
    logger, read = capture
    logger.info("no context")
    event = read()[0]
    assert event["model"] is None
    assert event["run_id"] is None


def test_extra_overrides_bound_context(capture):
    logger, read = capture
    with log_context(model="bound-model", run_id=1):
        logger.info("override", extra={"model": "call-model"})
    event = read()[0]
    assert event["model"] == "call-model"  # per-call wins
    assert event["run_id"] == 1            # bound still flows through


def test_log_context_nests_and_restores(capture):
    logger, read = capture
    with log_context(run_id=1):
        logger.info("outer")
        with log_context(run_id=2):
            logger.info("inner")
        logger.info("outer again")
    runs = [e["run_id"] for e in read()]
    assert runs == [1, 2, 1]


def test_error_includes_full_stack_trace(capture):
    logger, read = capture
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("call failed")

    event = read()[0]
    assert event["level"] == "ERROR"
    # DoD #3: the serialized exception carries the traceback and the message.
    assert "exception" in event
    assert "Traceback (most recent call last)" in event["exception"]
    assert "ValueError: boom" in event["exception"]


def test_extra_fields_are_serialized(capture):
    logger, read = capture
    logger.info("metrics", extra={"tokens_in": 10, "cost_usd": 0.5})
    event = read()[0]
    assert event["tokens_in"] == 10
    assert event["cost_usd"] == 0.5


def test_non_json_values_do_not_crash_logging(capture):
    logger, read = capture
    from decimal import Decimal

    logger.info("decimal", extra={"cost": Decimal("0.001")})
    # default=str keeps a Decimal (or any odd type) from blowing up the call.
    assert read()[0]["cost"] == "0.001"


def test_configure_logging_is_idempotent():
    root = logging.getLogger()
    before = [h for h in root.handlers if getattr(h, "_scrum32_json", False)]
    configure_logging()
    configure_logging()
    after = [h for h in root.handlers if getattr(h, "_scrum32_json", False)]
    # Exactly one JSON handler regardless of how many times we configure.
    assert len(after) == 1
    assert len(before) <= 1


def test_configure_logging_emits_json_to_its_stream():
    buf = io.StringIO()
    # Fresh root state for a deterministic assertion.
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers.clear()
    try:
        configure_logging(stream=buf)
        get_logger("scrum32.cfg").warning("configured")
        line = buf.getvalue().strip()
        event = json.loads(line)
        assert event["message"] == "configured"
        assert event["level"] == "WARNING"
    finally:
        root.handlers[:] = saved
