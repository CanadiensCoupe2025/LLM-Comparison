"""Shared retry/backoff helper for transient API failures.

Used by the judge (app/judge.py), the final-decision justification call
(app/decision.py), and the LLM adapters (app/llm_client.py) so every call site
handles rate limits (429) and server overload (503) the same way instead of
each hand-rolling its own loop.
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")

# Detected by status code if present, else by message markers — provider
# agnostic, no hard dependency on any one SDK's exception types.
_RETRYABLE_STATUS = (429, 503)
_RETRYABLE_MARKERS = ("429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE")


def is_retryable(exc: Exception) -> bool:
    """True for transient API errors (HTTP 429/503) worth retrying."""
    if getattr(exc, "code", None) in _RETRYABLE_STATUS:
        return True
    msg = str(exc)
    return any(marker in msg for marker in _RETRYABLE_MARKERS)


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_retries: int = 3,
    backoff_base: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> T:
    """Call `fn()`, retrying transient failures with exponential backoff.

    Retries up to `max_retries` times, waiting `backoff_base ** attempt`
    seconds between attempts. Non-retryable exceptions, and the exception from
    the final attempt, propagate immediately. `sleep` is injectable so tests
    don't actually wait; `on_retry(attempt, exc)` is called before each sleep
    if provided (e.g. to log the retry).
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            attempt += 1
            if attempt > max_retries or not is_retryable(e):
                raise
            if on_retry is not None:
                on_retry(attempt, e)
            sleep(backoff_base**attempt)
