"""Schemas for the `runs` domain: asynchronous study executions, their
human-approval gates, live progress, and results.

Mirrors the `runs` Postgres schema in setup.sql and the request/response
bodies documented in the API spec.

Note: the API's `Run` and `RunCreate` bodies use the field name
`model_config`, which collides with the attribute Pydantic itself reserves
for a model's `ConfigDict`. Both schemas store that value under the
Python-safe name `model_settings` and expose it on the wire as
`model_config` via `Field(alias=...)` — `APIModel`'s
`populate_by_name=True` accepts either name coming in; a route returning
these must call `.model_dump(by_alias=True)` (or set
`response_model_by_alias=True`) so it goes back out as `model_config`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import Field

from app.schemas.common import APIModel, Page, Timestamps

__all__ = [
    "ModelConfig",
    "RunStatus",
    "RunCreate",
    "RunEstimate",
    "Run",
    "RunList",
    "RunStatusView",
    "RunEventType",
    "RunEvent",
    "RecommendationTier",
    "RankingEntry",
    "RunResults",
    "ScoreDistribution",
    "AvatarReaction",
    "MessageResult",
    "ExportFormat",
    "ExportRequest",
]


class ModelConfig(APIModel):
    """Per-step model selection for a run; omit fields to use defaults."""

    reaction_model: str | None = Field(default=None, examples=["anthropic/claude-sonnet"])
    embedding_model: str | None = Field(
        default=None, examples=["openai/text-embedding-3-large"]
    )
    report_model: str | None = Field(default=None, examples=["anthropic/claude-haiku"])
    provider_key_id: UUID | None = Field(
        default=None, description="Pin the run to a bring-your-own key."
    )


class RunStatus(str, Enum):
    """Lifecycle state of a run, including its two human-approval gates."""

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


class RunCreate(APIModel):
    """Body for `POST /studies/{study_id}/runs`.

    Snapshots the study's current config (sources, messages, outcome +
    anchors, panel, model config). Then estimate -> approve -> start.
    """

    model_settings: ModelConfig | None = Field(
        default=None,
        alias="model_config",
        description="Per-step model selection; omit to use defaults.",
    )
    repetitions: int = Field(
        default=1, description="Optional N-repetition averaging for stability."
    )


class RunEstimate(APIModel):
    """A projected time/cost range plus optional LLM advice on parameters."""

    persona_count: int = Field(examples=[20])
    message_count: int = Field(examples=[6])
    est_time_seconds_min: int = Field(examples=[240])
    est_time_seconds_max: int = Field(examples=[900])
    est_cost_credits_min: float = Field(examples=[120])
    est_cost_credits_max: float = Field(examples=[260])
    advice: list[str] = Field(
        default_factory=list,
        description="Optional LLM guidance on the chosen parameters.",
    )


class Run(Timestamps):
    """An asynchronous execution of a study through the SSR engine."""

    id: UUID
    study_id: UUID
    status: RunStatus = RunStatus.DRAFT
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")
    estimate: RunEstimate | None = None
    coverage_pct: float | None = Field(default=None, examples=[70])
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime | None = None


RunList = Page[Run]


class RunStatusView(APIModel):
    """Lightweight status and progress counters for `GET /runs/{run_id}/status`."""

    run_id: UUID
    status: RunStatus = RunStatus.DRAFT
    reactions_total: int = Field(default=0, examples=[120])
    reactions_done: int = Field(default=0, examples=[84])
    coverage_pct: float = Field(default=0.0, examples=[70])
    message: str | None = Field(default=None, examples=["Running… 84 / 120 reactions"])


class RunEventType(str, Enum):
    """The kind of payload carried by one `RunEvent`."""

    PROGRESS = "progress"
    STATE_CHANGE = "state_change"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"


class RunEvent(APIModel):
    """One server-sent event payload from `GET /runs/{run_id}/events`."""

    type: RunEventType
    run_id: UUID
    status: RunStatus
    reactions_done: int = Field(examples=[84])
    reactions_total: int = Field(examples=[120])
    at: datetime


class RecommendationTier(str, Enum):
    """Where a ranked message landed relative to the baseline."""

    RECOMMENDED = "recommended"
    RUNNER_UP = "runner_up"
    DROP = "drop"


class RankingEntry(APIModel):
    """One message's position in the final Bradley-Terry ranking."""

    message_id: UUID
    text: str = Field(examples=["Superior efficacy vs standard of care"])
    rank: int = Field(examples=[1])
    strength: float = Field(
        description="Bradley-Terry strength (sums to 1 across messages).",
        examples=[0.42],
    )
    recommendation: RecommendationTier


class RunResults(APIModel):
    """The ranked recommendation plus the qualitative report for a run."""

    run_id: UUID
    ranking: list[RankingEntry] = Field(default_factory=list)
    report: str = Field(description="The qualitative narrative (Markdown).")
    baseline_lift_pct: float | None = Field(
        default=None,
        description=(
            "Winner's lift vs baseline (results are reported as lift, "
            "not raw SSR)."
        ),
        examples=[70],
    )


class ScoreDistribution(APIModel):
    """Probability mass at one scale point behind a message/avatar score."""

    scale_point: int = Field(examples=[5])
    probability: float = Field(examples=[0.42])


class AvatarReaction(APIModel):
    """One avatar's reaction to one message within a run."""

    avatar_id: UUID
    avatar_name: str = Field(examples=["Q3 lead-claim test — PCPs"])
    reaction: str = Field(
        description="The free-text reaction.",
        examples=["Compelling, but I would want head-to-head data."],
    )
    score: float = Field(examples=[0.42])
    distribution: list[ScoreDistribution] = Field(default_factory=list)


class MessageResult(APIModel):
    """Per-avatar reactions and score distribution behind one message."""

    run_id: UUID
    message_id: UUID
    text: str = Field(examples=["Superior efficacy vs standard of care"])
    aggregate_score: float = Field(examples=[0.42])
    reactions: list[AvatarReaction] = Field(default_factory=list)


class ExportFormat(str, Enum):
    """Output format for a run export."""

    MARKDOWN = "markdown"
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"


class ExportRequest(APIModel):
    """Body for `POST /runs/{run_id}/export`."""

    format: ExportFormat
    template_id: UUID | None = Field(
        default=None,
        description="For pptx, an uploaded org template to render into.",
    )
