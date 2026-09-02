"""Unit tests for the app's LLM seam (app/core/llm_gateway.py).

`call_llm` itself (LiteLLM plumbing) is covered by test_llm_client.py;
these tests cover this module's own logic: running `call_llm` off-thread,
JSON-mode parsing/validation, and the still-stubbed `embed_texts`.
"""

from __future__ import annotations

import pytest

from app.core.llm_gateway import (
    LLMGatewayNotConfiguredError,
    LLMResponseParseError,
    assess_sufficiency,
    call_llm_json,
    embed_texts,
)

# ---------------------------------------------------------------------------
# call_llm_json
# ---------------------------------------------------------------------------


async def test_call_llm_json_parses_valid_json_object(monkeypatch):
    monkeypatch.setattr(
        "app.core.llm_gateway.call_llm", lambda prompt, model: '{"a": 1, "b": "two"}'
    )

    result = await call_llm_json("prompt", model="openai/gpt-4o")

    assert result == {"a": 1, "b": "two"}


async def test_call_llm_json_rejects_non_json_response(monkeypatch):
    monkeypatch.setattr("app.core.llm_gateway.call_llm", lambda prompt, model: "not json at all")

    with pytest.raises(LLMResponseParseError, match="wasn't valid JSON"):
        await call_llm_json("prompt", model="openai/gpt-4o")


async def test_call_llm_json_rejects_json_array(monkeypatch):
    """A JSON *array* parses fine but isn't the object shape every caller
    in this app expects — must be rejected the same as malformed JSON.
    """
    monkeypatch.setattr("app.core.llm_gateway.call_llm", lambda prompt, model: "[1, 2, 3]")

    with pytest.raises(LLMResponseParseError, match="wasn't a JSON object"):
        await call_llm_json("prompt", model="openai/gpt-4o")


async def test_call_llm_json_rejects_json_scalar(monkeypatch):
    monkeypatch.setattr("app.core.llm_gateway.call_llm", lambda prompt, model: "42")

    with pytest.raises(LLMResponseParseError, match="wasn't a JSON object"):
        await call_llm_json("prompt", model="openai/gpt-4o")


async def test_call_llm_json_propagates_provider_error(monkeypatch):
    from app.llm.llm_client import LLMProviderError

    def _boom(prompt, model):
        raise LLMProviderError("provider down")

    monkeypatch.setattr("app.core.llm_gateway.call_llm", _boom)

    with pytest.raises(LLMProviderError):
        await call_llm_json("prompt", model="openai/gpt-4o")


# ---------------------------------------------------------------------------
# embed_texts — still a stub
# ---------------------------------------------------------------------------


async def test_embed_texts_raises_not_configured():
    with pytest.raises(LLMGatewayNotConfiguredError):
        await embed_texts(["some text"])


# ---------------------------------------------------------------------------
# assess_sufficiency
# ---------------------------------------------------------------------------


async def test_assess_sufficiency_builds_listing_from_sources(monkeypatch):
    captured: dict = {}

    def _fake_call_llm(prompt: str, model: str) -> str:
        captured["prompt"] = prompt
        return '{"sufficient": true, "summary": "Enough.", "gaps": []}'

    monkeypatch.setattr("app.core.llm_gateway.call_llm", _fake_call_llm)

    result = await assess_sufficiency(
        [{"filename": "q3-brief.pdf", "summary": "Efficacy data"}],
        model="openai/gpt-4o",
    )

    assert result == {"sufficient": True, "summary": "Enough.", "gaps": []}
    assert "q3-brief.pdf: Efficacy data" in captured["prompt"]


async def test_assess_sufficiency_handles_no_sources(monkeypatch):
    captured: dict = {}

    def _fake_call_llm(prompt: str, model: str) -> str:
        captured["prompt"] = prompt
        return '{"sufficient": false, "summary": "Nothing uploaded.", "gaps": ["everything"]}'

    monkeypatch.setattr("app.core.llm_gateway.call_llm", _fake_call_llm)

    result = await assess_sufficiency([], model="openai/gpt-4o")

    assert result["sufficient"] is False
    assert "(no sources uploaded yet)" in captured["prompt"]


async def test_assess_sufficiency_handles_source_with_no_summary_yet(monkeypatch):
    captured: dict = {}

    def _fake_call_llm(prompt: str, model: str) -> str:
        captured["prompt"] = prompt
        return '{"sufficient": false, "summary": "Too early.", "gaps": []}'

    monkeypatch.setattr("app.core.llm_gateway.call_llm", _fake_call_llm)

    await assess_sufficiency([{"filename": "raw.pdf", "summary": ""}], model="openai/gpt-4o")

    assert "raw.pdf: (no summary yet)" in captured["prompt"]
