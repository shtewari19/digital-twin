"""Unit tests for the get_current_user dependency (app/api/deps.py).

Covers the auth rejection paths (401 + WWW-Authenticate), JIT
provisioning on first login, and the last_login_at refresh on repeat
logins — all against a FakeAsyncSession, with validate_token stubbed.
"""

from __future__ import annotations

import uuid

import pytest
from fakes import FakeAsyncSession
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api import deps
from app.core.auth import TokenError
from app.db.models.user import User


def _credentials(token: str = "token-value") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _validate_token_stub(claims: dict):
    def _stub(token: str) -> dict:
        return claims

    return _stub


# ---------------------------------------------------------------------------
# Rejection paths -> HTTP 401 with WWW-Authenticate: Bearer
# ---------------------------------------------------------------------------


async def test_missing_credentials_yields_401():
    with pytest.raises(HTTPException) as excinfo:
        await deps.get_current_user(session=FakeAsyncSession(), credentials=None)

    assert excinfo.value.status_code == 401
    assert excinfo.value.headers["WWW-Authenticate"] == "Bearer"


async def test_invalid_token_yields_401(monkeypatch):
    def _raise(token: str) -> dict:
        raise TokenError("Token has expired.")

    monkeypatch.setattr(deps, "validate_token", _raise)

    with pytest.raises(HTTPException) as excinfo:
        await deps.get_current_user(session=FakeAsyncSession(), credentials=_credentials())

    assert excinfo.value.status_code == 401


async def test_claims_without_identity_yield_401(monkeypatch):
    monkeypatch.setattr(deps, "validate_token", _validate_token_stub({"sub": None}))

    with pytest.raises(HTTPException) as excinfo:
        await deps.get_current_user(session=FakeAsyncSession(), credentials=_credentials())

    assert excinfo.value.status_code == 401


# ---------------------------------------------------------------------------
# JIT provisioning & login bookkeeping
# ---------------------------------------------------------------------------


async def test_first_login_jit_provisions_operator(monkeypatch):
    oid, tid = str(uuid.uuid4()), str(uuid.uuid4())
    claims = {
        "oid": oid,
        "email": "new.operator@example.com",
        "name": "New Operator",
        "tid": tid,
    }
    monkeypatch.setattr(deps, "validate_token", _validate_token_stub(claims))
    session = FakeAsyncSession(execute_rows=[])  # no existing row

    user = await deps.get_current_user(session=session, credentials=_credentials())

    assert session.added == [user]
    assert session.commit_count == 1
    assert user.auth_provider_id == oid
    assert user.email == "new.operator@example.com"
    assert user.role == "operator"
    assert user.tenant_id == uuid.UUID(tid)
    assert user.last_login_at is not None


async def test_existing_user_login_refreshes_last_login(monkeypatch):
    oid = str(uuid.uuid4())
    claims = {"oid": oid, "email": "returning@example.com"}
    monkeypatch.setattr(deps, "validate_token", _validate_token_stub(claims))
    existing = User(
        auth_provider_id=oid,
        email="returning@example.com",
        name="Returning Operator",
        role="admin",
    )
    session = FakeAsyncSession(execute_rows=[existing])

    user = await deps.get_current_user(session=session, credentials=_credentials())

    assert user is existing
    assert user.last_login_at is not None
    assert session.commit_count == 1
    assert session.added == []


async def test_sub_claim_used_when_oid_absent_and_upn_for_email(monkeypatch):
    sub = str(uuid.uuid4())
    claims = {"sub": sub, "upn": "user@corp.example"}  # no oid / email / name
    monkeypatch.setattr(deps, "validate_token", _validate_token_stub(claims))
    session = FakeAsyncSession(execute_rows=[])

    user = await deps.get_current_user(session=session, credentials=_credentials())

    assert user.auth_provider_id == sub
    assert user.email == "user@corp.example"
    assert user.name == "Unknown"
