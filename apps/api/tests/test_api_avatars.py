"""Contract tests for avatar endpoints and the per-study panel sub-resource
(app/api/v1/avatars.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fakes import FakeAsyncSession
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_db
from app.db.models.avatar import Avatar as AvatarRow
from app.db.models.domain import Domain as DomainRow
from app.db.models.study import Study as StudyRow


def _install_db(app, session: FakeAsyncSession) -> None:
    app.dependency_overrides[get_db] = lambda: session


def _avatar_row(**overrides) -> AvatarRow:
    now = datetime.now(tz=UTC)
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": None,
        "scope": "library",
        "domain_id": uuid.uuid4(),
        "study_id": None,
        "name": "The Evidence-Driven Specialist",
        "profile": "Weighs RCT data heavily.",
        "source": "custom",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return AvatarRow(**defaults)


# ---------------------------------------------------------------------------
# GET /avatars
# ---------------------------------------------------------------------------


def test_list_avatars_returns_rows_in_query_order(client, app, current_user):
    rows = [_avatar_row(name="oldest"), _avatar_row(name="newest")]
    _install_db(app, FakeAsyncSession(execute_rows=rows))

    response = client.get("/api/v1/avatars")

    assert response.status_code == 200
    body = response.json()
    assert [a["name"] for a in body["data"]] == ["oldest", "newest"]
    assert body["has_more"] is False


def test_list_avatars_accepts_scope_domain_and_study_filters(client, app, current_user):
    _install_db(app, FakeAsyncSession(execute_rows=[_avatar_row()]))

    response = client.get(
        "/api/v1/avatars",
        params={"scope": "library", "domain_id": str(uuid.uuid4())},
    )

    assert response.status_code == 200


def test_list_avatars_filters_by_study_id_only(client, app, current_user):
    _install_db(app, FakeAsyncSession(execute_rows=[_avatar_row(scope="study")]))

    response = client.get("/api/v1/avatars", params={"study_id": str(uuid.uuid4())})

    assert response.status_code == 200


def test_list_avatars_accepts_next_cursor_for_follow_up_page(client, app, current_user):
    from app.api.pagination import encode_cursor

    row = _avatar_row()
    cursor = encode_cursor(row.created_at, row.id)
    _install_db(app, FakeAsyncSession(execute_rows=[row]))

    response = client.get("/api/v1/avatars", params={"cursor": cursor})

    assert response.status_code == 200


def test_list_avatars_rejects_invalid_scope(client, app, current_user):
    _install_db(app, FakeAsyncSession())

    response = client.get("/api/v1/avatars", params={"scope": "not-a-scope"})

    assert response.status_code == 422


def test_list_avatars_signals_more_pages_with_next_cursor(client, app, current_user):
    rows = [_avatar_row(name=f"avatar-{i}") for i in range(3)]
    _install_db(app, FakeAsyncSession(execute_rows=rows))

    response = client.get("/api/v1/avatars", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 2
    assert body["has_more"] is True
    assert body["next_cursor"] is not None


def test_list_avatars_rejects_invalid_cursor_with_422(client, app, current_user):
    _install_db(app, FakeAsyncSession())

    response = client.get("/api/v1/avatars", params={"cursor": "not-a-valid-cursor"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid pagination cursor."


def test_list_avatars_validates_limit_bounds(client, app, current_user):
    _install_db(app, FakeAsyncSession())

    too_small = client.get("/api/v1/avatars", params={"limit": 0})
    too_large = client.get("/api/v1/avatars", params={"limit": 101})

    assert too_small.status_code == 422
    assert too_large.status_code == 422


# ---------------------------------------------------------------------------
# POST /avatars — scope validation
# ---------------------------------------------------------------------------


def test_create_avatar_library_scope_requires_domain_id(client, app, current_user):
    _install_db(app, FakeAsyncSession())

    response = client.post(
        "/api/v1/avatars",
        json={"name": "Persona", "profile": "Profile text", "scope": "library"},
    )

    assert response.status_code == 422
    assert "domain_id is required" in response.json()["detail"]


def test_create_avatar_library_scope_rejects_unknown_domain(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.post(
        "/api/v1/avatars",
        json={
            "name": "Persona",
            "profile": "Profile text",
            "scope": "library",
            "domain_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 422
    assert "does not exist" in response.json()["detail"]


def test_create_avatar_study_scope_requires_study_id(client, app, current_user):
    _install_db(app, FakeAsyncSession())

    response = client.post(
        "/api/v1/avatars",
        json={"name": "Persona", "profile": "Profile text", "scope": "study"},
    )

    assert response.status_code == 422
    assert "study_id is required" in response.json()["detail"]


def test_create_avatar_study_scope_rejects_unknown_study(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.post(
        "/api/v1/avatars",
        json={
            "name": "Persona",
            "profile": "Profile text",
            "scope": "study",
            "study_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 422
    assert "does not exist" in response.json()["detail"]


def test_create_avatar_defaults_to_library_scope(client, app, current_user):
    domain_id = uuid.uuid4()
    session = FakeAsyncSession(get_result=DomainRow(id=domain_id))
    _install_db(app, session)

    response = client.post(
        "/api/v1/avatars",
        json={"name": "Persona", "profile": "Profile text", "domain_id": str(domain_id)},
    )

    assert response.status_code == 201
    assert response.json()["scope"] == "library"
    assert session.added[0].study_id is None


def test_create_avatar_study_scope_clears_domain_id(client, app, current_user):
    study_id = uuid.uuid4()
    session = FakeAsyncSession(get_result=StudyRow(id=study_id))
    _install_db(app, session)

    response = client.post(
        "/api/v1/avatars",
        json={
            "name": "Persona",
            "profile": "Profile text",
            "scope": "study",
            "study_id": str(study_id),
            "domain_id": str(uuid.uuid4()),  # should be dropped for a study-scoped avatar
        },
    )

    assert response.status_code == 201
    assert session.added[0].domain_id is None
    assert session.added[0].study_id == study_id


def test_create_avatar_defaults_source_to_custom(client, app, current_user):
    domain_id = uuid.uuid4()
    session = FakeAsyncSession(get_result=DomainRow(id=domain_id))
    _install_db(app, session)

    response = client.post(
        "/api/v1/avatars",
        json={"name": "Persona", "profile": "Profile text", "domain_id": str(domain_id)},
    )

    assert response.status_code == 201
    assert response.json()["source"] == "custom"


# ---------------------------------------------------------------------------
# GET / PATCH / DELETE /avatars/{avatar_id}
# ---------------------------------------------------------------------------


def test_get_avatar_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.get(f"/api/v1/avatars/{uuid.uuid4()}")

    assert response.status_code == 404


def test_get_avatar_happy_path(client, app, current_user):
    row = _avatar_row()
    _install_db(app, FakeAsyncSession(get_result=row))

    response = client.get(f"/api/v1/avatars/{row.id}")

    assert response.status_code == 200
    assert response.json()["name"] == row.name


def test_update_avatar_ignores_fields_outside_name_profile(client, app, current_user):
    """The API spec restricts `PATCH /avatars/{id}` to name/profile — the
    schema (`AvatarUpdate`) simply has no other fields, so this pins that
    contract rather than the implementation detail.
    """
    row = _avatar_row()
    _install_db(app, FakeAsyncSession(get_result=row))

    response = client.patch(f"/api/v1/avatars/{row.id}", json={"profile": "Updated profile"})

    assert response.status_code == 200
    assert row.profile == "Updated profile"
    assert row.name == "The Evidence-Driven Specialist"


def test_update_avatar_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.patch(f"/api/v1/avatars/{uuid.uuid4()}", json={"name": "x"})

    assert response.status_code == 404


def test_delete_avatar_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.delete(f"/api/v1/avatars/{uuid.uuid4()}")

    assert response.status_code == 404


def test_delete_avatar_referenced_by_panel_returns_409(client, app, current_user):
    row = _avatar_row()
    session = FakeAsyncSession(
        get_result=row,
        commit_error=IntegrityError("stmt", {}, Exception("fk violation")),
    )
    _install_db(app, session)

    response = client.delete(f"/api/v1/avatars/{row.id}")

    assert response.status_code == 409
    assert session.rollback_count == 1


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------


def test_get_panel_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.get(f"/api/v1/studies/{uuid.uuid4()}/panel")

    assert response.status_code == 404


def test_get_panel_happy_path(client, app, current_user):
    study_id = uuid.uuid4()
    avatar_id = uuid.uuid4()
    _install_db(
        app, FakeAsyncSession(get_result=StudyRow(id=study_id), execute_rows=[avatar_id])
    )

    response = client.get(f"/api/v1/studies/{study_id}/panel")

    assert response.status_code == 200
    assert response.json()["avatar_ids"] == [str(avatar_id)]


def test_set_panel_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.put(
        f"/api/v1/studies/{uuid.uuid4()}/panel", json={"avatar_ids": [str(uuid.uuid4())]}
    )

    assert response.status_code == 404


def test_set_panel_requires_at_least_one_avatar(client, app, current_user):
    study_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=StudyRow(id=study_id)))

    response = client.put(f"/api/v1/studies/{study_id}/panel", json={"avatar_ids": []})

    assert response.status_code == 422


def test_set_panel_rejects_unknown_avatar(client, app, current_user):
    study_id = uuid.uuid4()
    # First `get` resolves the study; second (per avatar_id) misses.
    session = FakeAsyncSession(get_results=[StudyRow(id=study_id), None])
    _install_db(app, session)

    response = client.put(
        f"/api/v1/studies/{study_id}/panel", json={"avatar_ids": [str(uuid.uuid4())]}
    )

    assert response.status_code == 422
    assert "does not exist" in response.json()["detail"]


def test_set_panel_replaces_full_set(client, app, current_user):
    study_id = uuid.uuid4()
    avatar_id = uuid.uuid4()
    session = FakeAsyncSession(get_results=[StudyRow(id=study_id), AvatarRow(id=avatar_id)])
    _install_db(app, session)

    response = client.put(
        f"/api/v1/studies/{study_id}/panel", json={"avatar_ids": [str(avatar_id)]}
    )

    assert response.status_code == 200
    assert response.json()["avatar_ids"] == [str(avatar_id)]
    # One DELETE (clear old panel) + one add (new membership row).
    assert len(session.execute_calls) == 1
    assert len(session.added) == 1
