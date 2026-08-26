"""Unit tests for the Temporal client holder (app/core/temporal.py)."""

from __future__ import annotations

import pytest

from app.core import temporal


class _FakeClient:
    pass


def test_get_temporal_client_raises_before_initialization(monkeypatch):
    monkeypatch.setattr(temporal, "_client", None)

    with pytest.raises(RuntimeError, match="not initialized"):
        temporal.get_temporal_client()


def test_get_temporal_client_returns_initialized_client(monkeypatch):
    sentinel = _FakeClient()
    monkeypatch.setattr(temporal, "_client", sentinel)

    assert temporal.get_temporal_client() is sentinel


def test_workflow_name_matches_engine_contract():
    # apps/engine registers its @workflow.defn under this exact name.
    assert temporal.STUDY_RUN_WORKFLOW_NAME == "study_run_workflow"


async def test_init_temporal_client_stores_singleton(monkeypatch):
    captured: dict = {}

    class _ConnectableClient(_FakeClient):
        @classmethod
        async def connect(cls, host: str, namespace: str | None = None):
            captured["host"] = host
            captured["namespace"] = namespace
            return cls()

    monkeypatch.setattr(temporal, "Client", _ConnectableClient)

    client = await temporal.init_temporal_client()

    assert isinstance(client, _ConnectableClient)
    assert captured["host"] == "localhost:7233"
    assert captured["namespace"] == "default"
    assert temporal.get_temporal_client() is client
