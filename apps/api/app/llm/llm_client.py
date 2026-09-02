import logging
import os

import litellm
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Enable automatic Langfuse tracing for LiteLLM.
litellm.callbacks = ["langfuse_otel"]


class LLMError(Exception):
    """Base exception for LLM errors."""


class LLMRateLimitError(LLMError):
    """Raised when an LLM provider rate-limits the request."""


class LLMTimeoutError(LLMError):
    """Raised when an LLM request times out."""


class LLMProviderError(LLMError):
    """Raised when an LLM provider is unavailable."""


def _get_default_api_key(model: str) -> str | None:
    """Get the default API key for the selected provider."""

    if model.startswith("anthropic/"):
        return os.getenv("ANTHROPIC_API_KEY")

    if model.startswith("openai/"):
        return os.getenv("OPENAI_API_KEY")

    if model.startswith("azure/"):
        return os.getenv("AZURE_OPENAI_API_KEY")

    return None


def call_llm(
    prompt: str,
    model: str,
    api_key: str | None = None,
) -> str:
    """
    Call an LLM through LiteLLM with automatic Langfuse tracing.
    """

    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")

    provider_api_key = api_key or _get_default_api_key(model)

    if not provider_api_key:
        raise LLMProviderError(
            f"No API key configured for model: {model}"
        )

    try:
        kwargs = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "api_key": provider_api_key,
            "timeout": 30,

        }

        # Azure-specific configuration.
        if model.startswith("azure/"):
            kwargs["api_base"] = os.getenv(
                "AZURE_OPENAI_ENDPOINT"
            )
            kwargs["api_version"] = os.getenv(
                "AZURE_OPENAI_API_VERSION"
            )

        response = litellm.completion(**kwargs)

        # Token usage for local logging/metering.
        usage = response.usage

        # Give the async OTEL exporter time to send the trace.
        # time.sleep(5)
        logger.info(
            "LLM usage | model=%s | prompt_tokens=%s | "
            "completion_tokens=%s | total_tokens=%s",
            model,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.total_tokens,
        )

        content = response.choices[0].message.content

        if not content:
            raise LLMProviderError(
                f"Empty response received from {model}"
            )

        return content

    except litellm.RateLimitError as exc:
        logger.error(
            "Rate limit from provider | model=%s",
            model,
        )
        raise LLMRateLimitError(
            "LLM provider rate limit exceeded."
        ) from exc

    except litellm.Timeout as exc:
        logger.error(
            "Timeout from provider | model=%s",
            model,
        )
        raise LLMTimeoutError(
            "LLM provider request timed out."
        ) from exc

    except Exception as exc:
        logger.exception(
            "LLM provider error | model=%s",
            model,
        )
        raise LLMProviderError(
            f"LLM provider error: {exc}"
        ) from exc