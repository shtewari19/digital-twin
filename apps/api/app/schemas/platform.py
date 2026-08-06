"""Schemas for the `platform` domain: async jobs, LLM providers and
bring-your-own keys, credits/usage metering, and users.

Mirrors the `platform` Postgres schema in setup.sql and the request/response
bodies documented in the API spec.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import EmailStr, Field

from app.schemas.common import APIModel, Page, Problem, Timestamps
from app.schemas.core import Intent, Scale

__all__ = [
    "JobKind",
    "JobStatus",
    "Job",
    "ModelType",
    "Model",
    "Provider",
    "ProviderKey",
    "ProviderKeyCreate",
    "StudyNameAssistRequest",
    "StudyNameSuggestion",
    "PersonaAssistRequest",
    "MessageAssistRequest",
    "AnchorAssistRequest",
    "CreditBalance",
    "LedgerEntry",
    "LedgerList",
    "Usage",
    "Role",
    "User",
]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class JobKind(str, Enum):
    """The kind of long-running operation a `Job` tracks."""

    EXPORT = "export"
    REINDEX = "reindex"


class JobStatus(str, Enum):
    """Lifecycle state of an async job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job(Timestamps):
    """Status of an asynchronous operation (export, reindex)."""

    job_id: UUID
    status: JobStatus = JobStatus.QUEUED
    kind: JobKind
    resource_id: UUID | None = None
    result_url: str | None = Field(
        default=None,
        description="Populated on success (e.g., the export download URL).",
    )
    error: Problem | None = None


# ---------------------------------------------------------------------------
# LLM providers, models & bring-your-own keys
# ---------------------------------------------------------------------------


class ModelType(str, Enum):
    """Whether an LLM provider's model does chat or embeddings."""

    CHAT = "chat"
    EMBEDDING = "embedding"


class Model(APIModel):
    """One model offered by a provider."""

    id: str = Field(examples=["anthropic/claude-sonnet"])
    name: str = Field(examples=["Claude Sonnet"])
    type: ModelType = ModelType.CHAT


class Provider(APIModel):
    """An LLM provider and the models it offers."""

    id: str = Field(examples=["anthropic"])
    name: str = Field(examples=["Anthropic"])
    supports_byo_key: bool = False
    models: list[Model] = Field(default_factory=list)


class ProviderKey(Timestamps):
    """Metadata for a stored bring-your-own provider key.

    The raw secret is write-only (see `ProviderKeyCreate`) and is never
    returned; only `last4` and descriptive metadata are.
    """

    id: UUID
    provider: str = Field(examples=["openai"])
    label: str | None = Field(default=None, examples=["Acme client key"])
    last4: str = Field(examples=["4f2a"])


class ProviderKeyCreate(APIModel):
    """Body for `POST /llm/keys`."""

    provider: str = Field(examples=["openai"])
    label: str | None = Field(default=None, examples=["Acme client key"])
    key: str = Field(
        description="The raw secret. Stored encrypted; never returned.",
    )


class StudyNameAssistRequest(APIModel):
    """Body for `POST /llm/assist/study-name`.

    Unlike the other schemas in this module, the API spec never gave this
    request body a name — it's documented as an inline object. Named here
    for a typed client/route signature; the wire shape is unchanged.
    """

    description: str = Field(
        description="Free-text of what the user is testing.",
        examples=["Test which claim wins for the Q3 PCP campaign"],
    )


class StudyNameSuggestion(APIModel):
    """Response of `POST /llm/assist/study-name`."""

    suggested_name: str | None = Field(
        default=None, examples=["Q3 lead-claim test — PCPs"]
    )
    suggested_description: str | None = Field(
        default=None,
        examples=["Test which claim wins for the Q3 PCP campaign"],
    )
    intent: Intent | None = None


class PersonaAssistRequest(APIModel):
    """Body for `POST /llm/assist/persona` — elaborates a rough description
    into a full persona (see `core.AvatarDraft` for the response).
    """

    rough_description: str = Field(
        examples=["Test which claim wins for the Q3 PCP campaign"]
    )
    domain_id: UUID | None = None


class MessageAssistRequest(APIModel):
    """Body for `POST /llm/assist/messages` — suggests candidate messages."""

    study_id: UUID
    count: int = Field(default=5, description="Default 5.")


class AnchorAssistRequest(APIModel):
    """Body for `POST /llm/assist/anchors`.

    Response is an `AnchorSetUpdate` (see `core.py`) — a first draft to
    review before `PUT /studies/{study_id}/anchors`.
    """

    outcome_dimension: str = Field(examples=["purchase intent"])
    scale: Scale


# ---------------------------------------------------------------------------
# Credits & usage
# ---------------------------------------------------------------------------


class CreditBalance(APIModel):
    """Response of `GET /credits/balance`."""

    balance: float = Field(examples=[4200])
    currency: str = Field(default="credits", description="Default 'credits'.")


class LedgerEntry(Timestamps):
    """One entry in the credit ledger."""

    id: UUID
    delta: float = Field(description="Negative = consumed.", examples=[-180])
    reason: str = Field(examples=["run:finalize"])
    run_id: UUID | None = None
    byo_key: bool = Field(
        default=False,
        description=(
            "True if metered against a bring-your-own key "
            "(tracked, not charged)."
        ),
    )


LedgerList = Page[LedgerEntry]


class Usage(APIModel):
    """Response of `GET /usage` — token/cost usage over a period.

    `from`/`to` are reserved words in Python, so they're stored as
    `period_from`/`period_to` and exposed on the wire via their JSON
    aliases (`APIModel.populate_by_name=True` accepts either).
    """

    period_from: date | None = Field(default=None, alias="from", examples=["2026-01-15"])
    period_to: date | None = Field(default=None, alias="to", examples=["2026-01-15"])
    total_tokens: int | None = Field(default=None, examples=[120])
    total_cost_credits: float | None = Field(default=None, examples=[180])
    by_provider: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class Role(str, Enum):
    """Access level of an authenticated user."""

    OPERATOR = "operator"
    ADMIN = "admin"


class User(APIModel):
    """The authenticated user, as returned by `GET /me`."""

    id: UUID
    name: str
    email: EmailStr = Field(examples=["operator@example.com"])
    role: Role = Role.OPERATOR
