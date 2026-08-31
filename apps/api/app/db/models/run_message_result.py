"""ORM model for ranked per-message results. Maps to runs.run_message_results
(see setup.sql). Written by apps/engine's rollup_message_results activity —
this app only reads it, for GET /runs/{run_id}/results."""

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RunMessageResult(Base):
    __tablename__ = "run_message_results"
    __table_args__: ClassVar[dict] = {"schema": "runs"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    aggregate_score: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    bt_strength: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
