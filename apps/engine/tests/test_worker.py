"""Unit tests for the Temporal worker entrypoint (app/worker.py)."""

from __future__ import annotations

import pytest

from app import worker
from app.workflows.study_run import StudyRunWorkflow
from tests.fakes import FakeTemporalClient, FakeWorker


async def test_main_raises_when_host_missing(monkeypatch):
    monkeypatch.delenv("APP_TEMPORAL_HOST", raising=False)
    monkeypatch.setenv("APP_TEMPORAL_NAMESPACE", "default")
    monkeypatch.setenv("APP_TASK_QUEUE", "test-queue")

    with pytest.raises(RuntimeError, match="APP_TEMPORAL_HOST"):
        await worker.main()


async def test_main_raises_when_namespace_missing(monkeypatch):
    monkeypatch.setenv("APP_TEMPORAL_HOST", "localhost:7233")
    monkeypatch.delenv("APP_TEMPORAL_NAMESPACE", raising=False)
    monkeypatch.setenv("APP_TASK_QUEUE", "test-queue")

    with pytest.raises(RuntimeError, match="APP_TEMPORAL_NAMESPACE"):
        await worker.main()


async def test_main_raises_when_task_queue_missing(monkeypatch):
    monkeypatch.setenv("APP_TEMPORAL_HOST", "localhost:7233")
    monkeypatch.setenv("APP_TEMPORAL_NAMESPACE", "default")
    monkeypatch.delenv("APP_TASK_QUEUE", raising=False)

    with pytest.raises(RuntimeError, match="APP_TASK_QUEUE"):
        await worker.main()


async def test_main_connects_to_temporal(monkeypatch):
    monkeypatch.setenv("APP_TEMPORAL_HOST", "temporal.example.com:7233")
    monkeypatch.setenv("APP_TEMPORAL_NAMESPACE", "production")
    monkeypatch.setenv("APP_TASK_QUEUE", "study-runs")

    monkeypatch.setattr(worker, "Client", FakeTemporalClient)

    captured_workers: list[FakeWorker] = []

    class _FakeWorkerFactory:
        def __init__(self, client, *, task_queue, workflows=None):
            self.client = client
            self.task_queue = task_queue
            self.workflows = list(workflows) if workflows else []
            captured_workers.append(self)

        async def run(self):
            pass

    monkeypatch.setattr(worker, "Worker", _FakeWorkerFactory)

    await worker.main()

    assert FakeTemporalClient.connect_calls[0]["host"] == "temporal.example.com:7233"
    assert FakeTemporalClient.connect_calls[0]["namespace"] == "production"
    assert captured_workers[0].task_queue == "study-runs"


async def test_main_registers_study_run_workflow(monkeypatch):
    monkeypatch.setenv("APP_TEMPORAL_HOST", "localhost:7233")
    monkeypatch.setenv("APP_TEMPORAL_NAMESPACE", "default")
    monkeypatch.setenv("APP_TASK_QUEUE", "test-queue")

    monkeypatch.setattr(worker, "Client", FakeTemporalClient)

    captured_workflows: list = []

    class _FakeWorkerFactory:
        def __init__(self, client, *, task_queue, workflows=None):
            captured_workflows.extend(workflows or [])

        async def run(self):
            pass

    monkeypatch.setattr(worker, "Worker", _FakeWorkerFactory)

    await worker.main()

    assert StudyRunWorkflow in captured_workflows


async def test_main_calls_worker_run(monkeypatch):
    monkeypatch.setenv("APP_TEMPORAL_HOST", "localhost:7233")
    monkeypatch.setenv("APP_TEMPORAL_NAMESPACE", "default")
    monkeypatch.setenv("APP_TASK_QUEUE", "test-queue")

    monkeypatch.setattr(worker, "Client", FakeTemporalClient)

    fake_worker = FakeWorker(None, task_queue="test-queue")
    monkeypatch.setattr(worker, "Worker", lambda *a, **kw: fake_worker)

    await worker.main()

    assert fake_worker._ran
