"""Common LLM client for Claude, OpenAI, and Gemini.

Exposes a single entry point — `call_llm(provider, model, prompt)` — backed by
a model registry and per-provider adapters. Add a new model by adding a row to
`MODEL_REGISTRY`; no other code needs to change.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.logging_setup import get_logger

log = get_logger(__name__)


class ApiSurface(str, Enum):
    MESSAGES = "messages"            # Anthropic
    CHAT_COMPLETIONS = "chat"        # OpenAI gpt-4o family
    RESPONSES = "responses"          # OpenAI reasoning models (o-series, gpt-5)
    GEMINI = "gemini"         


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    api_id: str
    surface: ApiSurface
    supports_temperature: bool
    returns_reasoning: bool
    # Total context window (tokens). Lets the final-decision metrics express
    # average prompt size as a percentage of capacity (SCRUM-38). Mirrored in
    # the `models` table by migration 014 so SQL views can compute the %.
    context_window: int


@dataclass
class LLMResponse:
    content: str
    reasoning: str | None
    tokens_in: int
    tokens_out: int
    latency_ms: float
    model_id: str
    raw: Any = field(repr=False)


MODEL_REGISTRY: dict[str, ModelSpec] = {
    "claude-sonnet-4-6": ModelSpec(
        "anthropic", "claude-sonnet-4-6", ApiSurface.MESSAGES, True, False, 200_000
    ),
    "claude-opus-4-8": ModelSpec(
        "anthropic", "claude-opus-4-8", ApiSurface.MESSAGES, False, False, 200_000
    ),
    "claude-haiku-4-5": ModelSpec(
        "anthropic", "claude-haiku-4-5-20251001", ApiSurface.MESSAGES, True, False, 200_000
    ),
    # Sonnet 5 (like Opus 4.7/4.8) rejects temperature/top_p/top_k with a 400 —
    # supports_temperature=False so the adapter never sends it.
    "claude-sonnet-5": ModelSpec(
        "anthropic", "claude-sonnet-5", ApiSurface.MESSAGES, False, False, 1_000_000
    ),
    "gpt-5": ModelSpec(
        "openai", "gpt-5-2025-08-07", ApiSurface.RESPONSES, False, True, 400_000
    ),
    "o3": ModelSpec(
        "openai", "o3-2025-04-16", ApiSurface.RESPONSES, False, True, 200_000
    ),
    # GPT-5.x on the Responses surface (reasoning): temperature is rejected in
    # reasoning mode, so supports_temperature=False. mini/nano are the fast/cheap
    # tiers (replacing the API-unavailable GPT-5.3 Instant).
    "gpt-5.5": ModelSpec(
        "openai", "gpt-5.5", ApiSurface.RESPONSES, False, True, 400_000
    ),
    "gpt-5.4": ModelSpec(
        "openai", "gpt-5.4", ApiSurface.RESPONSES, False, True, 400_000
    ),
    "gpt-5.4-mini": ModelSpec(
        "openai", "gpt-5.4-mini", ApiSurface.RESPONSES, False, True, 400_000
    ),
    "gpt-5.4-nano": ModelSpec(
        "openai", "gpt-5.4-nano", ApiSurface.RESPONSES, False, True, 400_000
    ),
    "gemini-2.5-pro": ModelSpec(
        "gemini","gemini-2.5-pro", ApiSurface.GEMINI, True, False, 1_048_576
    ),
    "gemini-2.5-flash": ModelSpec(
        "gemini", "gemini-2.5-flash", ApiSurface.GEMINI, True, False, 1_048_576
    ),
}


class UnknownProviderError(ValueError):
    pass


class UnknownModelError(ValueError):
    pass


class ProviderMismatchError(ValueError):
    pass


class MissingApiKeyError(RuntimeError):
    pass


def _require_env(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        raise MissingApiKeyError(f"Environment variable {var} is not set")
    return value


# Output-token budget shared by every adapter. Kept deliberately high because on
# reasoning models (OpenAI Responses, e.g. gpt-5/o3) this budget is split between
# reasoning AND the visible answer — too low and reasoning eats it all, leaving an
# EMPTY response that the judge then scores ~0. Non-reasoning models stop at their
# natural completion well under this cap, so a high ceiling costs them nothing.
DEFAULT_MAX_TOKENS = 8192


class AnthropicAdapter:
    def call(
        self,
        spec: ModelSpec,
        prompt: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.0,
    ) -> LLMResponse:
        import anthropic

        # max_retries above the SDK default (2): the Sonnet pool sheds load with
        # HTTP 529 (overloaded_error) during spikes, and 2 retries weren't enough
        # to ride them out — runs lost cases. The SDK retries 529 with exponential
        # backoff for us, so a higher budget just absorbs longer overload windows.
        client = anthropic.Anthropic(
            api_key=_require_env("ANTHROPIC_API_KEY"), max_retries=8
        )
        kwargs: dict[str, Any] = {
            "model": spec.api_id,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if spec.supports_temperature:
            kwargs["temperature"] = temperature

        t0 = time.perf_counter()
        raw = client.messages.create(**kwargs)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        text = "".join(
            block.text
            for block in raw.content
            if getattr(block, "type", "") == "text"
        )
        return LLMResponse(
            content=text,
            reasoning=None,
            tokens_in=raw.usage.input_tokens,
            tokens_out=raw.usage.output_tokens,
            latency_ms=latency_ms,
            model_id=spec.api_id,
            raw=raw,
        )
class GeminiAdapter:
    def call(
            self,
            spec: ModelSpec,
            prompt: str,
            *,
            max_tokens: int = DEFAULT_MAX_TOKENS,
            temperature: float = 0.0,
    ) -> LLMResponse:
        from google import genai

        client = genai.Client(api_key=_require_env("GEMINI_API_KEY"))
        kwargs: dict[str, Any] = {
            "model": spec.api_id,
            "contents": prompt,
            "config": {
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            },
        }

        t0 = time.perf_counter()
        raw = client.models.generate_content(**kwargs)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        return LLMResponse(
            content=raw.text,
            reasoning=None,
            tokens_in=raw.usage_metadata.prompt_token_count,
            tokens_out=raw.usage_metadata.candidates_token_count,
            latency_ms=latency_ms,
            model_id=spec.api_id,
            raw=raw,
        )


class OpenAIAdapter:
    def call(
        self,
        spec: ModelSpec,
        prompt: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.0,
    ) -> LLMResponse:
        import openai

        client = openai.OpenAI(api_key=_require_env("OPENAI_API_KEY"))
        if spec.surface == ApiSurface.RESPONSES:
            return self._responses(client, spec, prompt, max_tokens=max_tokens)
        return self._chat(
            client, spec, prompt, max_tokens=max_tokens, temperature=temperature
        )

    def _responses(
        self, client: Any, spec: ModelSpec, prompt: str, *, max_tokens: int
    ) -> LLMResponse:
        t0 = time.perf_counter()
        raw = client.responses.create(
            model=spec.api_id,
            input=prompt,
            max_output_tokens=max_tokens,
            reasoning={"effort": "medium"},
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0

        content = getattr(raw, "output_text", "") or ""
        reasoning: str | None = None
        if spec.returns_reasoning:
            summaries: list[str] = []
            for item in getattr(raw, "output", []) or []:
                if getattr(item, "type", "") == "reasoning":
                    for s in getattr(item, "summary", []) or []:
                        text = getattr(s, "text", None)
                        if text:
                            summaries.append(text)
            reasoning = "\n".join(summaries) if summaries else None

        return LLMResponse(
            content=content,
            reasoning=reasoning,
            tokens_in=raw.usage.input_tokens,
            tokens_out=raw.usage.output_tokens,
            latency_ms=latency_ms,
            model_id=spec.api_id,
            raw=raw,
        )

    def _chat(
        self,
        client: Any,
        spec: ModelSpec,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": spec.api_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        if spec.supports_temperature:
            kwargs["temperature"] = temperature

        t0 = time.perf_counter()
        raw = client.chat.completions.create(**kwargs)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        return LLMResponse(
            content=raw.choices[0].message.content,
            reasoning=None,
            tokens_in=raw.usage.prompt_tokens,
            tokens_out=raw.usage.completion_tokens,
            latency_ms=latency_ms,
            model_id=spec.api_id,
            raw=raw,
        )




_ADAPTERS: dict[str, Any] = {
    "anthropic": AnthropicAdapter(),
    "openai": OpenAIAdapter(),
    "gemini": GeminiAdapter(), 
}


def call_llm(
    provider: str, model: str, prompt: str, **kwargs: Any
) -> LLMResponse:
    """Call `model` via `provider` with `prompt`, return a normalized response.

    Raises
    ------
    UnknownModelError       — `model` is not in MODEL_REGISTRY.
    ProviderMismatchError   — `model` exists but belongs to a different provider.
    UnknownProviderError    — `provider` has no adapter registered.
    MissingApiKeyError      — the provider's API-key env var is not set.
    """
    try:
        spec = MODEL_REGISTRY[model]
    except KeyError as e:
        raise UnknownModelError(f"Unknown model: {model!r}") from e

    if spec.provider != provider:
        raise ProviderMismatchError(
            f"Model {model!r} belongs to provider {spec.provider!r}, not {provider!r}"
        )

    try:
        adapter = _ADAPTERS[provider]
    except KeyError as e:
        raise UnknownProviderError(f"Unknown provider: {provider!r}") from e

    # Never log the prompt or the API key (B13) — only call shape and outcome.
    try:
        response = adapter.call(spec, prompt, **kwargs)
    except Exception:
        log.exception(
            "llm call raised", extra={"model": model, "provider": provider}
        )
        raise
    log.info(
        "llm call ok",
        extra={
            "model": model,
            "provider": provider,
            "latency_ms": round(response.latency_ms),
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
        },
    )
    return response
