"""Contract tests for message endpoints, nested under a study
(app/api/v1/messages.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fakes import FakeAsyncSession

from app.api.deps import get_db
from app.db.models.message import Message as MessageRow
from app.db.models.study import Study as StudyRow


def _install_db(app, session: FakeAsyncSession) -> None:
    app.dependency_overrides[get_db] = lambda: session


def _message_row(study_id: uuid.UUID, **overrides) -> MessageRow:
    now = datetime.now(tz=UTC)
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": None,
        "study_id": study_id,
        "text": "Superior efficacy vs standard of care",
        "group": None,
        "version": 1,
        "position": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return MessageRow(**defaults)


# ---------------------------------------------------------------------------
# GET /studies/{study_id}/messages
# ---------------------------------------------------------------------------


def test_list_messages_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.get(f"/api/v1/studies/{uuid.uuid4()}/messages")

    assert response.status_code == 404


def test_list_messages_orders_by_position_then_created_at(client, app, current_user):
    study_id = uuid.uuid4()
    rows = [_message_row(study_id, text="first"), _message_row(study_id, text="second")]
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id), execute_rows=rows))

    response = client.get(f"/api/v1/studies/{study_id}/messages")

    assert response.status_code == 200
    body = response.json()
    assert [m["text"] for m in body["data"]] == ["first", "second"]
    assert body["has_more"] is False
    assert body["next_cursor"] is None


# ---------------------------------------------------------------------------
# POST /studies/{study_id}/messages
# ---------------------------------------------------------------------------


def test_create_message_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.post(
        f"/api/v1/studies/{uuid.uuid4()}/messages", json={"text": "Claim A"}
    )

    assert response.status_code == 404


def test_create_message_requires_text(client, app, current_user):
    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id)))

    response = client.post(f"/api/v1/studies/{study_id}/messages", json={})

    assert response.status_code == 422


def test_create_message_happy_path(client, app, current_user):
    study_id = uuid.uuid4()
    session = FakeAsyncSession(get_result=StudyRow(id=study_id))
    _install_db(app, session)

    response = client.post(
        f"/api/v1/studies/{study_id}/messages",
        json={"text": "Superior efficacy vs standard of care", "group": "set-A"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["text"] == "Superior efficacy vs standard of care"
    assert body["group"] == "set-A"
    assert body["version"] == 1
    assert session.commit_count == 1
    assert len(session.added) == 1


# ---------------------------------------------------------------------------
# PATCH /studies/{study_id}/messages/{message_id}
# ---------------------------------------------------------------------------


def test_update_message_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.patch(
        f"/api/v1/studies/{uuid.uuid4()}/messages/{uuid.uuid4()}", json={"text": "new"}
    )

    assert response.status_code == 404


def test_update_message_rejects_message_belonging_to_different_study(client, app, current_user):
    other_study_id = uuid.uuid4()
    row = _message_row(other_study_id)
    _install_db(app, FakeAsyncSession(get_result=row))

    response = client.patch(
        f"/api/v1/studies/{uuid.uuid4()}/messages/{row.id}", json={"text": "new"}
    )

    assert response.status_code == 404


def test_update_message_bumps_version_only_when_fields_change(client, app, current_user):
    study_id = uuid.uuid4()
    row = _message_row(study_id)
    _install_db(app, FakeAsyncSession(get_result=row))

    response = client.patch(
        f"/api/v1/studies/{study_id}/messages/{row.id}", json={"text": "Revised claim"}
    )

    assert response.status_code == 200
    assert row.text == "Revised claim"
    assert row.version == 2


def test_update_message_empty_body_does_not_bump_version(client, app, current_user):
    study_id = uuid.uuid4()
    row = _message_row(study_id)
    _install_db(app, FakeAsyncSession(get_result=row))

    response = client.patch(f"/api/v1/studies/{study_id}/messages/{row.id}", json={})

    assert response.status_code == 200
    assert row.version == 1


# ---------------------------------------------------------------------------
# DELETE /studies/{study_id}/messages/{message_id}
# ---------------------------------------------------------------------------


def test_delete_message_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.delete(f"/api/v1/studies/{uuid.uuid4()}/messages/{uuid.uuid4()}")

    assert response.status_code == 404


def test_delete_message_happy_path(client, app, current_user):
    study_id = uuid.uuid4()
    row = _message_row(study_id)
    session = FakeAsyncSession(get_result=row)
    _install_db(app, session)

    response = client.delete(f"/api/v1/studies/{study_id}/messages/{row.id}")

    assert response.status_code == 204
    assert session.deleted == [row]
    assert session.commit_count == 1
