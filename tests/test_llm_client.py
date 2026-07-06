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


def test_registry_lists_the_nine_required_models():
    assert set(MODEL_REGISTRY.keys()) == {
        "claude-sonnet-4-6",
        "claude-opus-4-8",
        "claude-haiku-4-5",
        "gpt-5",
        "o3",
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    }


def test_registry_provider_counts():
    by_provider: dict[str, int] = {}
    for spec in MODEL_REGISTRY.values():
        by_provider[spec.provider] = by_provider.get(spec.provider, 0) + 1
    # Anthropic: sonnet + opus + haiku; openai/deepseek x2; gemini pro + flash (judges).
    assert by_provider == {"anthropic": 3, "openai": 2, "deepseek": 2, "gemini": 2}


def test_openai_reasoning_models_disable_temperature():
    for key in ("gpt-5", "o3"):
        spec = MODEL_REGISTRY[key]
        assert spec.surface == ApiSurface.RESPONSES
        assert spec.supports_temperature is False
        assert spec.returns_reasoning is True


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
# OpenAI — Responses API (gpt-5, o3) happy path
# ---------------------------------------------------------------------------


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
@patch("openai.OpenAI")
def test_openai_responses_api_surfaces_reasoning_summary(mock_openai_cls):
    mock_client = mock_openai_cls.return_value
    mock_client.responses.create.return_value = SimpleNamespace(
        output_text="Hello from GPT-5.",
        output=[
            SimpleNamespace(
                type="reasoning",
                summary=[SimpleNamespace(text="Thinking step 1")],
            )
        ],
        usage=SimpleNamespace(input_tokens=12, output_tokens=8),
    )

    result = call_llm("openai", "gpt-5", "hi")

    assert result.content == "Hello from GPT-5."
    assert result.reasoning == "Thinking step 1"
    assert result.tokens_in == 12
    assert result.tokens_out == 8
    assert result.model_id == "gpt-5-2025-08-07"

    call_kwargs = mock_client.responses.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-5-2025-08-07"
    assert call_kwargs["input"] == "hi"
    assert "temperature" not in call_kwargs  # reasoning models reject temperature


# ---------------------------------------------------------------------------
# DeepSeek — uses the OpenAI SDK against a custom base_url
# ---------------------------------------------------------------------------


@patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"})
@patch("openai.OpenAI")
def test_deepseek_v4_pro_surfaces_reasoning_content(mock_openai_cls):
    mock_client = mock_openai_cls.return_value
    mock_message = SimpleNamespace(
        content="Hello from DeepSeek.",
        reasoning_content="My internal reasoning",
    )
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=mock_message)],
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=12),
    )

    result = call_llm("deepseek", "deepseek-v4-pro", "hi")

    assert result.content == "Hello from DeepSeek."
    assert result.reasoning == "My internal reasoning"
    assert result.tokens_in == 20
    assert result.tokens_out == 12

    # Confirm the OpenAI client was pointed at DeepSeek's endpoint with the right key.
    client_kwargs = mock_openai_cls.call_args.kwargs
    assert client_kwargs["base_url"] == "https://api.deepseek.com"
    assert client_kwargs["api_key"] == "test-key"


@patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"})
@patch("openai.OpenAI")
def test_deepseek_v4_flash_ignores_reasoning_content(mock_openai_cls):
    """V4-Flash isn't a reasoning model — even if the API emits reasoning_content, drop it."""
    mock_client = mock_openai_cls.return_value
    mock_message = SimpleNamespace(
        content="Plain answer.",
        reasoning_content="should be ignored",
    )
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=mock_message)],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2),
    )

    result = call_llm("deepseek", "deepseek-v4-flash", "hi")
    assert result.content == "Plain answer."
    assert result.reasoning is None


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
