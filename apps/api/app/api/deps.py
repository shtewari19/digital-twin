"""Shared FastAPI dependencies: DB session and the current authenticated user.

Authentication flow
-------------------
Every protected route declares `CurrentUser` (or `CurrentUserOptional`) as a
dependency.  FastAPI calls `get_current_user`, which:

  1. Extracts the Bearer token from the Authorization header.
  2. Validates the token's RS256 signature against Entra ID's JWKS endpoint,
     and checks iss / aud / exp claims (see `app.core.auth`).
  3. Looks up the user row in `core.users` by `auth_provider_id` (= the JWT's
     `oid` claim, which is stable across token refreshes).
  4. If no row exists yet (first login), JIT-provisions one from the JWT claims
     and commits it — no passwords are ever stored.
  5. Updates `last_login_at` on each successful authentication.

Swapping in a different OIDC provider later only changes this file.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenError, extract_bearer, validate_token
from app.db.models.user import User
from app.db.session import get_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB session
# ---------------------------------------------------------------------------

DbSession = Annotated[AsyncSession, Depends(get_db)]

# Declared so Swagger UI shows the green Authorize button (Bearer JWT).
_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Current-user resolution
# ---------------------------------------------------------------------------


async def get_current_user(
    session: DbSession,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ] = None,
) -> User:
    """Validate the Bearer JWT and return the (JIT-provisioned) User row.

    Raises HTTP 401 on any auth failure with a WWW-Authenticate header.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    authorization = (
        f"{credentials.scheme} {credentials.credentials}" if credentials else None
    )

    # 1. Extract token from header
    try:
        token = extract_bearer(authorization)
    except TokenError as exc:
        logger.info("Auth rejected (no/bad header): %s", exc)
        raise credentials_exception from exc

    # 2. Validate signature + claims
    try:
        claims = validate_token(token)
    except TokenError as exc:
        logger.info("Auth rejected (invalid token): %s", exc)
        raise credentials_exception from exc

    # 3. Extract identity fields from JWT claims
    # `oid` is the stable Entra object ID (auth_provider_id in our schema).
    oid: str | None = claims.get("oid") or claims.get("sub")
    email: str | None = (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("upn")
    )
    name: str = claims.get("name") or claims.get("preferred_username") or "Unknown"
    tenant_id_str: str | None = claims.get("tid")

    if not oid or not email:
        logger.warning("JWT missing oid or email claim: %s", claims.keys())
        raise credentials_exception

    # 4. Look up existing user by Entra oid
    result = await session.execute(
        select(User).where(User.auth_provider_id == oid)
    )
    user: User | None = result.scalar_one_or_none()

    now = datetime.now(tz=UTC)

    if user is None:
        # 5. JIT-provision: first login → create the user row
        logger.info("JIT-provisioning new user oid=%s email=%s", oid, email)
        tenant_id = uuid.UUID(tenant_id_str) if tenant_id_str else None
        user = User(
            tenant_id=tenant_id,
            auth_provider_id=oid,
            email=email,
            name=name,
            role="operator",
            last_login_at=now,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    else:
        # Update last_login_at on every authenticated request
        user.last_login_at = now
        await session.commit()

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
