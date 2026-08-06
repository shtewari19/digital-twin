"""Shared FastAPI dependencies: the DB session and the current-user stub.

`get_current_user` stands in for step 3 of the walking-skeleton plan
(real Entra ID JWT validation). It always resolves to the single seeded
dev user (`Settings.dev_user_id`, seeded by `scripts/seed_dev_data.py`)
so every route can already depend on a real `User` row — swapping in JWT
validation later only changes this function's body, not its callers.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.user import User
from app.db.session import get_db

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(session: DbSession) -> User:
    """Return the seeded dev user in place of a real authenticated identity."""
    user = await session.get(User, settings.dev_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Dev user {settings.dev_user_id} is not seeded. "
                "Run `python scripts/seed_dev_data.py`."
            ),
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
