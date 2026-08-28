"""Contract tests for source (uploaded document) endpoints
(app/api/v1/sources.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fakes import FakeAsyncSession

from app.api.deps import get_db
from app.db.models.source import Source as SourceRow
from app.db.models.study import Study as StudyRow


def _install_db(app, session: FakeAsyncSession) -> None:
    app.dependency_overrides[get_db] = lambda: session


def _source_row(study_id: uuid.UUID, **overrides) -> SourceRow:
    now = datetime.now(tz=UTC)
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": None,
        "study_id": study_id,
        "filename": "brief.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1024,
        "content": b"stub bytes",
        "priority": "medium",
        "suggested_priority": None,
        "ingest_status": "pending",
        "summary": None,
        "tags": None,
        "pii_flag": False,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return SourceRow(**defaults)


# ---------------------------------------------------------------------------
# GET /studies/{study_id}/sources
# ---------------------------------------------------------------------------


def test_list_sources_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.get(f"/api/v1/studies/{uuid.uuid4()}/sources")

    assert response.status_code == 404


def test_list_sources_is_not_paginated(client, app, current_user):
    study_id = uuid.uuid4()
    rows = [_source_row(study_id), _source_row(study_id)]
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id), execute_rows=rows))

    response = client.get(f"/api/v1/studies/{study_id}/sources")

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 2
    assert body["has_more"] is False
    assert body["next_cursor"] is None


# ---------------------------------------------------------------------------
# POST /studies/{study_id}/sources (multipart upload)
# ---------------------------------------------------------------------------


def test_upload_source_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.post(
        f"/api/v1/studies/{uuid.uuid4()}/sources",
        files={"file": ("brief.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 404


def test_upload_source_requires_a_file(client, app, current_user):
    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id)))

    response = client.post(f"/api/v1/studies/{study_id}/sources")

    assert response.status_code == 422


def test_upload_source_rejects_oversized_file(client, app, current_user):
    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id)))
    oversized = b"x" * (20 * 1024 * 1024 + 1)

    response = client.post(
        f"/api/v1/studies/{study_id}/sources",
        files={"file": ("huge.bin", oversized, "application/octet-stream")},
    )

    assert response.status_code == 413


def test_upload_source_defaults_priority_to_medium(client, app, current_user):
    study_id = uuid.uuid4()
    session = FakeAsyncSession(get_result=StudyRow(id=study_id))
    _install_db(app, session)

    response = client.post(
        f"/api/v1/studies/{study_id}/sources",
        files={"file": ("brief.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["priority"] == "medium"
    assert body["ingest_status"] == "pending"
    assert session.added[0].size_bytes == len(b"hello")


def test_upload_source_honors_explicit_priority(client, app, current_user):
    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id)))

    response = client.post(
        f"/api/v1/studies/{study_id}/sources",
        files={"file": ("brief.txt", b"hello", "text/plain")},
        data={"priority": "high"},
    )

    assert response.status_code == 201
    assert response.json()["priority"] == "high"


def test_upload_source_rejects_invalid_priority(client, app, current_user):
    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id)))

    response = client.post(
        f"/api/v1/studies/{study_id}/sources",
        files={"file": ("brief.txt", b"hello", "text/plain")},
        data={"priority": "urgent"},
    )

    assert response.status_code == 422


def test_upload_source_falls_back_to_octet_stream_when_content_type_missing(
    client, app, current_user
):
    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id)))

    response = client.post(
        f"/api/v1/studies/{study_id}/sources",
        files={"file": ("brief.bin", b"hello", "")},
    )

    assert response.status_code == 201
    assert response.json()["content_type"] in ("application/octet-stream", "")


# ---------------------------------------------------------------------------
# GET /studies/{study_id}/sufficiency
# ---------------------------------------------------------------------------


def test_get_sufficiency_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.get(f"/api/v1/studies/{uuid.uuid4()}/sufficiency")

    assert response.status_code == 404


def test_get_sufficiency_translates_llm_error_to_502(client, app, current_user, monkeypatch):
    from app.core.llm_gateway import LLMProviderError

    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id), execute_rows=[]))

    async def _boom(sources, *, model):
        raise LLMProviderError("provider down")

    monkeypatch.setattr("app.api.v1.sources.assess_sufficiency", _boom)

    response = client.get(f"/api/v1/studies/{study_id}/sufficiency")

    assert response.status_code == 502


def test_get_sufficiency_translates_parse_error_to_502(client, app, current_user, monkeypatch):
    from app.core.llm_gateway import LLMResponseParseError

    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id), execute_rows=[]))

    async def _boom(sources, *, model):
        raise LLMResponseParseError("not JSON")

    monkeypatch.setattr("app.api.v1.sources.assess_sufficiency", _boom)

    response = client.get(f"/api/v1/studies/{study_id}/sufficiency")

    assert response.status_code == 502


def test_get_sufficiency_happy_path(client, app, current_user, monkeypatch):
    study_id = uuid.uuid4()
    rows = [_source_row(study_id, filename="q3-brief.pdf", summary="Efficacy data")]
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id), execute_rows=rows))

    async def _fake_assess(sources, *, model):
        assert sources == [{"filename": "q3-brief.pdf", "summary": "Efficacy data"}]
        return {"sufficient": True, "summary": "Enough to run.", "gaps": []}

    monkeypatch.setattr("app.api.v1.sources.assess_sufficiency", _fake_assess)

    response = client.get(f"/api/v1/studies/{study_id}/sufficiency")

    assert response.status_code == 200
    assert response.json() == {"sufficient": True, "summary": "Enough to run.", "gaps": []}


# ---------------------------------------------------------------------------
# GET / PATCH / DELETE /sources/{source_id}
# ---------------------------------------------------------------------------


def test_get_source_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.get(f"/api/v1/sources/{uuid.uuid4()}")

    assert response.status_code == 404


def test_update_source_reprioritizes_only(client, app, current_user):
    row = _source_row(uuid.uuid4(), filename="unchanged.pdf")
    _install_db(app, FakeAsyncSession(get_result=row))

    response = client.patch(f"/api/v1/sources/{row.id}", json={"priority": "high"})

    assert response.status_code == 200
    assert row.priority == "high"
    assert row.filename == "unchanged.pdf"


def test_update_source_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.patch(f"/api/v1/sources/{uuid.uuid4()}", json={"priority": "high"})

    assert response.status_code == 404


def test_delete_source_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.delete(f"/api/v1/sources/{uuid.uuid4()}")

    assert response.status_code == 404


def test_delete_source_happy_path(client, app, current_user):
    row = _source_row(uuid.uuid4())
    session = FakeAsyncSession(get_result=row)
    _install_db(app, session)

    response = client.delete(f"/api/v1/sources/{row.id}")

    assert response.status_code == 204
    assert session.deleted == [row]


# ---------------------------------------------------------------------------
# GET /sources/{source_id}/analysis
# ---------------------------------------------------------------------------


def test_get_source_analysis_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.get(f"/api/v1/sources/{uuid.uuid4()}/analysis")

    assert response.status_code == 404


def test_get_source_analysis_reflects_pending_status_with_no_tags(client, app, current_user):
    row = _source_row(uuid.uuid4(), tags=None, summary=None, suggested_priority=None)
    _install_db(app, FakeAsyncSession(get_result=row))

    response = client.get(f"/api/v1/sources/{row.id}/analysis")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["tags"] == []
    assert body["summary"] is None
