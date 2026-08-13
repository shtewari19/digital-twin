"""GET /api/v1/me — return the authenticated user's profile.

Acceptance criteria (issue #16):
  - Returns the current user's id, name, email, role.
  - Returns HTTP 401 (Problem JSON) when credentials are missing or invalid.
  - The user row is JIT-provisioned by `get_current_user` (see deps.py) before
    this handler is ever reached, so this route only needs to serialize.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.schemas.platform import User as UserSchema

router = APIRouter(tags=["me"])


@router.get(
    "/me",
    response_model=UserSchema,
    summary="Get the current authenticated user",
    responses={
        401: {"description": "Missing or invalid credentials."},
    },
)
async def get_me(current_user: CurrentUser) -> UserSchema:
    """Return the profile of the JWT-authenticated user (operationId: getMe).

    The row is created automatically on first login (JIT-provisioned from
    the Entra ID JWT claims) — no pre-registration required.
    """
    return UserSchema.model_validate(current_user)
