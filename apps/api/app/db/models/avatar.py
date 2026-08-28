"""ORM models for `core.avatars` and the `core.study_avatars` join table."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Avatar(Base):
    """An AI persona standing in for one segment of the target audience."""

    __tablename__ = "avatars"
    __table_args__ = (
        Index("idx_avatars_scope_domain", "scope", "domain_id"),
        Index("idx_avatars_study", "study_id"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    scope: Mapped[str] = mapped_column(String, nullable=False)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    study_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String, nullable=False)
    profile: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, server_default="custom")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StudyAvatar(Base):
    """Join table: the set of avatars selected to react in a study's runs."""

    __tablename__ = "study_avatars"
    __table_args__ = (
        Index("idx_study_avatars_avatar", "avatar_id"),
        {"schema": "core"},
    )

    study_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    avatar_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
