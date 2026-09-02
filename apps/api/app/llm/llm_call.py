import logging

from langfuse import get_client

from app.llm.llm_client import call_llm

logging.basicConfig(
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

def main():
    response = call_llm(
        "provide 2 french name in 2 words only.",
        model="azure/gpt-4o-mini",
    )

    """ Self keys """
    # call_llm(
    # "Hello",
    # model="azure/gpt-4o-mini",
    # api_key="some-user-provided-key",
    # )

    """ for anthropic"""
    # response = call_llm(
    # "What is Apache Spark? Explain in one sentence.",
    # model="anthropic/claude-sonnet-4-20250514",
    # )

    """ for openai"""
    # response = call_llm(
    # "What is Apache Spark? Explain in one sentence.",
    # model="openai/gpt-4o",
    # )

    logger.info("\nLLM Response:")
    logger.info(response)


if __name__ == "__main__":
    try:
        main()
    finally:
        get_client().flush()