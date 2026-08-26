"""Stand-ins for the LLM gateway (LiteLLM) integration.

That integration doesn't exist anywhere in this repo yet (checked every
branch) — presumably still local to whoever's building it. Every route
that needs an LLM call (embeddings, sufficiency assessment, and later the
persona/message/anchor `/llm/assist/*` endpoints) calls one of these
functions instead of a provider SDK directly, so dropping in the real
LiteLLM-backed implementation is a one-file change here, not a
route-by-route rewrite.

Each stub raises rather than fakes a plausible-looking result — a fake
embedding would produce meaningless similarity scores that look real.
"""

from __future__ import annotations


class LLMGatewayNotConfiguredError(RuntimeError):
    """Raised by every function below until the real gateway lands.

    Routes should catch this and translate it to `HTTP 501 Not
    Implemented`, not let it surface as an unhandled 500.
    """


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed `texts`.

    Intended to call OpenAI `text-embedding-3-small` (1536 dimensions —
    matches `core.source_chunks.embedding`'s fixed size in setup.sql) via
    the LiteLLM gateway once that integration exists.
    """
    raise LLMGatewayNotConfiguredError(
        "Embedding generation isn't wired up yet (pending the LiteLLM gateway integration)."
    )


async def assess_sufficiency(sources: list[dict[str, str]]) -> dict[str, object]:
    """Assess whether `sources` (each `{"filename": ..., "summary": ...}`)
    are enough to run a credible study, per `app.schemas.core.Sufficiency`.
    """
    raise LLMGatewayNotConfiguredError(
        "Sufficiency assessment isn't wired up yet (pending the LiteLLM gateway integration)."
    )
