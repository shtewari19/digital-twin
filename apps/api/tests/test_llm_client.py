"""Unit tests for the LLM client (app/llm/llm_client.py).

All tests monkeypatch litellm and environment variables so no real LLM
API calls are made.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.llm.llm_client import (
    LLMError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    _get_default_api_key,
    call_llm,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_completion(*args: Any, **kwargs: Any) -> SimpleNamespace:
    """Return a minimal litellm-like response object."""
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    message = SimpleNamespace(content="Hello from LLM")
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(usage=usage, choices=[choice])


def _fake_completion_empty(*args: Any, **kwargs: Any) -> SimpleNamespace:
    """Response with empty content."""
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=0, total_tokens=10)
    message = SimpleNamespace(content=None)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(usage=usage, choices=[choice])


# ---------------------------------------------------------------------------
# _get_default_api_key
# ---------------------------------------------------------------------------


class TestGetDefaultApiKey:
    def test_anthropic(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-123")
        assert _get_default_api_key("anthropic/claude-sonnet") == "sk-ant-123"

    def test_openai(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-456")
        assert _get_default_api_key("openai/gpt-4o") == "sk-openai-456"

    def test_azure(self, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-789")
        assert _get_default_api_key("azure/gpt-4o") == "az-789"

    def test_unknown_provider_returns_none(self):
        assert _get_default_api_key("local/llama") is None

    def test_env_not_set_returns_none(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        assert _get_default_api_key("anthropic/claude-sonnet") is None


# ---------------------------------------------------------------------------
# call_llm — happy path
# ---------------------------------------------------------------------------


class TestCallLlmHappyPath:
    def test_returns_content(self, monkeypatch):
        monkeypatch.setattr("app.llm.llm_client.litellm.completion", _fake_completion)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        result = call_llm("Say hello", model="openai/gpt-4o", api_key="sk-test")

        assert result == "Hello from LLM"

    def test_uses_explicit_api_key_over_env(self, monkeypatch):
        captured: dict = {}

        def _capture(*args: Any, **kwargs: Any) -> SimpleNamespace:
            captured["api_key"] = kwargs.get("api_key")
            return _fake_completion()

        monkeypatch.setattr("app.llm.llm_client.litellm.completion", _capture)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")

        call_llm("Hi", model="openai/gpt-4o", api_key="sk-explicit")

        assert captured["api_key"] == "sk-explicit"

    def test_azure_sets_api_base_and_version(self, monkeypatch):
        captured: dict = {}

        def _capture(*args: Any, **kwargs: Any) -> SimpleNamespace:
            captured.update(kwargs)
            return _fake_completion()

        monkeypatch.setattr("app.llm.llm_client.litellm.completion", _capture)
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://my-azure.openai.azure.com")
        monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

        call_llm("Hi", model="azure/gpt-4o")

        assert captured["api_base"] == "https://my-azure.openai.azure.com"
        assert captured["api_version"] == "2024-02-15-preview"


# ---------------------------------------------------------------------------
# call_llm — error paths
# ---------------------------------------------------------------------------


class TestCallLlmErrors:
    def test_rejects_empty_prompt(self, monkeypatch):
        with pytest.raises(ValueError, match="Prompt cannot be empty"):
            call_llm("", model="openai/gpt-4o", api_key="sk-test")

    def test_rejects_whitespace_prompt(self, monkeypatch):
        with pytest.raises(ValueError, match="Prompt cannot be empty"):
            call_llm("   ", model="openai/gpt-4o", api_key="sk-test")

    def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

        with pytest.raises(LLMProviderError, match="No API key configured"):
            call_llm("Hi", model="anthropic/claude-sonnet")

    def test_raises_rate_limit_error(self, monkeypatch):
        import litellm

        def _rate_limit(*args: Any, **kwargs: Any) -> None:
            raise litellm.RateLimitError(
                "rate limited",
                model="openai/gpt-4o",
                llm_provider="openai",
            )

        monkeypatch.setattr("app.llm.llm_client.litellm.completion", _rate_limit)

        with pytest.raises(LLMRateLimitError, match="rate limit exceeded"):
            call_llm("Hi", model="openai/gpt-4o", api_key="sk-test")

    def test_raises_timeout_error(self, monkeypatch):
        import litellm

        def _timeout(*args: Any, **kwargs: Any) -> None:
            raise litellm.Timeout("timed out", model="openai/gpt-4o", llm_provider="openai")

        monkeypatch.setattr("app.llm.llm_client.litellm.completion", _timeout)

        with pytest.raises(LLMTimeoutError, match="timed out"):
            call_llm("Hi", model="openai/gpt-4o", api_key="sk-test")

    def test_raises_provider_error_on_generic_exception(self, monkeypatch):
        def _boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("something broke")

        monkeypatch.setattr("app.llm.llm_client.litellm.completion", _boom)

        with pytest.raises(LLMProviderError, match="LLM provider error"):
            call_llm("Hi", model="openai/gpt-4o", api_key="sk-test")

    def test_raises_on_empty_response_content(self, monkeypatch):
        monkeypatch.setattr("app.llm.llm_client.litellm.completion", _fake_completion_empty)

        with pytest.raises(LLMProviderError, match="Empty response"):
            call_llm("Hi", model="openai/gpt-4o", api_key="sk-test")


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_rate_limit_is_llm_error(self):
        assert issubclass(LLMRateLimitError, LLMError)

    def test_timeout_is_llm_error(self):
        assert issubclass(LLMTimeoutError, LLMError)

    def test_provider_is_llm_error(self):
        assert issubclass(LLMProviderError, LLMError)
