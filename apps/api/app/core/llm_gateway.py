"""The app's one seam onto LLM calls — everything routes through here
instead of importing `app.llm.llm_client` or a provider SDK directly.

As of PR #40, `app.llm.llm_client.call_llm` gives us LiteLLM-backed chat
completion (with Langfuse tracing), so `call_llm_json` and
`assess_sufficiency` below are real. There's still no embedding function
anywhere in this repo, so `embed_texts` stays stubbed — routes that need
it (knowledgebase search) report that plainly as a 501 rather than
faking a result.
"""

from __future__ import annotations

import asyncio
import json

from app.llm.llm_client import (
    LLMError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    call_llm,
)

__all__ = [
    "LLMError",
    "LLMGatewayNotConfiguredError",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMResponseParseError",
    "LLMTimeoutError",
    "assess_sufficiency",
    "call_llm_json",
    "embed_texts",
]


class LLMGatewayNotConfiguredError(RuntimeError):
    """Raised by `embed_texts` until a real embedding call exists.

    Routes should catch this and translate it to `HTTP 501 Not
    Implemented`, not let it surface as an unhandled 500.
    """


class LLMResponseParseError(RuntimeError):
    """Raised when the model's response isn't the JSON shape requested."""


async def call_llm_json(prompt: str, *, model: str) -> dict[str, object]:
    """Call `call_llm` off the event loop thread and parse its response as JSON.

    `call_llm` is synchronous (a plain `def`, calling `litellm.completion`
    — not `litellm.acompletion`) — running it directly from an `async
    def` route would block this process's single event loop for the
    entire request, stalling every other concurrent request. Every
    caller here goes through `asyncio.to_thread` instead.

    There's no way to request JSON-mode output through `call_llm`'s
    fixed `(prompt, model, api_key)` signature, so compliance is by
    prompting only — callers should ask for JSON explicitly and be
    ready for `LLMResponseParseError` for the rest.

    Raises:
        LLMError: on a provider-side failure (subclasses:
            `LLMRateLimitError`, `LLMTimeoutError`, `LLMProviderError`).
        LLMResponseParseError: if the model's response isn't valid JSON.
    """
    raw = await asyncio.to_thread(call_llm, prompt, model)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMResponseParseError(f"Model response wasn't valid JSON: {raw[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise LLMResponseParseError(f"Model response wasn't a JSON object: {raw[:200]!r}")
    return parsed


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed `texts`.

    Intended to call OpenAI `text-embedding-3-small` (1536 dimensions —
    matches `core.source_chunks.embedding`'s fixed size in setup.sql).
    `call_llm` only does chat completion, so this has nothing to call
    yet — still stubbed.
    """
    raise LLMGatewayNotConfiguredError(
        "Embedding generation isn't wired up yet (call_llm only does chat completion)."
    )


async def assess_sufficiency(
    sources: list[dict[str, str]], *, model: str
) -> dict[str, object]:
    """LLM assessment of whether `sources` are enough to run a credible
    study, per `app.schemas.core.Sufficiency` (`sufficient`, `summary`,
    `gaps`). Each item in `sources` is `{"filename": ..., "summary": ...}`.
    """
    listing = "\n".join(f"- {s['filename']}: {s['summary'] or '(no summary yet)'}" for s in sources)
    prompt = (
        "A researcher has uploaded these source documents for a "
        f"message-testing study:\n{listing or '(no sources uploaded yet)'}\n\n"
        "Assess whether these are enough to run a credible study. "
        "Respond with ONLY a JSON object, no markdown, matching exactly:\n"
        '{"sufficient": true/false, "summary": "...", "gaps": ["...", ...]}'
    )
    return await call_llm_json(prompt, model=model)
