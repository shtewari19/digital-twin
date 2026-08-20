"""Shared Temporal client, initialized once at app startup."""

from temporalio.client import Client

from app.core.config import settings

_client: Client | None = None

STUDY_RUN_WORKFLOW_NAME = "study_run_workflow"  # must match @workflow.defn(name=...) in apps/engine


async def init_temporal_client() -> Client:
    """Called once from the FastAPI lifespan/startup hook."""
    global _client
    _client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    return _client


def get_temporal_client() -> Client:
    """Dependency-style accessor for routes. Raises if startup hasn't run."""
    if _client is None:
        raise RuntimeError("Temporal client not initialized — check app startup hook")
    return _client