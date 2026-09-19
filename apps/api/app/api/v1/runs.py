"""Run endpoints — the full lifecycle of one asynchronous study execution.

State machine
-------------
Every transition below is enforced here, in `_require_status`; the API owns
the pre-start states and the workflow owns everything from `running`
onward (it writes its own status via the `update_run_status` activity, so a
status is true even if this process dies mid-request).

    draft ──estimate──> estimated ──approve (Gate 1)──> approved
                                                          │
                                                        start
                                                          ▼
                                    queued ──> running ──> awaiting_review
                                                                │
                                              ┌─────────────────┴─────────────┐
                                        finalize (Gate 2)                (timeout)
                                              ▼                             ▼
                                          finalized                      expired

    cancel      : ANY non-terminal state, before or after start
    <any step>  : failed, if an activity exhausts its retries

Two human gates, matching the contract:

  * **Gate 1 — `POST /runs/{id}/approve`.** Sign-off on the assembled config
    and the estimate *before* any money is spent. Purely an API-side
    transition; no workflow exists yet.
  * **Gate 2 — `POST /runs/{id}/finalize`.** Sign-off on the *results*. The
    workflow genuinely blocks at `awaiting_review` waiting for a `finalize`
    or `cancel` signal (see `APP_RUN_REVIEW_GATE_ENABLED`), so this route
    signals Temporal rather than writing the status itself.

Stopping a run is one endpoint, `POST /runs/{id}/cancel`, valid from every
non-terminal state — before start, mid-batch, or parked at Gate 2 — and
`?force=true` escalates it to an immediate Temporal terminate. Rejecting
results at Gate 2 is the same call: cancelling from `awaiting_review` keeps
the results in the database and ends the run `cancelled` rather than
`finalized`. See `cancel_run`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query
from fastapi import status as http_status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.service import RPCError

from app.api.deps import CurrentUser, DbSession
from app.api.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.core import idempotency
from app.core.config import settings
from app.core.problem import (
    TYPE_ACTIVE_RUN,
    ProblemError,
    conflict,
    invalid_state,
    not_found,
    unprocessable,
)
from app.core.temporal import STUDY_RUN_WORKFLOW_NAME, get_temporal_client
from app.db.models.message import Message
from app.db.models.run import Run as RunRow
from app.db.models.run import RunStatus as DbRunStatus
from app.db.models.run_message_result import RunMessageResult
from app.db.models.run_report import RunReport
from app.schemas.run import RankingEntryOut, RunResultsOut
from app.schemas.runs import (
    ModelConfig,
    Run,
    RunCreate,
    RunEstimate,
    RunEvent,
    RunEventType,
    RunList,
    RunStatusView,
)

router = APIRouter()
log = logging.getLogger("api.runs")

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description=(
            "Client-generated key so retries of this state-changing request "
            "are applied at most once."
        ),
    ),
]

# --- state machine -----------------------------------------------------------

S = DbRunStatus

#: Statuses a run must be in for each transition to be legal.
_ESTIMATABLE = {S.DRAFT, S.CONFIGURED, S.ESTIMATED}
_APPROVABLE = {S.ESTIMATED}
_STARTABLE = {S.APPROVED}
_REVIEWABLE = {S.AWAITING_REVIEW}
#: Live in Temporal — a signal (or a terminate) can reach the workflow.
_LIVE = {S.QUEUED, S.RUNNING, S.AWAITING_REVIEW}
#: Terminal — nothing can move a run out of these.
_TERMINAL = {S.FINALIZED, S.FAILED, S.CANCELLED, S.EXPIRED}
#: A run occupies the caller's single active-run slot in these states.
_ACTIVE = {S.QUEUED, S.RUNNING, S.AWAITING_REVIEW}


def _require_status(run: RunRow, allowed: set[DbRunStatus], action: str) -> None:
    """Raise a 409 problem unless `run` is in one of `allowed`."""
    if run.status not in allowed:
        expected = ", ".join(sorted(s.value for s in allowed))
        raise invalid_state(
            f"Cannot {action} a run in status '{run.status.value}'; expected one of: {expected}."
        )


# --- helpers -----------------------------------------------------------------


async def _load_run(session: AsyncSession, run_id: uuid.UUID, user_id: uuid.UUID) -> RunRow:
    """Fetch a run the caller is allowed to see, or raise 404.

    Ownership is checked through the run's study (`core.studies.owner_id`), and
    a run the caller doesn't own reads as 404 rather than 403 so the endpoint
    doesn't confirm that someone else's run id exists.
    """
    run = await session.get(RunRow, run_id)
    if run is None or not await _owns_study(session, run.study_id, user_id):
        raise not_found(f"No run exists with id {run_id}.")
    return run


async def _owns_study(session: AsyncSession, study_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    row = (
        await session.execute(
            text(
                "SELECT 1 FROM core.studies "
                "WHERE id = :study_id AND owner_id = :user_id AND deleted_at IS NULL"
            ),
            {"study_id": study_id, "user_id": user_id},
        )
    ).first()
    return row is not None


def _to_run(row: RunRow) -> Run:
    """Map a `runs.runs` row onto the contract's `Run` body.

    Hand-written rather than `from_attributes` because the wire field
    `model_config` is stored on the ORM as `model_config_json` and on the
    schema as `model_settings` (Pydantic reserves the name) — see the module
    docstring in `app.schemas.runs`.
    """
    return Run(
        id=row.id,
        study_id=row.study_id,
        status=row.status.value,
        model_settings=ModelConfig.model_validate(row.model_config_json)
        if row.model_config_json
        else None,
        estimate=RunEstimate.model_validate(row.estimate) if row.estimate else None,
        coverage_pct=float(row.coverage_pct) if row.coverage_pct is not None else None,
        started_at=row.started_at,
        finished_at=row.finished_at,
        expires_at=row.expires_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _signal(run: RunRow, signal: str, note: str | None, action: str) -> None:
    """Send `signal` to the run's workflow, mapping Temporal errors to problems.

    Used by `finalize`, where a closed workflow genuinely is a conflict —
    there is no sane fallback for "finalize a run that already ended".
    `cancel` handles that case itself instead, by writing the terminal status.
    """
    if run.workflow_id is None:
        raise invalid_state(f"Run {run.id} has no workflow to {action}.")
    handle = get_temporal_client().get_workflow_handle(run.workflow_id)
    try:
        await handle.signal(signal, note)
    except RPCError as exc:
        # The workflow already closed (finished, terminated, timed out) between
        # our status read and the signal. Report it as a state conflict — the
        # DB status the client saw is simply stale.
        raise conflict(
            f"Cannot {action} run {run.id}: its workflow is no longer running ({exc.message})."
        ) from exc
    log.info("run %s: %s signal sent", run.id, signal)


async def _progress(session: AsyncSession, run: RunRow) -> tuple[int, int]:
    """`(done, total)` reactions for a run.

    `done` counts persisted `runs.run_reactions` rows. `total` is derived
    from the run's *config snapshot* — `len(avatar_ids) × len(claims)`, the
    exact same cross product `fetch_study_context` builds — rather than from
    a stored count or a live count of the study. Deriving it means the
    denominator cannot drift from what the engine will actually execute, and
    there is no redundant `pair_count` to keep in sync.
    """
    done = (
        await session.execute(
            text("SELECT count(*) FROM runs.run_reactions WHERE run_id = :run_id"),
            {"run_id": run.id},
        )
    ).scalar_one()

    total = _pair_count(run.config_snapshot)
    if not total:
        # A snapshot that predates these keys: fall back to the study's
        # current shape, which is the best guess available.
        personas, messages = await _study_counts(session, run.study_id)
        total = personas * messages
    return int(done), total


def _respondents_per_avatar(snapshot: dict | None) -> int:
    """How many respondents each avatar/persona is panelled with.

    An avatar is an archetype ("Academic Oncologist"), not a person — a study
    panels N respondents of it. Absent or invalid means 1, matching runs
    created before the field existed.
    """
    raw = (snapshot or {}).get("respondents_per_avatar") or 1
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _pair_count(snapshot: dict | None) -> int:
    """The number of reactions a run will produce:

        len(avatar_ids) × respondents_per_avatar × len(claims)

    This is exactly the cross product `fetch_study_context` builds, computed in
    one place so the progress denominator, the estimate and the create-time
    validation cannot disagree with what the engine actually executes.
    """
    snapshot = snapshot or {}
    avatars = snapshot.get("avatar_ids") or snapshot.get("avatar_id") or []
    if isinstance(avatars, str):
        avatars = [avatars]
    claims = snapshot.get("claims") or snapshot.get("message_ids") or []
    return len(avatars) * _respondents_per_avatar(snapshot) * len(claims)


async def _study_counts(session: AsyncSession, study_id: uuid.UUID) -> tuple[int, int]:
    """`(persona_count, message_count)` for a study."""
    row = (
        await session.execute(
            text(
                """
                SELECT (SELECT count(*) FROM core.study_avatars WHERE study_id = :sid) AS personas,
                       (SELECT count(*) FROM core.messages      WHERE study_id = :sid) AS messages
                """
            ),
            {"sid": study_id},
        )
    ).one()
    return int(row.personas), int(row.messages)


def _status_view(run: RunRow, done: int, total: int) -> RunStatusView:
    coverage = round(done / total * 100, 2) if total else 0.0
    return RunStatusView(
        run_id=run.id,
        status=run.status.value,
        reactions_total=total,
        reactions_done=done,
        coverage_pct=coverage,
        message=_progress_message(run.status, done, total),
    )


def _progress_message(status: DbRunStatus, done: int, total: int) -> str:
    match status:
        case S.RUNNING:
            return f"Running… {done} / {total} reactions"
        case S.QUEUED:
            return "Queued — waiting for a worker"
        case S.AWAITING_REVIEW:
            return "Complete — awaiting human review"
        case S.FINALIZED:
            return f"Finalized — {done} reactions scored"
        case _:
            return status.value.replace("_", " ").capitalize()


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


@router.get("/studies/{study_id}/runs", response_model=RunList, operation_id="listRuns")
async def list_runs(
    study_id: uuid.UUID,
    session: DbSession,
    current_user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> RunList:
    """List a study's runs, newest first (keyset paginated)."""
    if not await _owns_study(session, study_id, current_user.id):
        raise not_found(f"No study exists with id {study_id}.")

    stmt = (
        select(RunRow)
        .where(RunRow.study_id == study_id)
        .order_by(RunRow.created_at.desc(), RunRow.id.desc())
    )
    if cursor is not None:
        try:
            after = decode_cursor(cursor)
        except InvalidCursorError as exc:
            raise unprocessable(str(exc)) from exc
        stmt = stmt.where(
            tuple_(RunRow.created_at, RunRow.id) < tuple_(after.created_at, after.id)
        )

    rows = (await session.execute(stmt.limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    rows = list(rows[:limit])
    return RunList(
        data=[_to_run(r) for r in rows],
        next_cursor=encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None,
        has_more=has_more,
    )


@router.post(
    "/studies/{study_id}/runs",
    response_model=Run,
    status_code=http_status.HTTP_201_CREATED,
    operation_id="createRun",
)
async def create_run(
    study_id: uuid.UUID,
    session: DbSession,
    current_user: CurrentUser,
    body: RunCreate | None = None,
    idempotency_key: IdempotencyKey = None,
) -> Run:
    """Create a run in `draft`, snapshotting the study's current config.

    The snapshot is what makes a run reproducible: from here on the pipeline
    reads `runs.runs.config_snapshot`, so editing the study's messages,
    avatars or anchors afterwards does not change what this run executes.
    Next: estimate → approve → start.
    """
    if not await _owns_study(session, study_id, current_user.id):
        raise not_found(f"No study exists with id {study_id}.")

    replay = await idempotency.claim(session, idempotency_key, "create_run", current_user.id)
    if replay is not None:
        log.info("create_run replayed from Idempotency-Key -> run %s", replay.resource_id)
        return _to_run(await _load_run(session, replay.resource_id, current_user.id))

    body = body or RunCreate()

    snapshot = await _build_config_snapshot(session, study_id, body)
    if _pair_count(snapshot) == 0:
        raise unprocessable(
            "Study has no avatar × message pairs to run; add at least one avatar and "
            "one message first."
        )

    run = RunRow(
        study_id=study_id,
        tenant_id=current_user.tenant_id,
        status=S.DRAFT,
        config_snapshot=snapshot,
        model_config_json=(
            body.model_settings.model_dump(mode="json", by_alias=True)
            if body.model_settings
            else None
        ),
        provider_key_id=body.model_settings.provider_key_id if body.model_settings else None,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    await idempotency.record(
        session, idempotency_key, "create_run", current_user.id, run.id, 201
    )
    log.info(
        "run %s created for study %s -> draft (%s pairs)", run.id, study_id, _pair_count(snapshot)
    )
    return _to_run(run)


async def _build_config_snapshot(
    session: AsyncSession, study_id: uuid.UUID, body: RunCreate
) -> dict:
    """Freeze everything the run will execute against.

    This dict is the engine's *only* input for the run: apps/engine's
    `fetch_study_context` activity reads `kbq`, `claims`, `avatar_ids`,
    `anchors` and `penalties` straight out of it, so editing the study's
    messages, avatars or anchors after this point cannot change what the run
    executes.

    `claims` and `anchors` carry their ids *and* their text, so the engine
    never has to re-read `core.messages` / `core.anchors` and can therefore
    never pick up a later edit. The ids in them are the foreign-key targets
    for `runs.run_reactions`.

    Nothing derivable is stored: the pair count is `len(avatar_ids) ×
    len(claims)`, computed by `_pair_count` wherever it is needed, so there
    is no second copy to fall out of sync.

    `penalties` are lifted from `core.studies.intent->'penalties'` — that's
    where study-level scoring configuration lives.
    """
    study = (
        await session.execute(
            text(
                """
                SELECT s.name, s.domain_id, d.name AS domain_name,
                       s.outcome_dimension, s.intent
                  FROM core.studies s
                  JOIN core.domains d ON d.id = s.domain_id
                 WHERE s.id = :sid
                """
            ),
            {"sid": study_id},
        )
    ).one()

    claims = [
        {"id": str(r[0]), "text": r[1]}
        for r in (
            await session.execute(
                text(
                    "SELECT id, text FROM core.messages WHERE study_id = :sid "
                    "ORDER BY position, id"
                ),
                {"sid": study_id},
            )
        ).all()
    ]
    avatar_ids = [
        str(r[0])
        for r in (
            await session.execute(
                text("SELECT avatar_id FROM core.study_avatars WHERE study_id = :sid"),
                {"sid": study_id},
            )
        ).all()
    ]
    anchors = [
        {"id": str(r[0]), "scale_point": r[1], "text": r[2]}
        for r in (
            await session.execute(
                text(
                    """
                    SELECT id, scale_point, text FROM core.anchors
                     WHERE (scope_type = 'study'  AND scope_id = :sid)
                        OR (scope_type = 'domain' AND scope_id = :did)
                     ORDER BY scale_point
                    """
                ),
                {"sid": study_id, "did": study.domain_id},
            )
        ).all()
    ]

    intent = study.intent or {}
    if isinstance(intent, str):
        intent = json.loads(intent)

    return {
        # ---- THE PIPELINE'S INPUT: every key below is read by apps/engine's
        # ---- fetch_study_context activity, and nothing else is.
        #: The Key Belief Question. Copied from core.studies.outcome_dimension
        #: at creation time and frozen here; the column is only the fallback
        #: for a snapshot that predates this key. Stored ONCE, under this name
        #: — `study` below is display metadata and must not repeat it.
        "kbq": study.outcome_dimension,
        "claims": claims,
        "avatar_ids": avatar_ids,
        #: Respondents panelled per persona. The engine expands the cross
        #: product by this: personas x respondents x claims reactions.
        "respondents_per_avatar": body.respondents_per_avatar,
        "anchors": anchors,
        "penalties": intent.get("penalties", []),

        # ---- provenance: what this run was created from. Not read by the
        # ---- pipeline; it is here so a run is self-describing in the DB and
        # ---- in an export, without joining back to tables that may have
        # ---- changed (or been deleted) since.
        "domain": {"id": str(study.domain_id), "name": study.domain_name},
        #: The scale itself is defined by `anchors[].scale_point`; the study's
        #: declared scale_min/scale_max are NOT copied here because nothing in
        #: the engine reads them and a second declaration could only drift from
        #: the anchors.
        "study": {"id": str(study_id), "name": study.name},
        "snapshot_at": datetime.now(tz=UTC).isoformat(),

    }


# ---------------------------------------------------------------------------
# Single run
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}", response_model=Run, operation_id="getRun")
async def get_run(run_id: uuid.UUID, session: DbSession, current_user: CurrentUser) -> Run:
    """Get one run."""
    return _to_run(await _load_run(session, run_id, current_user.id))


@router.post("/runs/{run_id}/estimate", response_model=RunEstimate, operation_id="estimateRun")
async def estimate_run(
    run_id: uuid.UUID, session: DbSession, current_user: CurrentUser
) -> RunEstimate:
    """Project time and cost for the snapshotted config, and move to `estimated`.

    Deliberately arithmetic, not an LLM call: pair count × per-reaction
    bounds from settings (`APP_ESTIMATE_*`). `advice` carries the cheap
    heuristics worth surfacing before someone approves a spend — it is where
    an LLM-generated recommendation would slot in later.

    Re-runnable while the run is still `estimated`, so a client can refresh a
    stale figure without recreating the run.
    """
    run = await _load_run(session, run_id, current_user.id)
    _require_status(run, _ESTIMATABLE, "estimate")

    snapshot = run.config_snapshot or {}
    persona_count = len(snapshot.get("avatar_ids") or [])
    message_count = len(snapshot.get("claims") or [])
    if not persona_count or not message_count:
        persona_count, message_count = await _study_counts(session, run.study_id)

    #: personas × respondents each × claims — the same cross product
    #: fetch_study_context builds and _pair_count reports.
    respondents = _respondents_per_avatar(snapshot)
    reactions = persona_count * respondents * message_count
    estimate = RunEstimate(
        persona_count=persona_count,
        message_count=message_count,
        est_time_seconds_min=int(reactions * settings.estimate_seconds_per_reaction_min),
        est_time_seconds_max=int(reactions * settings.estimate_seconds_per_reaction_max),
        est_cost_credits_min=round(reactions * settings.estimate_credits_per_reaction_min, 2),
        est_cost_credits_max=round(reactions * settings.estimate_credits_per_reaction_max, 2),
        advice=_estimate_advice(persona_count, respondents, message_count, reactions),
    )

    run.estimate = estimate.model_dump(mode="json")
    run.status = S.ESTIMATED
    await session.commit()
    log.info("run %s estimated: %s reactions -> %s", run_id, reactions, S.ESTIMATED.value)
    return estimate


def _estimate_advice(personas: int, respondents: int, messages: int, reactions: int) -> list[str]:
    """Heuristic guidance on the chosen parameters."""
    advice: list[str] = []
    judges = personas * respondents
    if judges < 20:
        advice.append(
            f"Only {judges} respondents ({personas} personas x {respondents} each) — "
            "Bradley-Terry strengths will be noisy. 20+ gives a stable ranking; raise "
            "respondents_per_avatar or add personas."
        )
    if messages < 2:
        advice.append(
            "A ranking needs at least two messages to compare; add more claims to this study."
        )
    if reactions > 5_000:
        advice.append(
            f"{reactions:,} reactions is a large run; it will span several "
            "continue_as_new hops and is worth starting outside peak hours."
        )
    return advice


@router.post("/runs/{run_id}/approve", response_model=Run, operation_id="approveRun")
async def approve_run(
    run_id: uuid.UUID,
    session: DbSession,
    current_user: CurrentUser,
    idempotency_key: IdempotencyKey = None,
) -> Run:
    """**Gate 1** — human sign-off on the assembled config and estimate.

    Nothing is running yet, so this is a plain DB transition (`estimated` →
    `approved`); no Temporal involvement. `start` is what actually spends
    money, and it refuses anything that isn't `approved`.
    """
    run = await _load_run(session, run_id, current_user.id)

    replay = await idempotency.claim(session, idempotency_key, "approve_run", current_user.id)
    if replay is not None:
        return _to_run(run)

    _require_status(run, _APPROVABLE, "approve")
    run.status = S.APPROVED
    await session.commit()
    await session.refresh(run)

    await idempotency.record(session, idempotency_key, "approve_run", current_user.id, run.id, 200)
    log.info("run %s -> %s (gate 1 approved by %s)", run_id, S.APPROVED.value, current_user.id)
    return _to_run(run)


@router.post(
    "/runs/{run_id}/start",
    response_model=Run,
    status_code=http_status.HTTP_202_ACCEPTED,
    operation_id="startRun",
)
async def start_run(
    run_id: uuid.UUID,
    session: DbSession,
    current_user: CurrentUser,
    idempotency_key: IdempotencyKey = None,
) -> Run:
    """Enqueue the durable workflow. 409s if the caller already has an active run.

    The status is set to `queued` *before* `start_workflow` so a crash between
    the two can never leave a run looking `approved` while a workflow is
    already grinding away. `running` is then written by the workflow's own
    first activity, not here — so the status is true even if this process dies
    the instant after the call returns.
    """
    run = await _load_run(session, run_id, current_user.id)

    replay = await idempotency.claim(session, idempotency_key, "start_run", current_user.id)
    if replay is not None:
        return _to_run(run)

    _require_status(run, _STARTABLE, "start")
    await _assert_no_active_run(session, current_user.id, run.id)

    run.status = S.QUEUED
    workflow_id = f"study-run-{run_id}"
    run.workflow_id = workflow_id
    await session.commit()

    client = get_temporal_client()
    try:
        await client.start_workflow(
            STUDY_RUN_WORKFLOW_NAME,
            {
                "run_id": str(run_id),
                "study_id": str(run.study_id),
                "review_gate_enabled": settings.run_review_gate_enabled,
                "review_timeout_seconds": settings.run_review_timeout_seconds,
            },
            id=workflow_id,
            task_queue=settings.task_queue,
        )
    except Exception:
        # Roll the run back to approved so the client can retry rather than
        # leaving it stranded in queued with no workflow behind it.
        run.status = S.APPROVED
        run.workflow_id = None
        await session.commit()
        log.exception("run %s: start_workflow failed, rolled back to approved", run_id)
        raise

    await session.refresh(run)
    await idempotency.record(session, idempotency_key, "start_run", current_user.id, run.id, 202)
    log.info("run %s submitted to Temporal (workflow_id=%s)", run_id, workflow_id)
    return _to_run(run)


async def _assert_no_active_run(
    session: AsyncSession, user_id: uuid.UUID, this_run_id: uuid.UUID
) -> None:
    """Enforce the one-active-run-per-user cap the contract's 409 describes."""
    active = (
        await session.execute(
            text(
                """
                SELECT count(*)
                  FROM runs.runs r
                  JOIN core.studies s ON s.id = r.study_id
                 WHERE s.owner_id = :owner_id
                   AND r.status = ANY(:active)
                   AND r.id <> :this_run_id
                """
            ),
            {
                "owner_id": user_id,
                "active": [s.value for s in _ACTIVE],
                "this_run_id": this_run_id,
            },
        )
    ).scalar_one()
    if active >= settings.max_active_runs_per_user:
        raise ProblemError(
            409,
            f"You already have {active} active run(s); wait for it to finish, or cancel it "
            "before starting another.",
            type_=TYPE_ACTIVE_RUN,
        )


@router.post(
    "/runs/{run_id}/finalize",
    response_model=Run,
    status_code=http_status.HTTP_202_ACCEPTED,
    operation_id="finalizeRun",
)
async def finalize_run(
    run_id: uuid.UUID,
    session: DbSession,
    current_user: CurrentUser,
    note: str | None = None,
    idempotency_key: IdempotencyKey = None,
) -> Run:
    """**Gate 2** — human sign-off on the results; makes them exportable.

    Signals the waiting workflow instead of writing `finalized` directly, so
    the workflow stays the single writer of every post-start status. The
    response is therefore 202: the run is still `awaiting_review` in the body
    and flips to `finalized` within a moment. Poll `GET /runs/{id}` or the
    event stream to observe it.

    The other half of Gate 2 is `POST /runs/{id}/cancel` — rejecting results
    is just cancelling from `awaiting_review`.
    """
    run = await _load_run(session, run_id, current_user.id)

    replay = await idempotency.claim(session, idempotency_key, "finalize_run", current_user.id)
    if replay is not None:
        return _to_run(run)

    _require_status(run, _REVIEWABLE, "finalize")
    await _signal(run, "finalize", note, "finalize")

    await idempotency.record(
        session, idempotency_key, "finalize_run", current_user.id, run.id, 202
    )
    return _to_run(run)


@router.post("/runs/{run_id}/cancel", response_model=Run, operation_id="cancelRun")
async def cancel_run(
    run_id: uuid.UUID,
    session: DbSession,
    current_user: CurrentUser,
    note: str | None = None,
    force: bool = False,
    idempotency_key: IdempotencyKey = None,
) -> Run:
    """Stop a run. Valid from **every** non-terminal state, before or after start.

    This is the only stop mechanism, and it covers all three things people
    mean by it:

      * **Not started yet** (`draft`/`configured`/`estimated`/`approved`) —
        there is no workflow, so the status is written here directly and the
        response already reads `cancelled`.
      * **Live** (`queued`/`running`/`awaiting_review`) — sends the `cancel`
        signal. The workflow checks it at every point it can safely stop:
        before the first batch, after each batch is persisted, before the
        rollup, before the report, and inside the Gate 2 review wait. So the
        worst case is the remainder of the batch currently in flight — the
        in-flight LLM call is deliberately allowed to finish and be persisted
        rather than paying for it and throwing it away. Everything scored so
        far stays in `runs.run_reactions`, and the workflow writes
        `cancelled` + `finished_at` itself. The response still reads the
        pre-signal status — the workflow, not the API, is the writer — so
        poll `GET /runs/{id}/status` or the event stream to see it flip.
      * **Cancelling at Gate 2** is how results are rejected: the ranking and
        report stay in the database and remain readable through
        `/runs/{id}/results`, they simply never become exportable. Pass the
        reason in `note`.

    `force=true` escalates to an immediate Temporal `terminate()` instead of
    the signal — for a run that has to stop this instant, one wedged in a
    poison-pill retry loop, or one whose worker is gone. The workflow then never
    gets to run another line of Python, so this route writes `cancelled`
    itself, and an activity that was mid-flight (an LLM call, a batch write)
    may not have committed its side effects. Prefer the graceful path.

    A run whose workflow has already closed underneath us (finished,
    terminated, timed out) is not an error either: the API falls back to
    writing the terminal status itself, so a cancel never leaves a run
    stranded in a live-looking state.
    """
    run = await _load_run(session, run_id, current_user.id)

    replay = await idempotency.claim(session, idempotency_key, "cancel_run", current_user.id)
    if replay is not None:
        return _to_run(run)

    if run.status in _TERMINAL:
        raise invalid_state(
            f"Cannot cancel a run in status '{run.status.value}'; it has already finished."
        )

    if run.workflow_id is None or run.status not in _LIVE:
        # Nothing is running yet — this is a pure bookkeeping cancel.
        await _write_cancelled(session, run, reason="cancelled", note=note)
        log.info("run %s cancelled before start", run_id)
    elif force:
        handle = get_temporal_client().get_workflow_handle(run.workflow_id)
        try:
            await handle.terminate(reason=note or "cancelled via API (force)")
        except RPCError as exc:
            log.warning("run %s: terminate on a closed workflow (%s)", run_id, exc.message)
        await _write_cancelled(session, run, reason="terminated", note=note)
        log.info("run %s: workflow terminated (force cancel)", run_id)
    else:
        handle = get_temporal_client().get_workflow_handle(run.workflow_id)
        try:
            await handle.signal("cancel", note)
            log.info("run %s: cancel signal sent", run_id)
        except RPCError as exc:
            # The workflow closed between our status read and the signal, so
            # nobody is left to write the terminal status. Do it here rather
            # than 409ing on a run the caller can no longer stop any other way.
            log.warning(
                "run %s: cancel signal failed (%s) — writing cancelled directly",
                run_id,
                exc.message,
            )
            await _write_cancelled(session, run, reason="cancelled", note=note)

    await idempotency.record(session, idempotency_key, "cancel_run", current_user.id, run.id, 200)
    return _to_run(run)


async def _write_cancelled(
    session: AsyncSession, run: RunRow, *, reason: str, note: str | None
) -> None:
    """Land a run on `cancelled` from the API side.

    Only for the cases where no workflow will do it: a run that never
    started, one that was force-terminated, or one whose workflow has already
    closed. On the graceful path the workflow owns this write.
    """
    run.status = S.CANCELLED
    run.finished_at = datetime.now(tz=UTC)
    run.error = {"reason": reason, "note": note}
    await session.commit()
    await session.refresh(run)


@router.get("/runs/{run_id}/status", response_model=RunStatusView, operation_id="getRunStatus")
async def get_run_status(
    run_id: uuid.UUID, session: DbSession, current_user: CurrentUser
) -> RunStatusView:
    """Lightweight status and progress counters — cheap enough to poll.

    Counters come from Postgres (`runs.run_reactions`), not a Temporal query,
    so this works even while no worker is up and it never blocks on the
    workflow being reachable.
    """
    run = await _load_run(session, run_id, current_user.id)
    done, total = await _progress(session, run)
    return _status_view(run, done, total)


@router.get(
    "/runs/{run_id}/events",
    operation_id="streamRunEvents",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "An event stream. Each event's data is a RunEvent JSON object.",
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream_run_events(
    run_id: uuid.UUID, session: DbSession, current_user: CurrentUser
) -> StreamingResponse:
    """Server-sent events until the run reaches a terminal or awaiting-review state.

    Implemented by polling the same two numbers `/status` returns every
    `APP_SSE_POLL_INTERVAL_SECONDS` and emitting only on change — a
    `state_change` when the status moves, a `progress` when the reaction count
    does. That keeps the transport a plain DB read (no Temporal query, no
    pub/sub broker to operate) at the cost of up-to-one-interval latency,
    which is the right trade for a progress bar. A terminal event closes the
    stream; `APP_SSE_MAX_DURATION_SECONDS` caps an abandoned connection.
    """
    run = await _load_run(session, run_id, current_user.id)

    _CLOSING = _TERMINAL | {S.AWAITING_REVIEW}
    _EVENT_FOR = {
        S.AWAITING_REVIEW: RunEventType.AWAITING_REVIEW,
        S.FINALIZED: RunEventType.COMPLETED,
        S.FAILED: RunEventType.FAILED,
    }

    def sse(event: RunEvent) -> str:
        return f"event: {event.type.value}\ndata: {event.model_dump_json()}\n\n"

    def build(row: RunRow, done: int, total: int, type_: RunEventType) -> RunEvent:
        return RunEvent(
            type=type_,
            run_id=row.id,
            status=row.status.value,
            reactions_done=done,
            reactions_total=total,
            at=datetime.now(tz=UTC),
        )

    async def generator() -> AsyncIterator[str]:
        last_status = run.status
        last_done, total = await _progress(session, run)
        # Emit the current state immediately so a late subscriber isn't
        # staring at an empty stream for a full poll interval.
        yield sse(build(run, last_done, total, _EVENT_FOR.get(run.status, RunEventType.PROGRESS)))
        if run.status in _CLOSING:
            return

        deadline = (
            asyncio.get_running_loop().time() + settings.sse_max_duration_seconds
        )
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(settings.sse_poll_interval_seconds)
            row = await session.get(RunRow, run_id)
            if row is None:  # deleted mid-stream
                return
            await session.refresh(row)
            done, total = await _progress(session, row)

            if row.status != last_status:
                last_status = row.status
                yield sse(
                    build(
                        row,
                        done,
                        total,
                        _EVENT_FOR.get(row.status, RunEventType.STATE_CHANGE),
                    )
                )
                if row.status in _CLOSING:
                    return
            elif done != last_done:
                yield sse(build(row, done, total, RunEventType.PROGRESS))
            else:
                # Comment frame: keeps proxies from reaping an idle connection.
                yield ": keep-alive\n\n"
            last_done = done

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/results", response_model=RunResultsOut, operation_id="getRunResults")
async def get_run_results(
    run_id: uuid.UUID, session: DbSession, current_user: CurrentUser
) -> RunResultsOut:
    """The ranked recommendation, the frozen metrics, and the narrative report.

    These become available at two different points, which matters for a UI:

    * `ranking` (runs.run_message_results) lands when scoring finishes,
      *before* the run pauses at `awaiting_review`. It carries rank,
      Bradley-Terry strength, aggregate score and recommendation per claim —
      everything a reviewer needs for the Gate 2 decision — and it persists
      even if they reject.
    * `report` and `baseline_lift_pct` (runs.run_reports) land only *after*
      Gate 2 approval — generating the narrative costs two LLM calls, so a
      rejected or expired run never has one.

    Never errors for a run without results; returns the empty shape instead.
    """
    run = await _load_run(session, run_id, current_user.id)

    ranking_rows = (
        await session.execute(
            select(RunMessageResult, Message.text)
            .join(Message, Message.id == RunMessageResult.message_id)
            .where(RunMessageResult.run_id == run_id)
            .order_by(RunMessageResult.rank)
        )
    ).all()

    report_row = await session.get(RunReport, run_id)

    return RunResultsOut(
        run_id=run_id,
        status=run.status,
        ranking=[
            RankingEntryOut(
                message_id=rmr.message_id,
                text=text_,
                rank=rmr.rank,
                bt_strength=float(rmr.bt_strength) if rmr.bt_strength is not None else None,
                aggregate_score=float(rmr.aggregate_score)
                if rmr.aggregate_score is not None
                else None,
                recommendation=rmr.recommendation,
            )
            for rmr, text_ in ranking_rows
        ],
        report=report_row.report if report_row else None,
        baseline_lift_pct=(
            float(report_row.baseline_lift_pct)
            if report_row and report_row.baseline_lift_pct is not None
            else None
        ),
    )
