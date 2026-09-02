"""Microsoft Entra ID (Azure AD) JWT validation.

Validates the Bearer token on every protected request:
  1. Fetches (and caches) the tenant's public-key set from the JWKS endpoint.
  2. Decodes the token, verifying RS256 signature, issuer, audience, and expiry.
  3. Returns the raw claims dict; callers extract the identity they need.

This module has no FastAPI dependency so it can be reused by apps/engine later.
"""

from __future__ import annotations

import logging
from typing import Any

import jwt
from jwt import PyJWKClient, PyJWKClientError

from app.core.config import settings

logger = logging.getLogger(__name__)

# PyJWT's PyJWKClient caches keys and refreshes them on its own schedule.
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    """Return (or lazily create) the module-level JWKS client."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(
            settings.jwks_uri,
            cache_keys=True,
            lifespan=3600,  # refresh keys after 1 h
        )
    return _jwks_client


class TokenError(Exception):
    """Raised when a JWT is missing, malformed, expired, or has wrong claims."""


def validate_token(token: str) -> dict[str, Any]:
    """Validate a raw Bearer token and return its claims.

    Raises
    ------
    TokenError
        On any validation failure — callers convert this to HTTP 401.
    """
    client = _get_jwks_client()

    try:
        signing_key = client.get_signing_key_from_jwt(token)
    except PyJWKClientError as exc:
        logger.warning("JWKS key lookup failed: %s", exc)
        raise TokenError("Unable to locate the signing key for this token.") from exc

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired.") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenError("Token audience does not match this API.") from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenError("Token issuer is not trusted.") from exc
    except jwt.DecodeError as exc:
        raise TokenError(f"Token is malformed: {exc}") from exc
    except jwt.PyJWTError as exc:
        raise TokenError(f"Token validation failed: {exc}") from exc

    logger.debug(
        "JWT validated for sub=%s oid=%s",
        payload.get("sub"),
        payload.get("oid"),
    )
    return payload


def extract_bearer(authorization: str | None) -> str:
    """Parse the Authorization header and return the raw JWT string.

    Raises
    ------
    TokenError
        If the header is absent or not a Bearer scheme.
    """
    if not authorization:
        raise TokenError("Authorization header is missing.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise TokenError("Authorization header must use the Bearer scheme.")
    return token
