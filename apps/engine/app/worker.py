"""Entrypoint for the Temporal worker process."""

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from app.workflows.study_run import StudyRunWorkflow

TEMPORAL_HOST = "localhost:7233"
TEMPORAL_NAMESPACE = "default"
TASK_QUEUE = "study-runs"

log = logging.getLogger("engine.worker")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    client = await Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE)
    worker = Worker(client, task_queue=TASK_QUEUE, workflows=[StudyRunWorkflow])

    log.info("connected to %s, polling task queue '%s'", TEMPORAL_HOST, TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())