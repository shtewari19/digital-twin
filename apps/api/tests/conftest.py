"""Pytest configuration for the Core API test suite.

`Settings()` is instantiated the moment `app.core.config` is imported, so
the `APP_` environment variables must exist BEFORE any `app.*` import —
that is why this module sets the environment first and imports the
application second (E402 is suppressed for this file in pyproject.toml).

The suite is hermetic: no Postgres, Temporal, or Entra ID access.
Database access goes through `FakeAsyncSession` (tests/fakes.py), the
Temporal client is swapped for an in-memory fake, and JWT validation
either gets monkeypatched or exercises real RS256 verification against a
throwaway key pair.
"""

from __future__ import annotations

import os
from uuid import uuid4

_TEST_ENV = {
    "APP_POSTGRES_USER": "postgres",
    "APP_POSTGRES_PASSWORD": "postgres",
    "APP_POSTGRES_HOST": "localhost",
    "APP_POSTGRES_PORT": "5432",
    "APP_POSTGRES_DB": "core_api_test",
    "APP_TEMPORAL_HOST": "localhost:7233",
    "APP_TEMPORAL_NAMESPACE": "default",
    "APP_TASK_QUEUE": "study-runs-test",
    "APP_TEMPORAL_CORS_ORIGINS": "http://localhost:3000",
    "APP_ENTRA_TENANT_ID": "11111111-2222-3333-4444-555555555555",
    "APP_ENTRA_CLIENT_ID": "test-client-id",
}
for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.models.user import User


@pytest.fixture()
def app():
    """The FastAPI application under test."""
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture()
def client(app):
    """Synchronous TestClient.

    Deliberately NOT used as a context manager: entering the lifespan
    would fire the startup hook that connects to Temporal.
    """
    return TestClient(app)


@pytest.fixture(autouse=True)
def _never_touch_real_services(app, monkeypatch):
    """Safety net: stub Temporal startup; wipe overrides between tests."""

    async def _noop() -> None:
        return None

    monkeypatch.setattr("app.main.init_temporal_client", _noop)
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def current_user(app):
    """Stub authentication: protected routes see this JIT-style User row."""
    user = User(
        id=uuid4(),
        tenant_id=None,
        auth_provider_id="entra-oid",
        email="operator@example.com",
        name="Test Operator",
        role="operator",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    return user
