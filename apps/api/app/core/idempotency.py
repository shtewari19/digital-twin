"""At-most-once handling for the `Idempotency-Key` header.

Every state-changing run endpoint in the contract accepts an optional
client-generated `Idempotency-Key` so a retried POST (a dropped response,
a double-clicked button, an SDK retry) is applied once rather than
creating a second run or firing a second signal.

How it works
------------
`platform.idempotency_keys` holds one row per (key, endpoint, user). The
primary key is what enforces the guarantee — `claim()` does an
`INSERT ... ON CONFLICT DO NOTHING` and reports whether *this* request won
the race:

  * won  -> the caller performs the real work, then calls `record()` with
            the resource id and status code it returned.
  * lost -> the caller replays: `claim()` hands back the stored response so
            the retry sees the original result instead of a 409.

A replay whose stored row hasn't been completed yet (the first request is
still in flight) raises a 409 rather than duplicating the work — the client
should retry once the first call returns.

Endpoints are keyed by name (`"create_run"`, `"start_run"`, ...) so the same
key reused against a *different* endpoint is treated as a separate claim,
matching the contract's per-operation scope.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.problem import TYPE_IDEMPOTENCY_CONFLICT, ProblemError


@dataclass(frozen=True)
class Replay:
    """The stored outcome of a previously completed identical request."""

    resource_id: uuid.UUID
    status_code: int


_CLAIM = text(
    """
    INSERT INTO platform.idempotency_keys (key, endpoint, user_id)
    VALUES (:key, :endpoint, :user_id)
    ON CONFLICT (key, endpoint, user_id) DO NOTHING
    RETURNING key
    """
)

_LOOKUP = text(
    """
    SELECT resource_id, status_code
      FROM platform.idempotency_keys
     WHERE key = :key AND endpoint = :endpoint AND user_id = :user_id
    """
)

_RECORD = text(
    """
    UPDATE platform.idempotency_keys
       SET resource_id = :resource_id, status_code = :status_code, completed_at = now()
     WHERE key = :key AND endpoint = :endpoint AND user_id = :user_id
    """
)


async def claim(
    session: AsyncSession,
    key: str | None,
    endpoint: str,
    user_id: uuid.UUID,
) -> Replay | None:
    """Claim `key` for `endpoint`.

    Returns `None` when the caller should do the work (no key supplied, or
    this request won the claim), or a `Replay` when an identical request
    already completed.

    Raises:
        ProblemError: 409, if an identical request is still in flight.
    """
    if not key:
        return None

    params = {"key": key, "endpoint": endpoint, "user_id": user_id}
    won = (await session.execute(_CLAIM, params)).first() is not None
    await session.commit()
    if won:
        return None

    row = (await session.execute(_LOOKUP, params)).first()
    if row is None or row.resource_id is None:
        raise ProblemError(
            409,
            "A request with this Idempotency-Key is still in progress; retry shortly.",
            type_=TYPE_IDEMPOTENCY_CONFLICT,
        )
    return Replay(resource_id=row.resource_id, status_code=row.status_code)


async def record(
    session: AsyncSession,
    key: str | None,
    endpoint: str,
    user_id: uuid.UUID,
    resource_id: uuid.UUID,
    status_code: int,
) -> None:
    """Mark the claim complete so later retries replay this outcome."""
    if not key:
        return
    await session.execute(
        _RECORD,
        {
            "key": key,
            "endpoint": endpoint,
            "user_id": user_id,
            "resource_id": resource_id,
            "status_code": status_code,
        },
    )
    await session.commit()
