from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.llm_client import (
    MODEL_REGISTRY,
    ApiSurface,
    LLMResponse,
    MissingApiKeyError,
    ProviderMismatchError,
    UnknownModelError,
    call_llm,
)

# ---------------------------------------------------------------------------
# Registry consistency
# ---------------------------------------------------------------------------


def test_registry_lists_all_models():
    assert set(MODEL_REGISTRY.keys()) == {
        "claude-sonnet-4-6",
        "claude-opus-4-8",
        "claude-haiku-4-5",
        "claude-sonnet-5",
        "gpt-5.5",
        "gpt-5.4",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    }


def test_registry_provider_counts():
    by_provider: dict[str, int] = {}
    for spec in MODEL_REGISTRY.values():
        by_provider[spec.provider] = by_provider.get(spec.provider, 0) + 1
    # Anthropic: sonnet-4-6/opus-4-8/haiku-4-5/sonnet-5; openai x2 (gpt-5.5,
    # gpt-5.4); gemini pro + flash (judge only, not a comparison model).
    assert by_provider == {"anthropic": 4, "openai": 2, "gemini": 2}


def test_openai_reasoning_models_disable_temperature():
    # gpt-5.x on the Responses surface reject temperature in reasoning mode.
    for key in ("gpt-5.5", "gpt-5.4"):
        spec = MODEL_REGISTRY[key]
        assert spec.surface == ApiSurface.RESPONSES
        assert spec.supports_temperature is False
        assert spec.returns_reasoning is True


def test_sonnet_5_disables_temperature():
    # Sonnet 5 rejects temperature/top_p/top_k (like Opus 4.7/4.8) → never send it.
    spec = MODEL_REGISTRY["claude-sonnet-5"]
    assert spec.surface == ApiSurface.MESSAGES
    assert spec.supports_temperature is False


# ---------------------------------------------------------------------------
# Anthropic — happy path
# ---------------------------------------------------------------------------


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
@patch("anthropic.Anthropic")
def test_anthropic_messages_api_returns_normalized_response(mock_anthropic_cls):
    mock_client = mock_anthropic_cls.return_value
    mock_client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Hello from Claude.")],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )

    result = call_llm("anthropic", "claude-sonnet-4-6", "hi")

    assert isinstance(result, LLMResponse)
    assert result.content == "Hello from Claude."
    assert result.reasoning is None
    assert result.tokens_in == 10
    assert result.tokens_out == 5
    assert result.model_id == "claude-sonnet-4-6"
    assert result.latency_ms >= 0

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    assert call_kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert call_kwargs["temperature"] == 0.0


# ---------------------------------------------------------------------------
# OpenAI — Responses API (gpt-5.4/gpt-5.5) happy path
# ---------------------------------------------------------------------------


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
@patch("openai.OpenAI")
def test_openai_responses_api_surfaces_reasoning_summary(mock_openai_cls):
    mock_client = mock_openai_cls.return_value
    mock_client.responses.create.return_value = SimpleNamespace(
        output_text="Hello from GPT-5.5.",
        output=[
            SimpleNamespace(
                type="reasoning",
                summary=[SimpleNamespace(text="Thinking step 1")],
            )
        ],
        usage=SimpleNamespace(input_tokens=12, output_tokens=8),
    )

    result = call_llm("openai", "gpt-5.5", "hi")

    assert result.content == "Hello from GPT-5.5."
    assert result.reasoning == "Thinking step 1"
    assert result.tokens_in == 12
    assert result.tokens_out == 8
    assert result.model_id == "gpt-5.5"

    call_kwargs = mock_client.responses.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-5.5"
    assert call_kwargs["input"] == "hi"
    assert "temperature" not in call_kwargs  # reasoning models reject temperature


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
@patch("openai.OpenAI")
def test_openai_retries_transient_error_then_succeeds(mock_openai_cls):
    """A transient 429/503 is retried; a later success returns a response."""
    mock_client = mock_openai_cls.return_value
    calls = {"n": 0}

    def flaky_create(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("503 UNAVAILABLE: high demand")
        return SimpleNamespace(
            output_text="Recovered.",
            output=[],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    mock_client.responses.create.side_effect = flaky_create

    with patch("time.sleep"):
        result = call_llm("openai", "gpt-5.5", "hi")

    assert result.content == "Recovered."
    assert calls["n"] == 3


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
@patch("openai.OpenAI")
def test_openai_does_not_retry_non_transient_error(mock_openai_cls):
    mock_client = mock_openai_cls.return_value
    calls = {"n": 0}

    def bad_request(**kwargs):
        calls["n"] += 1
        raise RuntimeError("400 INVALID_ARGUMENT")

    mock_client.responses.create.side_effect = bad_request

    with pytest.raises(RuntimeError):
        call_llm("openai", "gpt-5.5", "hi")
    assert calls["n"] == 1  # no retry


# ---------------------------------------------------------------------------
# Gemini — retry behavior (judge model, called the same way as any adapter)
# ---------------------------------------------------------------------------


@patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
@patch("google.genai.Client")
def test_gemini_retries_transient_error_then_succeeds(mock_genai_cls):
    mock_client = mock_genai_cls.return_value
    calls = {"n": 0}

    def flaky_generate(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return SimpleNamespace(
            text="Recovered.",
            usage_metadata=SimpleNamespace(
                prompt_token_count=1, candidates_token_count=1
            ),
        )

    mock_client.models.generate_content.side_effect = flaky_generate

    with patch("time.sleep"):
        result = call_llm("gemini", "gemini-2.5-pro", "hi")

    assert result.content == "Recovered."
    assert calls["n"] == 3


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


def test_unknown_model_raises_unknown_model_error():
    with pytest.raises(UnknownModelError):
        call_llm("anthropic", "claude-no-such-model", "hi")


def test_provider_mismatch_raises():
    """Asking 'openai' for a Claude model is a programmer error and must be caught."""
    with pytest.raises(ProviderMismatchError):
        call_llm("openai", "claude-sonnet-4-6", "hi")


@patch.dict(os.environ, {}, clear=True)
@patch("anthropic.Anthropic")
def test_missing_api_key_raises(mock_anthropic_cls):
    """The adapter must read the env var even if the SDK is mocked."""
    with pytest.raises(MissingApiKeyError):
        call_llm("anthropic", "claude-sonnet-4-6", "hi")
    mock_anthropic_cls.assert_not_called()
