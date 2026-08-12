"""Shared building blocks reused across every API schema.

These mirror the conventions and shared entries in the API spec's "Data
models (schemas)" section: the RFC 7807 problem envelope, cursor
pagination, the created_at/updated_at pair every persisted resource
carries, and the Priority enum shared by sources and avatars.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "APIModel",
    "Page",
    "PageMeta",
    "Priority",
    "Problem",
    "Timestamps",
]

_T = TypeVar("_T", bound=BaseModel)


class APIModel(BaseModel):
    """Base class for every request/response schema in this package.

    Configures the behavior every schema in this package relies on:
    construction from ORM row objects (`from_attributes`), accepting either
    a field's Python name or its JSON alias (`populate_by_name`), and
    disabling Pydantic's "model_" namespace protection so fields such as
    `Run.model_config` (see runs.py) don't trigger a false-positive warning.
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        protected_namespaces=(),
    )


class Priority(str, Enum):
    """Relevance priority assigned to a study source or avatar."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Problem(APIModel):
    """RFC 7807 problem details, returned on every non-2xx response."""

    type: str = Field(
        default="about:blank",
        description="A URI reference identifying the problem type.",
    )
    title: str | None = Field(default=None, examples=["Not Found"])
    status: int | None = Field(default=None, examples=[404])
    detail: str | None = Field(
        default=None, examples=["No study exists with that id."]
    )
    instance: str | None = Field(
        default=None, description="URI of the specific problem occurrence."
    )
    errors: list[dict[str, Any]] | None = Field(
        default=None,
        description="Field-level validation errors (when applicable).",
    )


class Timestamps(APIModel):
    """Server-managed creation/modification timestamps.

    Mixed into every persisted resource; both fields are read-only.
    """

    created_at: datetime
    updated_at: datetime


class PageMeta(APIModel):
    """Cursor-pagination metadata shared by every list response."""

    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page, or null if none.",
    )
    has_more: bool = False


class Page(PageMeta, Generic[_T]):
    """A cursor-paginated page of `_T` items.

    Concrete list schemas (`DomainList`, `StudyList`, `RunList`, ...) are
    aliases of `Page[SomeModel]` instead of hand-written copies of
    PageMeta plus a `data` field, per the doc's repeated
    "Composition: PageMeta" pattern.
    """

    data: list[_T] = Field(default_factory=list)
