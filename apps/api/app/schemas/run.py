import uuid

from pydantic import BaseModel, ConfigDict

from app.db.models.run import RunStatus


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    study_id: uuid.UUID
    status: RunStatus
    workflow_id: str | None = None


class RankingEntryOut(BaseModel):
    """One message's position in the Bradley-Terry ranking. Mirrors a row of
    runs.run_message_results, joined to its message text."""

    model_config = ConfigDict(from_attributes=True)

    message_id: uuid.UUID
    text: str
    rank: int | None = None
    bt_strength: float | None = None
    aggregate_score: float | None = None
    recommendation: str | None = None


class RunResultsOut(BaseModel):
    """Response for GET /runs/{run_id}/results — the ranking plus the
    narrative report apps/engine's generate_run_report activity writes.
    ranking/report/baseline_lift_pct are empty/null until the run reaches
    at least awaiting_review."""

    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID
    status: RunStatus
    ranking: list[RankingEntryOut] = []
    report: str | None = None
    baseline_lift_pct: float | None = None