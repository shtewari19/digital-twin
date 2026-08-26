"""Source (uploaded document) endpoints, nested under a study, plus the
per-source analysis and per-study sufficiency check.

File bytes are stored in-database (`core.sources.content`) — see
setup.sql. `GET .../sufficiency` needs an LLM call that doesn't exist yet
(see `app.core.llm_gateway`); it reports that plainly as a 501 rather
than faking an answer. The automatic post-upload ingestion pipeline
(chunk -> embed -> summarize -> tag) also doesn't exist yet — for the
same reason `POST /knowledgebase/reindex` was held back (see
`app/api/v1/knowledgebase.py`) — so a freshly uploaded source sits at
`ingest_status="pending"` and stays there.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.llm_gateway import LLMGatewayNotConfiguredError, assess_sufficiency
from app.db.models.source import Source as SourceRow
from app.db.models.study import Study as StudyRow
from app.schemas import Priority, Source, SourceAnalysis, SourceList, SourceUpdate, Sufficiency

router = APIRouter(tags=["sources"])

# Arbitrary but documented — the API spec's own 413 response for the
# upload endpoint implies some limit exists; this is ours.
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


async def _get_study_or_404(session: DbSession, study_id: uuid.UUID) -> StudyRow:
    row = await session.get(StudyRow, study_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found.")
    return row


async def _get_source_or_404(session: DbSession, source_id: uuid.UUID) -> SourceRow:
    row = await session.get(SourceRow, source_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")
    return row


@router.get("/studies/{study_id}/sources", response_model=SourceList)
async def list_sources(
    study_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> SourceList:
    """`GET /api/v1/studies/{study_id}/sources` — not paginated per the API spec."""
    await _get_study_or_404(session, study_id)
    rows = (
        (
            await session.execute(
                select(SourceRow)
                .where(SourceRow.study_id == study_id)
                .order_by(SourceRow.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return SourceList(
        data=[Source.model_validate(r) for r in rows], next_cursor=None, has_more=False
    )


@router.post(
    "/studies/{study_id}/sources", response_model=Source, status_code=status.HTTP_201_CREATED
)
async def upload_source(
    study_id: uuid.UUID,
    session: DbSession,
    _current_user: CurrentUser,
    file: UploadFile = File(...),
    priority: Priority | None = Form(default=None),
) -> Source:
    """`POST /api/v1/studies/{study_id}/sources`.

    Stores the upload's bytes directly in `core.sources.content` and
    does NOT run ingestion (see module docstring) — the row is created
    at `ingest_status="pending"` and stays there for now.
    """
    await _get_study_or_404(session, study_id)

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {_MAX_UPLOAD_BYTES}-byte limit.",
        )

    row = SourceRow(
        study_id=study_id,
        filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
        content=content,
        priority=(priority or Priority.MEDIUM).value,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return Source.model_validate(row)


@router.get("/studies/{study_id}/sufficiency", response_model=Sufficiency)
async def get_sufficiency(
    study_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> Sufficiency:
    """`GET /api/v1/studies/{study_id}/sufficiency`."""
    await _get_study_or_404(session, study_id)
    rows = (
        (await session.execute(select(SourceRow).where(SourceRow.study_id == study_id)))
        .scalars()
        .all()
    )
    try:
        result = await assess_sufficiency(
            [{"filename": r.filename, "summary": r.summary or ""} for r in rows]
        )
    except LLMGatewayNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    return Sufficiency.model_validate(result)


@router.get("/sources/{source_id}", response_model=Source)
async def get_source(
    source_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> Source:
    """`GET /api/v1/sources/{source_id}`."""
    return Source.model_validate(await _get_source_or_404(session, source_id))


@router.patch("/sources/{source_id}", response_model=Source)
async def update_source(
    source_id: uuid.UUID,
    body: SourceUpdate,
    session: DbSession,
    _current_user: CurrentUser,
) -> Source:
    """`PATCH /api/v1/sources/{source_id}` — re-prioritize only, per the API spec."""
    row = await _get_source_or_404(session, source_id)
    updates = body.model_dump(exclude_unset=True)
    if "priority" in updates:
        updates["priority"] = updates["priority"].value
    for field, value in updates.items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return Source.model_validate(row)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> None:
    """`DELETE /api/v1/sources/{source_id}` — also removes its chunks (DB cascade)."""
    row = await _get_source_or_404(session, source_id)
    await session.delete(row)
    await session.commit()


@router.get("/sources/{source_id}/analysis", response_model=SourceAnalysis)
async def get_source_analysis(
    source_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> SourceAnalysis:
    """`GET /api/v1/sources/{source_id}/analysis`."""
    row = await _get_source_or_404(session, source_id)
    return SourceAnalysis(
        source_id=row.id,
        status=row.ingest_status,
        summary=row.summary,
        tags=row.tags or [],
        suggested_priority=row.suggested_priority,
    )
