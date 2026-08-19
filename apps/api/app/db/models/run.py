"""ORM model for study runs. Maps to runs.runs (see setup.sql)."""

import enum
import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import Enum, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RunStatus(str, enum.Enum):
    """Mirrors the CHECK constraint on runs.runs.status exactly.
    This ticket only drives DRAFT -> QUEUED -> RUNNING -> FINALIZED/FAILED;
    the rest exist because the same table backs later tickets
    (cost estimate, approval gate, review)."""
    DRAFT = "draft"
    CONFIGURED = "configured"
    ESTIMATED = "estimated"
    APPROVED = "approved"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    FINALIZED = "finalized"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Run(Base):
    __tablename__ = "runs"
    __table_args__: ClassVar[dict] = {"schema": "runs"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    study_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
        default=RunStatus.DRAFT,
        nullable=False,
    )
    workflow_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Columns that exist on the table but this ticket doesn't touch yet —
    # declared so the model matches the table 1:1; later tickets populate them.
    config_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model_config_json: Mapped[dict | None] = mapped_column(
        "model_config", JSONB, nullable=True
    )
    estimate: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    actuals: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())