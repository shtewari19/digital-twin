"""Domain endpoints.

The full-error-handling pass (RFC 7807 `Problem` responses across every
route, not just the ad hoc `HTTPException`s below) is still outstanding.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.api.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.db.models.domain import Domain as DomainRow
from app.schemas import Domain, DomainCreate, DomainList, DomainType, DomainUpdate

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


@router.post("", response_model=Domain, status_code=status.HTTP_201_CREATED)
async def create_domain(
    body: DomainCreate, session: DbSession, _current_user: CurrentUser
) -> Domain:
    """`POST /api/v1/domains` — always creates a custom (not predefined) domain."""
    row = DomainRow(
        name=body.name,
        type=DomainType.CUSTOM.value,
        description=body.description,
        compliance_profile=body.compliance_profile,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return Domain.model_validate(row)


@router.get("/{domain_id}", response_model=Domain)
async def get_domain(
    domain_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> Domain:
    """`GET /api/v1/domains/{domain_id}`."""
    row = await session.get(DomainRow, domain_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found.")
    return Domain.model_validate(row)


@router.patch("/{domain_id}", response_model=Domain)
async def update_domain(
    domain_id: uuid.UUID,
    body: DomainUpdate,
    session: DbSession,
    _current_user: CurrentUser,
) -> Domain:
    """`PATCH /api/v1/domains/{domain_id}`."""
    row = await session.get(DomainRow, domain_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return Domain.model_validate(row)


@router.delete("/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_domain(
    domain_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> None:
    """`DELETE /api/v1/domains/{domain_id}`."""
    row = await session.get(DomainRow, domain_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found.")
    await session.delete(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Domain is referenced by existing studies or avatars.",
        ) from exc
