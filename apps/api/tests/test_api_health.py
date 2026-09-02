"""Smoke tests for the app entrypoint (app/main.py)."""

from __future__ import annotations


def test_health_liveness_probe(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_startup_hook_is_safe_without_temporal(client):
    # Entering the lifespan fires the startup hook; the autouse conftest
    # stub keeps it from reaching a real Temporal server.
    with client:
        pass

    assert client.get("/health").status_code == 200
