"""ORM model for `core.anchors`."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Anchor(Base):
    """One anchor statement for a single scale point, scoped to a domain or study."""

    __tablename__ = "anchors"
    __table_args__ = (
        UniqueConstraint(
            "scope_type", "scope_id", "scale_point", name="uq_anchor_scope_point"
        ),
        Index("idx_anchors_scope", "scope_type", "scope_id"),
        {"schema": "core"},
    )

    # `text` (the column) below shadows the module-level `text()` function
    # within the class body, so that function is imported as `sql_text`.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    scope_type: Mapped[str] = mapped_column(String, nullable=False)
    # App-enforced reference to domains.id or studies.id — no DB-level FK
    # since it can point at either table (see setup.sql).
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scale_point: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
