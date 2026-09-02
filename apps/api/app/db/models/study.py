"""ORM model for `core.studies`."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Study(Base):
    """A message-testing project scoped to one domain."""

    __tablename__ = "studies"
    __table_args__ = (
        Index("idx_studies_owner_created", "owner_id", "created_at", "id"),
        Index("idx_studies_domain", "domain_id"),
        Index("idx_studies_expires", "expires_at"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    domain_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    intent: Mapped[dict | None] = mapped_column(JSONB)
    outcome_dimension: Mapped[str | None] = mapped_column(String)
    scale_min: Mapped[int | None] = mapped_column(Integer, server_default="1")
    scale_max: Mapped[int | None] = mapped_column(Integer, server_default="5")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="draft")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Column exists on the table (soft-delete groundwork) but nothing sets it
    # yet — DELETE /studies/{id} does a real hard delete, matching the API
    # spec's "cascades to sources, messages, avatars, runs..." wording.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
