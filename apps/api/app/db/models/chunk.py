"""ORM model for `core.source_chunks` — the pgvector-backed knowledgebase store.

The `idx_chunks_embedding` HNSW index is created by setup.sql's raw DDL,
not redeclared here (this ORM layer isn't used to generate DDL — see
`app/db/base.py`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Matches core.source_chunks.embedding's fixed dimension in setup.sql —
# chosen to match OpenAI text-embedding-3-small (see the embedding-model
# decision this vertical was built against).
EMBEDDING_DIM = 1536


class SourceChunk(Base):
    """One chunk of a source's processed, embedded text."""

    __tablename__ = "source_chunks"
    __table_args__ = (
        Index("idx_chunks_source", "source_id"),
        Index("idx_chunks_study", "study_id"),
        {"schema": "core"},
    )

    # `text` (the column) below shadows the module-level `text()` function
    # within the class body, so that function is imported as `sql_text`.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    study_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    token_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
