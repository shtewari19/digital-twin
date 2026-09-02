"""Unit tests for Settings and its derived properties (app/core/config.py)."""

from __future__ import annotations

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    base: dict = {
        "postgres_user": "core",
        "postgres_password": "secret",
        "postgres_host": "db.internal",
        "postgres_port": 6543,
        "postgres_db": "core_api",
        "temporal_host": "temporal:7233",
        "temporal_namespace": "twin",
        "task_queue": "study-runs",
        "temporal_cors_origins": "http://localhost:3000",
        "entra_tenant_id": "tenant-123",
        "entra_client_id": "client-456",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_async_database_url_builds_asyncpg_dsn():
    settings = _settings()

    assert (
        settings.async_database_url == "postgresql+asyncpg://core:secret@db.internal:6543/core_api"
    )


def test_jwks_uri_points_at_configured_tenant():
    assert (
        _settings().jwks_uri == "https://login.microsoftonline.com/tenant-123/discovery/v2.0/keys"
    )


def test_jwt_issuer_is_the_tenant_v2_endpoint():
    assert _settings().jwt_issuer == "https://login.microsoftonline.com/tenant-123/v2.0"


def test_jwt_audience_defaults_to_client_id():
    assert _settings().jwt_audience == "client-456"


def test_jwt_audience_prefers_explicit_override():
    settings = _settings(entra_audience="api://client-456")

    assert settings.jwt_audience == "api://client-456"
