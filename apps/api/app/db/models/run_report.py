"""ORM model for the narrative report + lift metric. Maps to
runs.run_reports (see setup.sql, PK is run_id itself — one report per run).
Written by apps/engine's generate_run_report activity — this app only
reads it, for GET /runs/{run_id}/results."""

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RunReport(Base):
    __tablename__ = "run_reports"
    __table_args__: ClassVar[dict] = {"schema": "runs"}

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    report: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_lift_pct: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
