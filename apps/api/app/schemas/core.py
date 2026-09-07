"""Schemas for the `core` domain: domains, studies, outcomes, messages,
sources, the knowledgebase, and avatars.

Field-for-field, these mirror the `core` Postgres schema in setup.sql and
the corresponding request/response bodies documented in the API spec.
"""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.common import APIModel, Page, Priority, Timestamps

__all__ = [
    "Anchor",
    "AnchorSet",
    "AnchorSetUpdate",
    "Avatar",
    "AvatarCreate",
    "AvatarDraft",
    "AvatarList",
    "AvatarScope",
    "AvatarSource",
    "AvatarUpdate",
    "Chunk",
    "ChunkList",
    "Domain",
    "DomainCreate",
    "DomainList",
    "DomainType",
    "DomainUpdate",
    "IngestStatus",
    "Intent",
    "Knowledgebase",
    "KnowledgebaseSearchRequest",
    "KnowledgebaseSearchResult",
    "Message",
    "MessageCreate",
    "MessageList",
    "MessageUpdate",
    "Outcome",
    "OutcomeUpdate",
    "Panel",
    "PanelMember",
    "PanelUpdate",
    "Scale",
    "Source",
    "SourceAnalysis",
    "SourceList",
    "SourceUpdate",
    "Study",
    "StudyCreate",
    "StudyList",
    "StudyStatus",
    "StudyUpdate",
    "Sufficiency",
]


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


class DomainType(str, Enum):
    """Whether a domain ships built in or was authored by an operator."""

    PREDEFINED = "predefined"
    CUSTOM = "custom"


class Domain(Timestamps):
    """A market/audience context that sets persona library and prompt framing."""

    id: UUID
    name: str = Field(examples=["Pharmaceutical Marketing"])
    type: DomainType
    description: str | None = None
    compliance_profile: str = Field(
        default="standard",
        description="One of standard, strict.",
        examples=["standard"],
    )


class DomainCreate(APIModel):
    """Body for `POST /domains`."""

    name: str = Field(examples=["Q3 lead-claim test — PCPs"])
    description: str | None = None
    compliance_profile: str = Field(
        default="standard", description="One of standard, strict."
    )


class DomainUpdate(APIModel):
    """Body for `PATCH /domains/{domain_id}`."""

    name: str | None = None
    description: str | None = None
    compliance_profile: str | None = Field(
        default=None, description="One of standard, strict."
    )


DomainList = Page[Domain]


# ---------------------------------------------------------------------------
# Studies
# ---------------------------------------------------------------------------


class StudyStatus(str, Enum):
    """Lifecycle state of a study."""

    DRAFT = "draft"
    READY = "ready"
    ARCHIVED = "archived"


class Intent(APIModel):
    """Structured intent extracted from a study's free-text description."""

    audience: str | None = None
    product: str | None = None
    decision: str | None = None
    success_criteria: str | None = None


class Study(Timestamps):
    """A message-testing project scoped to one domain."""

    id: UUID
    domain_id: UUID
    owner_id: UUID
    name: str
    description: str | None = None
    intent: Intent | None = None
    status: StudyStatus = StudyStatus.DRAFT
    expires_at: datetime.datetime | None = Field(
        default=None,
        description="TTL — when the study and its data are purged.",
    )


class StudyCreate(APIModel):
    """Body for `POST /studies`.

    Use `POST /llm/assist/study-name` first to get a suggested name,
    description, and structured intent.
    """

    domain_id: UUID
    name: str | None = Field(
        default=None,
        description="Optional; a suggestion is generated if omitted.",
    )
    description: str
    intent: Intent | None = None


class StudyUpdate(APIModel):
    """Body for `PATCH /studies/{study_id}`."""

    name: str | None = None
    description: str | None = None
    intent: Intent | None = None
    status: StudyStatus | None = None


StudyList = Page[Study]


# ---------------------------------------------------------------------------
# Outcome & anchors
# ---------------------------------------------------------------------------


class Scale(APIModel):
    """The rating scale for a study's outcome dimension."""

    min: int = Field(examples=[1])
    max: int = Field(examples=[5])


class Outcome(APIModel):
    """The single attitude a study measures, and its rating scale."""

    study_id: UUID
    dimension: str = Field(
        description="The single attitude measured.",
        examples=["prescribing intent"],
    )
    scale: Scale


class OutcomeUpdate(APIModel):
    """Body for `PUT /studies/{study_id}/outcome`."""

    dimension: str
    scale: Scale


class Anchor(APIModel):
    """One anchor statement for a single scale point."""

    scale_point: int = Field(examples=[5])
    text: str = Field(examples=["I would definitely prescribe this."])


class AnchorSet(APIModel):
    """One anchor statement per scale point, for a study."""

    study_id: UUID
    anchors: list[Anchor] = Field(default_factory=list)


class AnchorSetUpdate(APIModel):
    """Body for `PUT /studies/{study_id}/anchors`."""

    anchors: list[Anchor]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class Message(Timestamps):
    """A candidate message/claim a study ranks."""

    id: UUID
    study_id: UUID
    text: str = Field(examples=["Superior efficacy vs standard of care."])
    group: str | None = Field(
        default=None, description="Optional grouping label.", examples=["set-A"]
    )
    version: int = 1


class MessageCreate(APIModel):
    """Body for `POST /studies/{study_id}/messages`."""

    text: str = Field(examples=["Superior efficacy vs standard of care"])
    group: str | None = Field(default=None, examples=["set-A"])


class MessageUpdate(APIModel):
    """Body for `PATCH /studies/{study_id}/messages/{message_id}`."""

    text: str | None = None
    group: str | None = None


MessageList = Page[Message]


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class IngestStatus(str, Enum):
    """Where a source (or the knowledgebase built from it) is in ingestion."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class Source(Timestamps):
    """A raw document uploaded to a study, with relevance priority."""

    id: UUID
    study_id: UUID
    filename: str = Field(examples=["q3-brief.pdf"])
    content_type: str = Field(examples=["application/pdf"])
    size_bytes: int = Field(examples=[10_485_760])
    priority: Priority = Priority.MEDIUM
    ingest_status: IngestStatus = IngestStatus.PENDING


class SourceUpdate(APIModel):
    """Body for `PATCH /sources/{source_id}` (re-prioritize)."""

    priority: Priority | None = None


class SourceAnalysis(APIModel):
    """Summary, tags, and suggested priority produced during ingestion."""

    source_id: UUID
    status: IngestStatus = IngestStatus.PENDING
    summary: str | None = Field(
        default=None, examples=["Clinical data sheet with efficacy endpoints"]
    )
    tags: list[str] = Field(default_factory=list)
    suggested_priority: Priority | None = None


SourceList = Page[Source]


class Sufficiency(APIModel):
    """LLM assessment of whether uploaded sources support a credible study."""

    sufficient: bool
    summary: str = Field(
        examples=["Enough to run, but competitor context is thin."]
    )
    gaps: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Knowledgebase
# ---------------------------------------------------------------------------


class Chunk(APIModel):
    """One chunk of a source's processed, embedded text."""

    id: UUID
    source_id: UUID
    text: str = Field(examples=["Superior efficacy vs standard of care"])
    position: int = 0


ChunkList = Page[Chunk]


class Knowledgebase(APIModel):
    """Index status and coverage across a study's sources."""

    study_id: UUID
    status: IngestStatus = IngestStatus.PENDING
    source_count: int = 0
    chunk_count: int = 0
    embedded_count: int = 0
    coverage_pct: float = Field(default=0.0, examples=[100.0])


class KnowledgebaseSearchRequest(APIModel):
    """Body for `POST /studies/{study_id}/knowledgebase/search`."""

    query: str = Field(examples=["efficacy vs competitor"])
    top_k: int = Field(default=8, description="Default 8.")


class KnowledgebaseSearchResult(APIModel):
    """Ranked chunks retrieved for a knowledgebase search."""

    results: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Avatars
# ---------------------------------------------------------------------------


class AvatarScope(str, Enum):
    """Whether a persona belongs to the shared library or one study."""

    LIBRARY = "library"
    STUDY = "study"


class AvatarSource(str, Enum):
    """How an avatar's profile was produced."""

    PREBUILT = "prebuilt"
    CUSTOM = "custom"
    LLM_ASSISTED = "llm_assisted"


class Avatar(Timestamps):
    """An AI persona standing in for one segment of the target audience."""

    id: UUID
    scope: AvatarScope = AvatarScope.LIBRARY
    domain_id: UUID | None = None
    study_id: UUID | None = None
    name: str = Field(examples=["The Evidence-Driven Specialist"])
    profile: str = Field(
        description="The full descriptive persona.",
        examples=["Sub-specialist who weighs RCT data and guideline alignment"],
    )
    source: AvatarSource = AvatarSource.PREBUILT


class AvatarDraft(APIModel):
    """An LLM-elaborated persona, not yet saved.

    Returned by `POST /llm/assist/persona`.
    """

    name: str | None = None
    profile: str | None = None


class AvatarCreate(APIModel):
    """Body for `POST /avatars`.

    Provide a full profile, or a rough description plus
    `POST /llm/assist/persona` to elaborate one first.
    """

    name: str
    profile: str
    scope: AvatarScope | None = None
    domain_id: UUID | None = None
    study_id: UUID | None = None
    source: AvatarSource | None = None


class AvatarUpdate(APIModel):
    """Body for `PATCH /avatars/{avatar_id}`."""

    name: str | None = None
    profile: str | None = None


AvatarList = Page[Avatar]


class PanelMember(APIModel):
    """One avatar's membership in a study's panel."""

    avatar_id: UUID
    replica_count: int = Field(
        default=1,
        ge=1,
        description="How many independent replicas of this avatar react in a run.",
        examples=[1],
    )


class Panel(APIModel):
    """The set of avatars selected to react in a study's runs."""

    study_id: UUID
    avatars: list[PanelMember] = Field(default_factory=list)


class PanelUpdate(APIModel):
    """Body for `PUT /studies/{study_id}/panel`."""

    avatars: list[PanelMember] = Field(min_length=1)
