"""ORM model for `core.domains`.

The `type` and `compliance_profile` CHECK constraints are enforced at the
database level by setup.sql and at the API boundary by
`app.schemas.core.DomainType`/`compliance_profile`; not re-declared here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Domain(Base):
    """A market/audience context that sets persona library and prompt framing."""

    __tablename__ = "domains"
    __table_args__ = (
        Index("idx_domains_tenant_type", "tenant_id", "type"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    compliance_profile: Mapped[str] = mapped_column(
        String, nullable=False, server_default="standard"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
