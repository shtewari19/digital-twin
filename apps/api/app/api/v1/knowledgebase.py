"""Knowledgebase endpoints: a source's chunks, per-study index status, and
semantic search.

`POST .../reindex` is NOT implemented here on purpose. It's documented as
an async op returning a `Job`, and there's no Job/async-execution pattern
in this app yet beyond the Temporal run workflow already in progress —
building a second, one-off async mechanism for this would need
unwinding later. Revisit once that Temporal work lands and reuse the
same pattern.

`search` needs an embedding of the query text, which needs the LiteLLM
gateway integration that doesn't exist yet (see `app.core.llm_gateway`) —
it 501s plainly rather than faking a result. The pgvector query itself is
real and ready; it just can't run until `embed_texts` can.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select, tuple_

from app.api.deps import CurrentUser, DbSession
from app.api.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.core.llm_gateway import LLMGatewayNotConfiguredError, embed_texts
from app.db.models.chunk import SourceChunk
from app.db.models.source import Source as SourceRow
from app.db.models.study import Study as StudyRow
from app.schemas import (
    Chunk,
    ChunkList,
    Knowledgebase,
    KnowledgebaseSearchRequest,
    KnowledgebaseSearchResult,
)

router = APIRouter(tags=["knowledgebase"])


@router.get("/sources/{source_id}/chunks", response_model=ChunkList)
async def list_chunks(
    source_id: uuid.UUID,
    session: DbSession,
    _current_user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> ChunkList:
    """`GET /api/v1/sources/{source_id}/chunks`."""
    if await session.get(SourceRow, source_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")

    stmt = (
        select(SourceChunk)
        .where(SourceChunk.source_id == source_id)
        .order_by(SourceChunk.created_at.desc(), SourceChunk.id.desc())
    )
    if cursor is not None:
        try:
            after = decode_cursor(cursor)
        except InvalidCursorError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        stmt = stmt.where(
            tuple_(SourceChunk.created_at, SourceChunk.id) < tuple_(after.created_at, after.id)
        )

    rows = (await session.execute(stmt.limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return ChunkList(
        data=[Chunk.model_validate(r) for r in rows], next_cursor=next_cursor, has_more=has_more
    )


@router.get("/studies/{study_id}/knowledgebase", response_model=Knowledgebase)
async def get_knowledgebase_status(
    study_id: uuid.UUID, session: DbSession, _current_user: CurrentUser
) -> Knowledgebase:
    """`GET /api/v1/studies/{study_id}/knowledgebase`."""
    if await session.get(StudyRow, study_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found.")

    source_count = await session.scalar(
        select(func.count()).select_from(SourceRow).where(SourceRow.study_id == study_id)
    )
    chunk_count = await session.scalar(
        select(func.count()).select_from(SourceChunk).where(SourceChunk.study_id == study_id)
    )
    embedded_count = await session.scalar(
        select(func.count())
        .select_from(SourceChunk)
        .where(SourceChunk.study_id == study_id, SourceChunk.embedding.is_not(None))
    )

    if chunk_count and embedded_count == chunk_count:
        kb_status = "ready"
    elif chunk_count:
        kb_status = "processing"
    else:
        kb_status = "pending"

    return Knowledgebase(
        study_id=study_id,
        status=kb_status,
        source_count=source_count or 0,
        chunk_count=chunk_count or 0,
        embedded_count=embedded_count or 0,
        coverage_pct=(embedded_count / chunk_count * 100) if chunk_count else 0.0,
    )


@router.post(
    "/studies/{study_id}/knowledgebase/search", response_model=KnowledgebaseSearchResult
)
async def search_knowledgebase(
    study_id: uuid.UUID,
    body: KnowledgebaseSearchRequest,
    session: DbSession,
    _current_user: CurrentUser,
) -> KnowledgebaseSearchResult:
    """`POST /api/v1/studies/{study_id}/knowledgebase/search`."""
    if await session.get(StudyRow, study_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found.")

    try:
        [query_vector] = await embed_texts([body.query])
    except LLMGatewayNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc

    stmt = (
        select(SourceChunk, SourceChunk.embedding.cosine_distance(query_vector).label("distance"))
        .where(SourceChunk.study_id == study_id, SourceChunk.embedding.is_not(None))
        .order_by("distance")
        .limit(body.top_k)
    )
    rows = (await session.execute(stmt)).all()
    results = [
        {
            "chunk_id": str(chunk.id),
            "source_id": str(chunk.source_id),
            "text": chunk.text,
            "score": 1 - distance,
        }
        for chunk, distance in rows
    ]
    return KnowledgebaseSearchResult(results=results)
