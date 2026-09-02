"""ORM model for `core.sources`.

File bytes are stored in-database (`content bytea`) rather than in object
storage — a deliberate call, not the architecture doc's original R2/S3
plan. See setup.sql's comment on this column.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, LargeBinary, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Source(Base):
    """A raw document uploaded to a study, with relevance priority."""

    __tablename__ = "sources"
    __table_args__ = (
        Index("idx_sources_study", "study_id"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    study_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    priority: Mapped[str] = mapped_column(String, nullable=False, server_default="medium")
    suggested_priority: Mapped[str | None] = mapped_column(String)
    ingest_status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    summary: Mapped[str | None] = mapped_column(String)
    tags: Mapped[list | None] = mapped_column(JSONB)
    pii_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
