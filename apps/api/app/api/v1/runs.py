"""Endpoints for creating and starting study runs."""

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.temporal import get_temporal_client, STUDY_RUN_WORKFLOW_NAME
from app.core.config import settings
from app.db.models.run import Run, RunStatus
from app.db.session import SessionLocal
from app.schemas.run import RunOut

router = APIRouter()
log = logging.getLogger("api.runs")


@router.post("/studies/{study_id}/runs", response_model=RunOut, status_code=201)
async def create_run(study_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Run:
    run = Run(study_id=study_id, status=RunStatus.DRAFT)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    log.info("run %s created for study %s -> %s", run.id, study_id, RunStatus.DRAFT.value)
    return run


@router.post("/runs/{run_id}/start", response_model=RunOut)
async def start_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Run:
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
        str(run.study_id),
        id=workflow_id,
        task_queue=settings.task_queue,
    )

    run.status = RunStatus.RUNNING
    run.workflow_id = workflow_id
    await db.commit()
    await db.refresh(run)
    log.info("run %s -> %s (workflow_id=%s)", run_id, RunStatus.RUNNING.value, workflow_id)

    asyncio.create_task(_finalize_when_done(run_id, workflow_id))
    return run


async def _finalize_when_done(run_id: uuid.UUID, workflow_id: str) -> None:
    client = get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        try:
            await handle.result()
            run.status = RunStatus.FINALIZED
            log.info("run %s -> %s", run_id, RunStatus.FINALIZED.value)
        except Exception:
            log.exception("run %s failed", run_id)
            run.status = RunStatus.FAILED
        await db.commit()