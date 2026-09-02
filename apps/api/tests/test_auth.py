"""Unit tests for Entra ID JWT handling (app/core/auth.py).

The JWKS client is replaced with a fake that hands back a real RSA
public key, so `validate_token` exercises genuine signature and claim
verification while staying offline.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKClientError

from app.core import auth
from app.core.config import settings


class _FakeSigningKey:
    def __init__(self, key: rsa.RSAPublicKey) -> None:
        self.key = key


class _FakeJWKClient:
    def __init__(self, key: rsa.RSAPublicKey | None, error: Exception | None = None) -> None:
        self._key = key
        self._error = error

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        if self._error is not None:
            raise self._error
        return _FakeSigningKey(self._key)  # type: ignore[arg-type]


@pytest.fixture
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def use_fake_jwks(monkeypatch, private_key):
    """Route validate_token at a JWKS client holding our public key."""
    client = _FakeJWKClient(private_key.public_key())
    monkeypatch.setattr(auth, "_get_jwks_client", lambda: client)
    return private_key


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": "subject-1",
        "oid": str(uuid4()),
        "email": "operator@example.com",
        "name": "Operator",
        "tid": str(uuid4()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return claims


# ---------------------------------------------------------------------------
# validate_token
# ---------------------------------------------------------------------------


def test_validate_token_accepts_valid_rs256_token(use_fake_jwks):
    token = jwt.encode(_claims(), use_fake_jwks, algorithm="RS256")

    payload = auth.validate_token(token)

    assert payload["sub"] == "subject-1"
    assert payload["email"] == "operator@example.com"


@pytest.mark.parametrize(
    ("claim_overrides", "expected_message"),
    [
        pytest.param({"exp": int(time.time()) - 10}, "Token has expired.", id="expired"),
        pytest.param(
            {"aud": "someone-elses-api"},
            "Token audience does not match this API.",
            id="wrong-audience",
        ),
        pytest.param(
            {"iss": "https://evil.example.com/v2.0"},
            "Token issuer is not trusted.",
            id="wrong-issuer",
        ),
    ],
)
def test_validate_token_rejects_bad_claims(use_fake_jwks, claim_overrides, expected_message):
    token = jwt.encode(_claims(**claim_overrides), use_fake_jwks, algorithm="RS256")

    with pytest.raises(auth.TokenError, match=expected_message):
        auth.validate_token(token)


def test_validate_token_requires_standard_claims(use_fake_jwks):
    claims = _claims()
    del claims["iat"]
    token = jwt.encode(claims, use_fake_jwks, algorithm="RS256")

    with pytest.raises(auth.TokenError, match="Token validation failed"):
        auth.validate_token(token)


def test_validate_token_rejects_tampered_signature(use_fake_jwks):
    token = jwt.encode(_claims(), use_fake_jwks, algorithm="RS256")
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-4]}AAAA"

    with pytest.raises(auth.TokenError):
        auth.validate_token(tampered)


def test_validate_token_flags_malformed_tokens(use_fake_jwks):
    with pytest.raises(auth.TokenError, match="Token is malformed"):
        auth.validate_token("this.is-not-a-jwt")


def test_validate_token_reports_unknown_signing_key(monkeypatch):
    monkeypatch.setattr(
        auth,
        "_get_jwks_client",
        lambda: _FakeJWKClient(key=None, error=PyJWKClientError("kid not found")),
    )

    with pytest.raises(auth.TokenError, match="Unable to locate the signing key"):
        auth.validate_token("any-token")


# ---------------------------------------------------------------------------
# JWKS client factory
# ---------------------------------------------------------------------------


def test_get_jwks_client_is_lazy_cached_singleton(monkeypatch):
    monkeypatch.setattr(auth, "_jwks_client", None)

    client = auth._get_jwks_client()

    assert isinstance(client, auth.PyJWKClient)
    assert client.uri == settings.jwks_uri
    assert auth._get_jwks_client() is client  # cached, not rebuilt


# ---------------------------------------------------------------------------
# extract_bearer
# ---------------------------------------------------------------------------


def test_extract_bearer_returns_raw_token():
    assert auth.extract_bearer("Bearer abc.def.ghi") == "abc.def.ghi"


def test_extract_bearer_scheme_is_case_insensitive():
    assert auth.extract_bearer("bearer tok") == "tok"


def test_extract_bearer_missing_header():
    with pytest.raises(auth.TokenError, match="Authorization header is missing"):
        auth.extract_bearer(None)


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", ""])
def test_extract_bearer_rejects_non_bearer_headers(header):
    with pytest.raises(auth.TokenError, match="Bearer scheme|missing"):
        auth.extract_bearer(header)
