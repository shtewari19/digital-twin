"""Unit tests for StudyRunWorkflow (app/workflows/study_run.py).

Runs against temporalio's in-process test server (`WorkflowEnvironment.start_time_skipping`),
so the suite stays hermetic: no external Temporal, no 30-second real sleep.
"""

from __future__ import annotations

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.workflows.study_run import StudyRunWorkflow

WORKFLOW_NAME = "study_run_workflow"


def test_workflow_name_matches_api_contract():
    """The API's `app/core/temporal.py` defines STUDY_RUN_WORKFLOW_NAME under
    this exact value — the string that links the two apps at runtime.
    """
    assert StudyRunWorkflow.__temporal_workflow_definition.name == WORKFLOW_NAME
    assert WORKFLOW_NAME == "study_run_workflow"


async def test_workflow_completes_successfully():
    async with await WorkflowEnvironment.start_time_skipping() as env, Worker(
        env.client,
        task_queue="test-queue",
        workflows=[StudyRunWorkflow],
    ):
        result = await env.client.execute_workflow(
            StudyRunWorkflow.run,
            "test-study-id",
            id="test-run-id",
            task_queue="test-queue",
        )

        assert result is None


async def test_workflow_accepts_any_study_id():
    async with await WorkflowEnvironment.start_time_skipping() as env, Worker(
        env.client,
        task_queue="test-queue",
        workflows=[StudyRunWorkflow],
    ):
        result = await env.client.execute_workflow(
            StudyRunWorkflow.run,
            "study-abc-123",
            id="run-study-abc",
            task_queue="test-queue",
        )

        assert result is None


async def test_workflow_skip_time_from_thirty_second_sleep():
    """The skeleton sleeps 30s; time-skipping should complete it instantly."""
    async with await WorkflowEnvironment.start_time_skipping() as env, Worker(
        env.client,
        task_queue="test-queue",
        workflows=[StudyRunWorkflow],
    ):
        result = await env.client.execute_workflow(
            StudyRunWorkflow.run,
            "sleep-test",
            id="sleep-run",
            task_queue="test-queue",
        )

        assert result is None