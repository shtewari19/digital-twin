"""Contract tests for study-run endpoints (app/api/v1/runs.py)."""

from __future__ import annotations

import uuid

from fakes import (
    FakeAsyncSession,
    FakeSessionContext,
    FakeTemporalClient,
    RecordingTaskFactory,
)

from app.api.deps import get_db
from app.api.v1.runs import _finalize_when_done
from app.core import temporal
from app.core.config import settings
from app.core.temporal import STUDY_RUN_WORKFLOW_NAME
from app.db.models.run import Run, RunStatus


def _install_db(app, session: FakeAsyncSession) -> None:
    app.dependency_overrides[get_db] = lambda: session


# ---------------------------------------------------------------------------
# POST /studies/{study_id}/runs
# ---------------------------------------------------------------------------


def test_create_run_persists_draft_run(client, app):
    session = FakeAsyncSession()
    _install_db(app, session)
    study_id = uuid.uuid4()

    response = client.post(f"/api/v1/studies/{study_id}/runs")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == RunStatus.DRAFT.value
    assert body["study_id"] == str(study_id)
    assert body["workflow_id"] is None
    assert session.commit_count == 1
    assert len(session.added) == 1


def test_create_run_rejects_non_uuid_study_id(client):
    response = client.post("/api/v1/studies/not-a-uuid/runs")

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /runs/{run_id}/start
# ---------------------------------------------------------------------------


def test_start_unknown_run_returns_404(client, app):
    session = FakeAsyncSession(get_result=None)
    _install_db(app, session)

    response = client.post(f"/api/v1/runs/{uuid.uuid4()}/start")

    assert response.status_code == 404
    assert response.json()["detail"] == "run not found"


def test_start_non_draft_run_conflicts(client, app):
    run = Run(id=uuid.uuid4(), study_id=uuid.uuid4(), status=RunStatus.QUEUED)
    _install_db(app, FakeAsyncSession(get_result=run))

    response = client.post(f"/api/v1/runs/{run.id}/start")

    assert response.status_code == 409
    assert "queued" in response.json()["detail"]


def test_start_run_starts_workflow_and_marks_running(client, app, monkeypatch):
    run = Run(id=uuid.uuid4(), study_id=uuid.uuid4(), status=RunStatus.DRAFT)
    _install_db(app, FakeAsyncSession(get_result=run))

    fake_client = FakeTemporalClient()
    monkeypatch.setattr(temporal, "_client", fake_client)

    tasks = RecordingTaskFactory()
    monkeypatch.setattr("app.api.v1.runs.asyncio", tasks)

    response = client.post(f"/api/v1/runs/{run.id}/start")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == RunStatus.RUNNING.value
    assert body["workflow_id"] == f"study-run-{run.id}"

    assert len(fake_client.started) == 1
    started = fake_client.started[0]
    assert started["name"] == STUDY_RUN_WORKFLOW_NAME
    assert started["arg"] == str(run.study_id)
    assert started["id"] == f"study-run-{run.id}"
    assert started["task_queue"] == settings.task_queue

    # The background finalizer is scheduled exactly once.
    assert len(tasks.coroutines) == 1


# ---------------------------------------------------------------------------
# _finalize_when_done
# ---------------------------------------------------------------------------


def _install_finalizer_env(monkeypatch, session, fake_client) -> None:
    monkeypatch.setattr("app.api.v1.runs.SessionLocal", lambda: FakeSessionContext(session))
    monkeypatch.setattr(temporal, "_client", fake_client)


async def test_finalize_marks_success_when_workflow_succeeds(monkeypatch):
    run = Run(study_id=uuid.uuid4(), status=RunStatus.RUNNING)
    session = FakeAsyncSession(get_result=run)
    _install_finalizer_env(monkeypatch, session, FakeTemporalClient())

    await _finalize_when_done(run.id, "wf-1")

    assert run.status == RunStatus.FINALIZED
    assert session.commit_count == 1


async def test_finalize_marks_failure_when_workflow_raises(monkeypatch):
    run = Run(study_id=uuid.uuid4(), status=RunStatus.RUNNING)
    session = FakeAsyncSession(get_result=run)
    failing = FakeTemporalClient(handle_error=RuntimeError("workflow exploded"))
    _install_finalizer_env(monkeypatch, session, failing)

    await _finalize_when_done(run.id, "wf-1")

    assert run.status == RunStatus.FAILED
    assert session.commit_count == 1
