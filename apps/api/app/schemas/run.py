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
    """Response for GET /runs/{run_id}/results.

    Two things arrive at different times, by design:

    * `ranking` is written as soon as scoring finishes, BEFORE the run pauses
      at `awaiting_review`. It is what a reviewer reads in order to decide, and
      it survives a rejection.
    * `report` and `baseline_lift_pct` are written only AFTER a human approves
      at Gate 2, because generating the narrative costs two LLM calls. They
      stay null on a run that was rejected or expired.

    Everything is empty/null while the run is still executing. This route never
    errors for a run without results — it just returns the empty shape.
    """

    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID
    status: RunStatus
    ranking: list[RankingEntryOut] = []
    report: str | None = None
    baseline_lift_pct: float | None = None