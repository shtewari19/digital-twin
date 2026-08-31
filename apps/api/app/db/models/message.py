"""ORM model for `core.messages`. Maps to core.messages (see setup.sql).

Only added for the join in `GET /runs/{run_id}/results` (need the message
text alongside its ranking) — no message CRUD routes exist yet.
"""

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Message(Base):
    __tablename__ = "messages"
    __table_args__: ClassVar[dict] = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    study_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    group: Mapped[str | None] = mapped_column(String, nullable=True)  # "group" — quoted automatically, reserved word
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
