"""Study endpoints, plus the outcome/anchor sub-resources scoped to one study.

`outcome` isn't a separate table — `dimension`/`scale` live as columns
directly on `core.studies` (see setup.sql) — so its handlers read/write
`Study` row fields rather than a second model.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, select, tuple_
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.api.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.db.models.anchor import Anchor as AnchorRow
from app.db.models.domain import Domain as DomainRow
from app.db.models.study import Study as StudyRow
from app.schemas import (
    Anchor,
    AnchorSet,
    AnchorSetUpdate,
    Outcome,
    OutcomeUpdate,
    Scale,
    Study,
    StudyCreate,
    StudyList,
    StudyStatus,
    StudyUpdate,
)

router = APIRouter(tags=["studies"])


async def _get_study_or_404(session: DbSession, study_id: uuid.UUID) -> StudyRow:
    row = await session.get(StudyRow, study_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found.")
    return row


@router.get("/studies", response_model=StudyList)
async def list_studies(
    session: DbSession,
    _current_user: CurrentUser,
    domain_id: uuid.UUID | None = Query(default=None),
    status_filter: StudyStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> StudyList:
    """`GET /api/v1/studies`."""
    stmt = select(StudyRow).order_by(StudyRow.created_at.desc(), StudyRow.id.desc())
    if domain_id is not None:
        stmt = stmt.where(StudyRow.domain_id == domain_id)
    if status_filter is not None:
        stmt = stmt.where(StudyRow.status == status_filter.value)

    if cursor is not None:
        try:
            after = decode_cursor(cursor)
        except InvalidCursorError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        stmt = stmt.where(
            tuple_(StudyRow.created_at, StudyRow.id) < tuple_(after.created_at, after.id)
        )

    rows = (await session.execute(stmt.limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return StudyList(
        data=[Study.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post("/studies", response_model=Study, status_code=status.HTTP_201_CREATED)
async def create_study(
    body: StudyCreate, session: DbSession, current_user: CurrentUser
) -> Study:
    """`POST /api/v1/studies`."""
    if await session.get(DomainRow, body.domain_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Domain {body.domain_id} does not exist.",
        )

    row = StudyRow(
        domain_id=body.domain_id,
        owner_id=current_user.id,
        name=body.name,
        description=body.description,
        intent=body.intent.model_dump() if body.intent else None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return Study.model_validate(row)


@router.get("/studies/{study_id}", response_model=Study)
async def get_study(
    study_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> Study:
    """`GET /api/v1/studies/{study_id}`."""
    return Study.model_validate(await _get_study_or_404(session, study_id))


@router.patch("/studies/{study_id}", response_model=Study)
async def update_study(
    study_id: uuid.UUID,
    body: StudyUpdate,
    session: DbSession,
    _current_user: CurrentUser,
) -> Study:
    """`PATCH /api/v1/studies/{study_id}`."""
    row = await _get_study_or_404(session, study_id)
    updates = body.model_dump(exclude_unset=True)
    if "status" in updates:
        updates["status"] = updates["status"].value
    for field, value in updates.items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return Study.model_validate(row)


@router.delete("/studies/{study_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_study(
    study_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> None:
    """`DELETE /api/v1/studies/{study_id}`.

    Hard delete. Cascades at the DB level to sources, messages,
    study_avatars, and runs (all `ON DELETE CASCADE` in setup.sql).
    Anchors scoped to this study (`scope_type='study'`) aren't covered by
    a DB foreign key, so they're deleted explicitly first.
    """
    await _get_study_or_404(session, study_id)
    await session.execute(
        delete(AnchorRow).where(AnchorRow.scope_type == "study", AnchorRow.scope_id == study_id)
    )
    await session.execute(delete(StudyRow).where(StudyRow.id == study_id))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Study has dependent records."
        ) from exc


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@router.get("/studies/{study_id}/outcome", response_model=Outcome)
async def get_outcome(
    study_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> Outcome:
    """`GET /api/v1/studies/{study_id}/outcome`."""
    row = await _get_study_or_404(session, study_id)
    return Outcome(
        study_id=row.id,
        dimension=row.outcome_dimension or "",
        scale=Scale(min=row.scale_min or 1, max=row.scale_max or 5),
    )


@router.put("/studies/{study_id}/outcome", response_model=Outcome)
async def set_outcome(
    study_id: uuid.UUID,
    body: OutcomeUpdate,
    session: DbSession,
    _current_user: CurrentUser,
) -> Outcome:
    """`PUT /api/v1/studies/{study_id}/outcome`."""
    row = await _get_study_or_404(session, study_id)
    row.outcome_dimension = body.dimension
    row.scale_min = body.scale.min
    row.scale_max = body.scale.max
    await session.commit()
    return Outcome(study_id=study_id, dimension=body.dimension, scale=body.scale)


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------


@router.get("/studies/{study_id}/anchors", response_model=AnchorSet)
async def get_anchors(
    study_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> AnchorSet:
    """`GET /api/v1/studies/{study_id}/anchors`."""
    await _get_study_or_404(session, study_id)
    rows = (
        (
            await session.execute(
                select(AnchorRow)
                .where(AnchorRow.scope_type == "study", AnchorRow.scope_id == study_id)
                .order_by(AnchorRow.scale_point)
            )
        )
        .scalars()
        .all()
    )
    return AnchorSet(
        study_id=study_id,
        anchors=[Anchor(scale_point=r.scale_point, text=r.text) for r in rows],
    )


@router.put("/studies/{study_id}/anchors", response_model=AnchorSet)
async def set_anchors(
    study_id: uuid.UUID,
    body: AnchorSetUpdate,
    session: DbSession,
    _current_user: CurrentUser,
) -> AnchorSet:
    """`PUT /api/v1/studies/{study_id}/anchors` — replaces the full anchor set."""
    await _get_study_or_404(session, study_id)
    await session.execute(
        delete(AnchorRow).where(AnchorRow.scope_type == "study", AnchorRow.scope_id == study_id)
    )
    for anchor in body.anchors:
        session.add(
            AnchorRow(
                scope_type="study",
                scope_id=study_id,
                scale_point=anchor.scale_point,
                text=anchor.text,
            )
        )
    await session.commit()
    return AnchorSet(study_id=study_id, anchors=list(body.anchors))
