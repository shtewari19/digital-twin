"""Contract tests for knowledgebase endpoints (app/api/v1/knowledgebase.py).

`POST .../reindex` is intentionally not implemented in the app (see that
module's docstring), so there's nothing to test here for it. `search`
501s until `embed_texts` is wired up — both branches are covered below.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fakes import FakeAsyncSession

from app.api.deps import get_db
from app.db.models.chunk import SourceChunk
from app.db.models.source import Source as SourceRow
from app.db.models.study import Study as StudyRow


def _install_db(app, session: FakeAsyncSession) -> None:
    app.dependency_overrides[get_db] = lambda: session


def _chunk_row(study_id: uuid.UUID, source_id: uuid.UUID, **overrides) -> SourceChunk:
    now = datetime.now(tz=UTC)
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": None,
        "source_id": source_id,
        "study_id": study_id,
        "position": 0,
        "text": "Superior efficacy vs standard of care",
        "embedding": None,
        "token_count": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return SourceChunk(**defaults)


# ---------------------------------------------------------------------------
# GET /sources/{source_id}/chunks
# ---------------------------------------------------------------------------


def test_list_chunks_source_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.get(f"/api/v1/sources/{uuid.uuid4()}/chunks")

    assert response.status_code == 404


def test_list_chunks_returns_page(client, app, current_user):
    source_id = uuid.uuid4()
    study_id = uuid.uuid4()
    rows = [_chunk_row(study_id, source_id, position=i) for i in range(2)]
    _install_db(app, FakeAsyncSession(get_result=SourceRow(id=source_id), execute_rows=rows))

    response = client.get(f"/api/v1/sources/{source_id}/chunks")

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 2


def test_list_chunks_accepts_next_cursor_for_follow_up_page(client, app, current_user):
    from app.api.pagination import encode_cursor

    source_id = uuid.uuid4()
    study_id = uuid.uuid4()
    chunk = _chunk_row(study_id, source_id)
    cursor = encode_cursor(chunk.created_at, chunk.id)
    _install_db(app, FakeAsyncSession(get_result=SourceRow(id=source_id), execute_rows=[chunk]))

    response = client.get(f"/api/v1/sources/{source_id}/chunks", params={"cursor": cursor})

    assert response.status_code == 200


def test_list_chunks_rejects_invalid_cursor_with_422(client, app, current_user):
    source_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=SourceRow(id=source_id)))

    response = client.get(
        f"/api/v1/sources/{source_id}/chunks", params={"cursor": "not-a-valid-cursor"}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid pagination cursor."


# ---------------------------------------------------------------------------
# GET /studies/{study_id}/knowledgebase
# ---------------------------------------------------------------------------


def test_get_knowledgebase_status_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.get(f"/api/v1/studies/{uuid.uuid4()}/knowledgebase")

    assert response.status_code == 404


def test_get_knowledgebase_status_pending_when_no_chunks_yet(client, app, current_user):
    study_id = uuid.uuid4()
    # `get_knowledgebase_status` issues three `session.scalar(...)` calls,
    # in order: source_count, chunk_count, embedded_count.
    session = FakeAsyncSession(get_result=StudyRow(id=study_id), scalar_results=[1, 0, 0])
    _install_db(app, session)

    response = client.get(f"/api/v1/studies/{study_id}/knowledgebase")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["coverage_pct"] == 0.0


def test_get_knowledgebase_status_processing_when_partially_embedded(client, app, current_user):
    study_id = uuid.uuid4()
    session = FakeAsyncSession(get_result=StudyRow(id=study_id), scalar_results=[1, 10, 4])
    _install_db(app, session)

    response = client.get(f"/api/v1/studies/{study_id}/knowledgebase")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processing"
    assert body["coverage_pct"] == 40.0


def test_get_knowledgebase_status_ready_when_fully_embedded(client, app, current_user):
    study_id = uuid.uuid4()
    session = FakeAsyncSession(get_result=StudyRow(id=study_id), scalar_results=[1, 10, 10])
    _install_db(app, session)

    response = client.get(f"/api/v1/studies/{study_id}/knowledgebase")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["coverage_pct"] == 100.0


# ---------------------------------------------------------------------------
# POST /studies/{study_id}/knowledgebase/search
# ---------------------------------------------------------------------------


def test_search_knowledgebase_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.post(
        f"/api/v1/studies/{uuid.uuid4()}/knowledgebase/search", json={"query": "efficacy"}
    )

    assert response.status_code == 404


def test_search_knowledgebase_returns_501_when_embeddings_not_configured(
    client, app, current_user
):
    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id)))

    response = client.post(
        f"/api/v1/studies/{study_id}/knowledgebase/search", json={"query": "efficacy"}
    )

    assert response.status_code == 501


def test_search_knowledgebase_requires_query(client, app, current_user):
    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id)))

    response = client.post(f"/api/v1/studies/{study_id}/knowledgebase/search", json={})

    assert response.status_code == 422


def test_search_knowledgebase_happy_path_ranks_by_distance(client, app, current_user, monkeypatch):
    study_id = uuid.uuid4()
    chunk = _chunk_row(study_id, uuid.uuid4(), text="Superior efficacy vs standard of care")
    session = FakeAsyncSession(get_result=StudyRow(id=study_id), execute_rows=[(chunk, 0.25)])
    _install_db(app, session)

    async def _fake_embed_texts(texts: list[str]) -> list[list[float]]:
        assert texts == ["efficacy vs competitor"]
        return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr("app.api.v1.knowledgebase.embed_texts", _fake_embed_texts)

    response = client.post(
        f"/api/v1/studies/{study_id}/knowledgebase/search",
        json={"query": "efficacy vs competitor", "top_k": 5},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["chunk_id"] == str(chunk.id)
    assert results[0]["text"] == "Superior efficacy vs standard of care"
    assert results[0]["score"] == 0.75
