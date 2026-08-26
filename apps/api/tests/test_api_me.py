"""Contract tests for GET /api/v1/me."""

from __future__ import annotations

from fakes import FakeAsyncSession

from app.api.deps import get_db


def test_me_returns_profile_of_current_user(client, current_user):
    response = client.get("/api/v1/me")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(current_user.id)
    assert body["name"] == "Test Operator"
    assert body["email"] == "operator@example.com"
    assert body["role"] == "operator"


def test_me_requires_authentication(client, app):
    app.dependency_overrides[get_db] = lambda: FakeAsyncSession()

    response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["detail"] == "Missing or invalid credentials."
