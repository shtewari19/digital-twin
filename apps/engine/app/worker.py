"""Entrypoint for the Temporal worker process."""

import asyncio
import concurrent.futures
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from app.activities import StudyDataActivities, embed_batch, generate_reaction_batch, score_batch
from app.config import settings
from app.db import create_pool
from app.workflows.study_run import StudyRunWorkflow

log = logging.getLogger("engine.worker")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    pool = await create_pool()
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    data_activities = StudyDataActivities(pool)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as activity_executor:
        worker = Worker(
            client,
            task_queue=settings.task_queue,
            workflows=[StudyRunWorkflow],
            activities=[
                embed_batch, score_batch, generate_reaction_batch,   # sync — need the executor
                data_activities.fetch_study_context,                 # async, DB-bound
                data_activities.persist_reactions,
                data_activities.rollup_message_results,
                data_activities.update_run_status,
            ],
            activity_executor=activity_executor,
        )
        log.info("connected to %s, polling task queue '%s'", settings.temporal_host, settings.task_queue)
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())