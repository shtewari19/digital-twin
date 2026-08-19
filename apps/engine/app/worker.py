"""Entrypoint for the Temporal worker process."""

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.worker import Worker

from app.workflows.study_run import StudyRunWorkflow

for env_file in Path(__file__).resolve().parents[3].rglob(".env"):
    load_dotenv(env_file)
    break

log = logging.getLogger("engine.worker")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    temporal_host = os.getenv("APP_TEMPORAL_HOST")
    temporal_namespace = os.getenv("APP_TEMPORAL_NAMESPACE")
    task_queue = os.getenv("APP_TASK_QUEUE")

    if not temporal_host:
        raise RuntimeError("APP_TEMPORAL_HOST is not set")

    if not temporal_namespace:
        raise RuntimeError("APP_TEMPORAL_NAMESPACE is not set")

    if not task_queue:
        raise RuntimeError("APP_TASK_QUEUE is not set")

    client = await Client.connect(
        temporal_host,
        namespace=temporal_namespace,
    )

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[StudyRunWorkflow],
    )

    log.info(
        "connected to %s, polling task queue '%s'",
        temporal_host,
        task_queue,
    )

    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())