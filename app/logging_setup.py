"""Structured JSON logging for every Python component (SCRUM-32).

One log event = one line of JSON on stdout, so Azure Container Apps ships it
straight into Azure Monitor Log Analytics (`ContainerAppConsoleLogs_CL`) where
each field is queryable — no log SDK, no agent, just 12-factor stdout.

Every record carries at least: `timestamp` (ISO-8601 UTC), `level`, `logger`,
`message`. Two contextual fields the DoD asks for — `model` and `run_id` — are
bound once with `log_context(...)` and then ride along on every record emitted
inside that block, including from worker threads. Errors logged with
`exc_info=True` (or via `logger.exception`) include the full stack trace.

Java analogy: `log_context` is SLF4J's **MDC** (mapped diagnostic context) and
`ContextFilter` is what turns those MDC keys into `%X{run_id}`-style fields;
`contextvars.ContextVar` is the async/thread-safe cousin of `ThreadLocal`.

Usage
-----
    from app.logging_setup import configure_logging, get_logger, log_context

    configure_logging()                 # once, at process start (CLI entrypoint)
    log = get_logger(__name__)

    with log_context(run_id=run_id):
        log.info("run started", extra={"model": "claude-sonnet-4-6"})
        ...
        try:
            ...
        except Exception:
            log.exception("call failed")   # message + full stack trace
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as _dt
import json
import logging
import sys
from typing import Any, Iterator

# Fields the DoD requires on every event. Always present (None when unbound) so
# downstream queries can rely on the columns existing.
_CONTEXT_FIELDS = ("model", "run_id")

# The standard LogRecord attributes — anything NOT in here that lands on a record
# is treated as a caller-supplied `extra` field and serialized into the JSON.
_RESERVED = frozenset(
    logging.makeLogRecord({}).__dict__.keys()
    | {"message", "asctime", "taskName"}
)

# Thread/async-safe bag of bound context (the "MDC"). Default is empty; each
# `log_context` push replaces it with a merged copy and restores it on exit.
_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "log_context", default={}
)


@contextlib.contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Bind `fields` onto every log record emitted inside the block.

    Nests cleanly: inner binds shadow outer ones and the previous context is
    restored on exit. Because the value is a `ContextVar`, a context captured
    by `ThreadPoolExecutor`/`copy_context` is visible inside worker threads.
    """
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


class ContextFilter(logging.Filter):
    """Stamp bound-context + the required DoD fields onto each record.

    A per-call `extra={"model": ...}` wins over the bound context, which wins
    over the `None` default — so `model`/`run_id` are guaranteed to exist.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        bound = _context.get()
        for key, value in bound.items():
            if key not in record.__dict__:
                record.__dict__[key] = value
        for key in _CONTEXT_FIELDS:
            record.__dict__.setdefault(key, None)
        return True


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single line of JSON.

    Key order is stable (timestamp, level, logger, message, then the DoD
    context fields, then any extras, then exception info) so logs read well
    both raw and in Log Analytics.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in _CONTEXT_FIELDS:
            payload[key] = record.__dict__.get(key)

        # Any caller-supplied `extra=` keys that aren't standard record attrs.
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in _CONTEXT_FIELDS:
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # default=str so Decimals/paths/etc. never blow up a log call.
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int | str = logging.INFO, *, stream: Any = None) -> None:
    """Install the JSON handler on the root logger. Idempotent.

    Safe to call from every CLI entrypoint: a marker on the handler stops a
    second call from stacking duplicate handlers. Defaults to stdout, the
    stream Azure Container Apps captures into Log Analytics.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        if getattr(handler, "_scrum32_json", False):
            handler.setLevel(level)
            return

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ContextFilter())
    handler.setLevel(level)
    handler._scrum32_json = True  # type: ignore[attr-defined]
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper so callers don't import `logging` directly."""
    return logging.getLogger(name)
