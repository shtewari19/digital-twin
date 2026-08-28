"""Azure OpenAI chat client for reaction/report generation.

Sync `AzureOpenAI` (not langchain, not the async client) — matches
activities.py's existing convention: activities run on worker.py's
activity_executor thread pool, not the asyncio event loop, so a plain
blocking client is the right shape.
"""

from __future__ import annotations

from openai import AzureOpenAI

from app.config import settings

_client: AzureOpenAI | None = None


def get_chat_client() -> AzureOpenAI:
    global _client
    if _client is None:
        _client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )
    return _client


def call_chat(
    system_prompt: str, user_prompt: str, *, max_tokens: int = 300, temperature: float = 0.4
) -> str:
    """One chat completion. Retries are handled by the caller (Temporal's
    activity RetryPolicy already covers transient failures at the batch
    level; a per-call retry here would double up)."""
    client = get_chat_client()
    resp = client.chat.completions.create(
        model=settings.azure_openai_deployment,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""
