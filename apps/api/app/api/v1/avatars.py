"""Avatar endpoints, plus the per-study panel sub-resource (`core.study_avatars`)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, select, tuple_
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.api.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.db.models.avatar import Avatar as AvatarRow
from app.db.models.avatar import StudyAvatar
from app.db.models.domain import Domain as DomainRow
from app.db.models.study import Study as StudyRow
from app.schemas import (
    Avatar,
    AvatarCreate,
    AvatarList,
    AvatarScope,
    AvatarUpdate,
    Panel,
    PanelUpdate,
)

router = APIRouter(tags=["avatars"])


@router.get("/avatars", response_model=AvatarList)
async def list_avatars(
    session: DbSession,
    _current_user: CurrentUser,
    scope: AvatarScope | None = Query(default=None),
    domain_id: uuid.UUID | None = Query(default=None),
    study_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> AvatarList:
    """`GET /api/v1/avatars`."""
    stmt = select(AvatarRow).order_by(AvatarRow.created_at.desc(), AvatarRow.id.desc())
    if scope is not None:
        stmt = stmt.where(AvatarRow.scope == scope.value)
    if domain_id is not None:
        stmt = stmt.where(AvatarRow.domain_id == domain_id)
    if study_id is not None:
        stmt = stmt.where(AvatarRow.study_id == study_id)

    if cursor is not None:
        try:
            after = decode_cursor(cursor)
        except InvalidCursorError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        stmt = stmt.where(
            tuple_(AvatarRow.created_at, AvatarRow.id) < tuple_(after.created_at, after.id)
        )

    rows = (await session.execute(stmt.limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return AvatarList(
        data=[Avatar.model_validate(r) for r in rows], next_cursor=next_cursor, has_more=has_more
    )


@router.post("/avatars", response_model=Avatar, status_code=status.HTTP_201_CREATED)
async def create_avatar(
    body: AvatarCreate, session: DbSession, _current_user: CurrentUser
) -> Avatar:
    """`POST /api/v1/avatars`.

    Mirrors the DB's `ck_avatar_scope` CHECK: a `library`-scoped avatar
    needs `domain_id` (and no `study_id`); a `study`-scoped one needs
    `study_id`. Validated here so a bad combination is a 422, not a raw
    CHECK-constraint failure surfaced as a 500.
    """
    scope = body.scope or AvatarScope.LIBRARY

    if scope == AvatarScope.LIBRARY:
        if body.domain_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="domain_id is required for a library-scoped avatar.",
            )
        if await session.get(DomainRow, body.domain_id) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Domain {body.domain_id} does not exist.",
            )
    else:
        if body.study_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="study_id is required for a study-scoped avatar.",
            )
        if await session.get(StudyRow, body.study_id) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Study {body.study_id} does not exist.",
            )

    row = AvatarRow(
        scope=scope.value,
        domain_id=body.domain_id if scope == AvatarScope.LIBRARY else None,
        study_id=body.study_id if scope == AvatarScope.STUDY else None,
        name=body.name,
        profile=body.profile,
        source=body.source.value if body.source else "custom",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return Avatar.model_validate(row)


@router.get("/avatars/{avatar_id}", response_model=Avatar)
async def get_avatar(
    avatar_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> Avatar:
    """`GET /api/v1/avatars/{avatar_id}`."""
    row = await session.get(AvatarRow, avatar_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found.")
    return Avatar.model_validate(row)


@router.patch("/avatars/{avatar_id}", response_model=Avatar)
async def update_avatar(
    avatar_id: uuid.UUID,
    body: AvatarUpdate,
    session: DbSession,
    _current_user: CurrentUser,
) -> Avatar:
    """`PATCH /api/v1/avatars/{avatar_id}` — name/profile only, per the API spec."""
    row = await session.get(AvatarRow, avatar_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return Avatar.model_validate(row)


@router.delete("/avatars/{avatar_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_avatar(
    avatar_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> None:
    """`DELETE /api/v1/avatars/{avatar_id}`."""
    row = await session.get(AvatarRow, avatar_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found.")
    await session.delete(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Avatar is referenced by an existing study panel.",
        ) from exc


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------


@router.get("/studies/{study_id}/panel", response_model=Panel)
async def get_panel(
    study_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> Panel:
    """`GET /api/v1/studies/{study_id}/panel`."""
    if await session.get(StudyRow, study_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found.")
    avatar_ids = (
        (await session.execute(select(StudyAvatar.avatar_id).where(StudyAvatar.study_id == study_id)))
        .scalars()
        .all()
    )
    return Panel(study_id=study_id, avatar_ids=list(avatar_ids))


@router.put("/studies/{study_id}/panel", response_model=Panel)
async def set_panel(
    study_id: uuid.UUID,
    body: PanelUpdate,
    session: DbSession,
    _current_user: CurrentUser,
) -> Panel:
    """`PUT /api/v1/studies/{study_id}/panel` — replaces the full panel."""
    if await session.get(StudyRow, study_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found.")

    for avatar_id in body.avatar_ids:
        if await session.get(AvatarRow, avatar_id) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Avatar {avatar_id} does not exist.",
            )

    await session.execute(delete(StudyAvatar).where(StudyAvatar.study_id == study_id))
    for avatar_id in body.avatar_ids:
        session.add(StudyAvatar(study_id=study_id, avatar_id=avatar_id))
    await session.commit()
    return Panel(study_id=study_id, avatar_ids=list(body.avatar_ids))
