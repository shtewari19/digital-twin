"""Domain endpoints — the walking skeleton's first vertical slice.

Only `GET /domains` is wired up so far, with real DB-backed keyset
pagination. `POST`/`PATCH`/`DELETE /domains` and the full error-handling
pass (RFC 7807 `Problem` responses) land with the rest of this vertical
in a later milestone.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, tuple_

from app.api.deps import CurrentUser, DbSession
from app.api.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.db.models.domain import Domain as DomainRow
from app.schemas import Domain, DomainList

router = APIRouter(prefix="/domains", tags=["domains"])


@router.get("", response_model=DomainList)
async def list_domains(
    session: DbSession,
    _current_user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> DomainList:
    """`GET /api/v1/domains` — list domains, newest first."""
    stmt = select(DomainRow).order_by(DomainRow.created_at.desc(), DomainRow.id.desc())

    if cursor is not None:
        try:
            after = decode_cursor(cursor)
        except InvalidCursorError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        stmt = stmt.where(
            tuple_(DomainRow.created_at, DomainRow.id) < tuple_(after.created_at, after.id)
        )

    rows = (await session.execute(stmt.limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return DomainList(
        data=[Domain.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )
