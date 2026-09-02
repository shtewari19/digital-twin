"""Contract tests for study endpoints and the outcome/anchor sub-resources
(app/api/v1/studies.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fakes import FakeAsyncSession

from app.api.deps import get_db
from app.db.models.anchor import Anchor as AnchorRow
from app.db.models.domain import Domain as DomainRow
from app.db.models.study import Study as StudyRow


def _install_db(app, session: FakeAsyncSession) -> None:
    app.dependency_overrides[get_db] = lambda: session


def _study_row(**overrides) -> StudyRow:
    now = datetime.now(tz=UTC)
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": None,
        "domain_id": uuid.uuid4(),
        "owner_id": uuid.uuid4(),
        "name": "Q3 lead-claim test",
        "description": "Test which claim wins",
        "intent": None,
        "outcome_dimension": None,
        "scale_min": 1,
        "scale_max": 5,
        "status": "draft",
        "expires_at": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return StudyRow(**defaults)


# ---------------------------------------------------------------------------
# GET /studies
# ---------------------------------------------------------------------------


def test_list_studies_returns_rows_in_query_order(client, app, current_user):
    rows = [_study_row(name="oldest"), _study_row(name="newest")]
    _install_db(app, FakeAsyncSession(execute_rows=rows))

    response = client.get("/api/v1/studies")

    assert response.status_code == 200
    body = response.json()
    assert [s["name"] for s in body["data"]] == ["oldest", "newest"]
    assert body["has_more"] is False


def test_list_studies_filters_by_domain_and_status(client, app, current_user):
    """The fake session doesn't execute real SQL, so this only pins that
    the query params are accepted and 200s — not that filtering actually
    narrows the result set (see the module-level pagination tests in
    test_api_domains.py for why that's out of scope for a fake session).
    """
    domain_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(execute_rows=[_study_row(domain_id=domain_id)]))

    response = client.get(
        "/api/v1/studies", params={"domain_id": str(domain_id), "status": "ready"}
    )

    assert response.status_code == 200


def test_list_studies_rejects_invalid_status_filter(client, app, current_user):
    _install_db(app, FakeAsyncSession())

    response = client.get("/api/v1/studies", params={"status": "not-a-status"})

    assert response.status_code == 422


def test_list_studies_signals_more_pages_with_next_cursor(client, app, current_user):
    rows = [_study_row(name=f"study-{i}") for i in range(3)]
    _install_db(app, FakeAsyncSession(execute_rows=rows))

    response = client.get("/api/v1/studies", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 2
    assert body["has_more"] is True
    assert body["next_cursor"] is not None


def test_list_studies_accepts_next_cursor_for_follow_up_page(client, app, current_user):
    from app.api.pagination import encode_cursor

    row = _study_row()
    cursor = encode_cursor(row.created_at, row.id)
    _install_db(app, FakeAsyncSession(execute_rows=[row]))

    response = client.get("/api/v1/studies", params={"cursor": cursor})

    assert response.status_code == 200


def test_list_studies_rejects_invalid_cursor_with_422(client, app, current_user):
    _install_db(app, FakeAsyncSession())

    response = client.get("/api/v1/studies", params={"cursor": "not-a-valid-cursor"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid pagination cursor."


def test_list_studies_validates_limit_bounds(client, app, current_user):
    _install_db(app, FakeAsyncSession())

    too_small = client.get("/api/v1/studies", params={"limit": 0})
    too_large = client.get("/api/v1/studies", params={"limit": 101})

    assert too_small.status_code == 422
    assert too_large.status_code == 422


# ---------------------------------------------------------------------------
# POST /studies
# ---------------------------------------------------------------------------


def test_create_study_rejects_unknown_domain(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.post(
        "/api/v1/studies",
        json={"domain_id": str(uuid.uuid4()), "description": "desc"},
    )

    assert response.status_code == 422
    assert "does not exist" in response.json()["detail"]


def test_create_study_requires_description(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=DomainRow(id=uuid.uuid4())))

    response = client.post("/api/v1/studies", json={"domain_id": str(uuid.uuid4())})

    assert response.status_code == 422


def test_create_study_persists_intent_and_owner(client, app, current_user):
    domain_id = uuid.uuid4()
    session = FakeAsyncSession(get_result=DomainRow(id=domain_id))
    _install_db(app, session)

    response = client.post(
        "/api/v1/studies",
        json={
            "domain_id": str(domain_id),
            "name": "Q3 lead-claim test",
            "description": "Test which claim wins",
            "intent": {"audience": "Cardiology PCPs", "product": "Drug X"},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["intent"] == {
        "audience": "Cardiology PCPs",
        "product": "Drug X",
        "decision": None,
        "success_criteria": None,
    }
    assert len(session.added) == 1
    assert session.added[0].owner_id == current_user.id
    assert session.commit_count == 1


def test_create_study_omitting_name_currently_raises_instead_of_422(client, app, current_user):
    """Known gap, pinned rather than hidden: `StudyCreate.name` is typed
    optional ("a suggestion is generated if omitted"), but no such
    auto-suggestion is wired up in `create_study`, and `core.studies.name`
    is NOT NULL. A real Postgres would reject the INSERT; here, the fake
    session has no NOT NULL enforcement, so the failure instead surfaces
    one line later — `Study.model_validate(row)` rejecting `name=None` —
    but either way, omitting `name` blows up unhandled rather than
    returning a clean 422. Flip this test once `create_study` requires
    `name` (422) or actually generates one.
    """
    from pydantic import ValidationError

    domain_id = uuid.uuid4()
    _install_db(app, FakeAsyncSession(get_result=DomainRow(id=domain_id)))

    with pytest.raises(ValidationError, match="name"):
        client.post("/api/v1/studies", json={"domain_id": str(domain_id), "description": "desc"})


# ---------------------------------------------------------------------------
# GET / PATCH / DELETE /studies/{study_id}
# ---------------------------------------------------------------------------


def test_get_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.get(f"/api/v1/studies/{uuid.uuid4()}")

    assert response.status_code == 404


def test_update_study_only_touches_provided_fields(client, app, current_user):
    row = _study_row()
    _install_db(app, FakeAsyncSession(get_result=row))

    response = client.patch(f"/api/v1/studies/{row.id}", json={"name": "New name"})

    assert response.status_code == 200
    assert row.name == "New name"
    assert row.description == "Test which claim wins"  # untouched


def test_update_study_status_transition_serializes_enum_value(client, app, current_user):
    row = _study_row()
    _install_db(app, FakeAsyncSession(get_result=row))

    response = client.patch(f"/api/v1/studies/{row.id}", json={"status": "ready"})

    assert response.status_code == 200
    assert row.status == "ready"
    assert response.json()["status"] == "ready"


def test_update_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.patch(f"/api/v1/studies/{uuid.uuid4()}", json={"name": "x"})

    assert response.status_code == 404


def test_update_study_rejects_invalid_status(client, app, current_user):
    row = _study_row()
    _install_db(app, FakeAsyncSession(get_result=row))

    response = client.patch(f"/api/v1/studies/{row.id}", json={"status": "not-a-status"})

    assert response.status_code == 422


def test_delete_study_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.delete(f"/api/v1/studies/{uuid.uuid4()}")

    assert response.status_code == 404


def test_delete_study_happy_path_deletes_scoped_anchors_first(client, app, current_user):
    row = _study_row()
    session = FakeAsyncSession(get_result=row)
    _install_db(app, session)

    response = client.delete(f"/api/v1/studies/{row.id}")

    assert response.status_code == 204
    # Two DELETE statements: anchors, then the study itself.
    assert len(session.execute_calls) == 2
    assert session.commit_count == 1


def test_delete_study_with_dependents_returns_409(client, app, current_user):
    from sqlalchemy.exc import IntegrityError

    row = _study_row()
    session = FakeAsyncSession(
        get_result=row, commit_error=IntegrityError("stmt", {}, Exception("fk violation"))
    )
    _install_db(app, session)

    response = client.delete(f"/api/v1/studies/{row.id}")

    assert response.status_code == 409
    assert session.rollback_count == 1


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


def test_get_outcome_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.get(f"/api/v1/studies/{uuid.uuid4()}/outcome")

    assert response.status_code == 404


def test_get_outcome_defaults_when_never_set(client, app, current_user):
    row = _study_row(outcome_dimension=None, scale_min=None, scale_max=None)
    _install_db(app, FakeAsyncSession(get_result=row))

    response = client.get(f"/api/v1/studies/{row.id}/outcome")

    assert response.status_code == 200
    body = response.json()
    assert body["dimension"] == ""
    assert body["scale"] == {"min": 1, "max": 5}


def test_set_outcome_persists_dimension_and_scale(client, app, current_user):
    row = _study_row()
    session = FakeAsyncSession(get_result=row)
    _install_db(app, session)

    response = client.put(
        f"/api/v1/studies/{row.id}/outcome",
        json={"dimension": "prescribing intent", "scale": {"min": 1, "max": 7}},
    )

    assert response.status_code == 200
    assert row.outcome_dimension == "prescribing intent"
    assert row.scale_min == 1
    assert row.scale_max == 7
    assert session.commit_count == 1


def test_set_outcome_rejects_missing_scale(client, app, current_user):
    row = _study_row()
    _install_db(app, FakeAsyncSession(get_result=row))

    response = client.put(
        f"/api/v1/studies/{row.id}/outcome", json={"dimension": "prescribing intent"}
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------


def _anchor_row(scale_point: int, text: str, study_id: uuid.UUID) -> AnchorRow:
    return AnchorRow(
        id=uuid.uuid4(),
        scope_type="study",
        scope_id=study_id,
        scale_point=scale_point,
        text=text,
    )


def test_get_anchors_not_found_returns_404(client, app, current_user):
    _install_db(app, FakeAsyncSession(get_result=None))

    response = client.get(f"/api/v1/studies/{uuid.uuid4()}/anchors")

    assert response.status_code == 404


def test_get_anchors_empty_when_none_set(client, app, current_user):
    row = _study_row()
    _install_db(app, FakeAsyncSession(get_result=row, execute_rows=[]))

    response = client.get(f"/api/v1/studies/{row.id}/anchors")

    assert response.status_code == 200
    assert response.json()["anchors"] == []


def test_get_anchors_returns_existing_set(client, app, current_user):
    row = _study_row()
    anchors = [_anchor_row(1, "no", row.id), _anchor_row(5, "yes", row.id)]
    _install_db(app, FakeAsyncSession(get_result=row, execute_rows=anchors))

    response = client.get(f"/api/v1/studies/{row.id}/anchors")

    assert response.status_code == 200
    body = response.json()
    assert [(a["scale_point"], a["text"]) for a in body["anchors"]] == [(1, "no"), (5, "yes")]


def test_set_anchors_replaces_full_set(client, app, current_user):
    row = _study_row()
    session = FakeAsyncSession(get_result=row)
    _install_db(app, session)

    response = client.put(
        f"/api/v1/studies/{row.id}/anchors",
        json={"anchors": [{"scale_point": 1, "text": "no"}, {"scale_point": 5, "text": "yes"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert [a["scale_point"] for a in body["anchors"]] == [1, 5]
    # One DELETE (clear old set) + two adds (new anchors).
    assert len(session.execute_calls) == 1
    assert len(session.added) == 2


def test_set_anchors_rejects_empty_list_body_shape(client, app, current_user):
    row = _study_row()
    _install_db(app, FakeAsyncSession(get_result=row))

    # Missing the required `anchors` key entirely.
    response = client.put(f"/api/v1/studies/{row.id}/anchors", json={})

    assert response.status_code == 422
