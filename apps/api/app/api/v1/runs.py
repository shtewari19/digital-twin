"""Endpoints for creating, starting, and approving/rejecting study runs."""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.core.temporal import STUDY_RUN_WORKFLOW_NAME, get_temporal_client
from app.db.models.message import Message
from app.db.models.run import Run, RunStatus
from app.db.models.run_message_result import RunMessageResult
from app.db.models.run_report import RunReport
from app.schemas.run import RankingEntryOut, RunOut, RunResultsOut

router = APIRouter()
log = logging.getLogger("api.runs")


@router.post("/studies/{study_id}/runs", response_model=RunOut, status_code=201)
async def create_run(
    study_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Run:
    run = Run(study_id=study_id, status=RunStatus.DRAFT)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    log.info("run %s created for study %s -> %s", run.id, study_id, RunStatus.DRAFT.value)
    return run


@router.post("/runs/{run_id}/start", response_model=RunOut)
async def start_run(run_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> Run:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status != RunStatus.DRAFT:
        raise HTTPException(status_code=409, detail=f"run is {run.status.value}, expected draft")

    run.status = RunStatus.QUEUED
    await db.commit()
    log.info("run %s -> %s", run_id, RunStatus.QUEUED.value)

    client = get_temporal_client()
    workflow_id = f"study-run-{run_id}"
    await client.start_workflow(
        STUDY_RUN_WORKFLOW_NAME,
        {"run_id": str(run_id), "study_id": str(run.study_id)},
        id=workflow_id,
        task_queue=settings.task_queue,
    )

    # NOTE: no longer set to RUNNING here — the workflow's own first activity
    # (update_run_status) does that now, so it's true even if the API
    # process dies the instant after this call returns.
    run.workflow_id = workflow_id
    await db.commit()
    await db.refresh(run)
    log.info("run %s submitted to Temporal (workflow_id=%s)", run_id, workflow_id)
    return run


@router.post("/runs/{run_id}/approve", response_model=RunOut, status_code=202)
async def approve_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    note: str | None = None,
) -> Run:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status != RunStatus.AWAITING_REVIEW:
        raise HTTPException(status_code=409, detail=f"run is {run.status.value}, expected awaiting_review")

    handle = get_temporal_client().get_workflow_handle(run.workflow_id)
    await handle.signal("approve", note)
    log.info("run %s: approve signal sent", run_id)
    return run


@router.post("/runs/{run_id}/reject", response_model=RunOut, status_code=202)
async def reject_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    note: str | None = None,
) -> Run:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status != RunStatus.AWAITING_REVIEW:
        raise HTTPException(status_code=409, detail=f"run is {run.status.value}, expected awaiting_review")

    handle = get_temporal_client().get_workflow_handle(run.workflow_id)
    await handle.signal("reject", note)
    log.info("run %s: reject signal sent", run_id)
    return run


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> Run:
    """Not strictly part of this ticket, but you'll want it for testing —
    lets you poll a run's status via curl instead of only checking the DB."""
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.get("/runs/{run_id}/results", response_model=RunResultsOut)
async def get_run_results(
    run_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> RunResultsOut:
    """The ranked recommendation + narrative report apps/engine writes
    (runs.run_message_results, runs.run_reports). Both are empty/null until
    the run reaches at least awaiting_review — this route doesn't error in
    that case, it just returns an empty ranking and a null report."""
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    ranking_rows = (
        await db.execute(
            select(RunMessageResult, Message.text)
            .join(Message, Message.id == RunMessageResult.message_id)
            .where(RunMessageResult.run_id == run_id)
            .order_by(RunMessageResult.rank)
        )
    ).all()

    report_row = await db.get(RunReport, run_id)

    return RunResultsOut(
        run_id=run_id,
        status=run.status,
        ranking=[
            RankingEntryOut(
                message_id=rmr.message_id,
                text=text,
                rank=rmr.rank,
                bt_strength=float(rmr.bt_strength) if rmr.bt_strength is not None else None,
                aggregate_score=float(rmr.aggregate_score) if rmr.aggregate_score is not None else None,
                recommendation=rmr.recommendation,
            )
            for rmr, text in ranking_rows
        ],
        report=report_row.report if report_row else None,
        baseline_lift_pct=(
            float(report_row.baseline_lift_pct)
            if report_row and report_row.baseline_lift_pct is not None
            else None
        ),
    )