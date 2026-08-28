"""The durable orchestrator. Owns every status write for its run — the API
only starts it and later sends approve/reject signals. If the worker
crashes mid-run, Temporal replays this function's history on a new worker
and resumes exactly where it left off; completed activities are NOT
re-executed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.activities import (
        AnchorSet,
        ApplyPenaltiesBatchInput,
        EmbedBatchInput,
        GenerateReactionBatchInput,
        Pair,
        Penalty,
        PenaltyResult,
        ReactionRow,
        ScoreBatchInput,
        ScoreResult,
        StudyContext,
        UpdateRunStatusInput,
        apply_penalties_batch,
        embed_batch,
        generate_reaction_batch,
        score_batch,
    )
    from app.config import settings

RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1), backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30), maximum_attempts=5,
)


@dataclass
class StudyRunInput:
    run_id: str
    study_id: str
    batch_size: int = 50
    delta: float = 0.02
    max_batches_per_run: int = 1000
    anchors: AnchorSet | None = None
    remaining_pairs: list[Pair] | None = None
    processed_so_far: int = 0
    kbq: str | None = None
    penalties: list[Penalty] | None = None


@dataclass
class StudyRunResult:
    total_pairs_scored: int
    final_status: str


@workflow.defn(name="study_run_workflow")
class StudyRunWorkflow:
    def __init__(self) -> None:
        self._decision: str | None = None
        self._decision_note: str | None = None
        self._processed = 0
        self._total = 0

    @workflow.signal
    async def approve(self, note: str | None = None) -> None:
        self._decision, self._decision_note = "approve", note

    @workflow.signal
    async def reject(self, note: str | None = None) -> None:
        self._decision, self._decision_note = "reject", note

    @workflow.query
    def progress(self) -> dict:
        return {"processed": self._processed, "total": self._total}

    @workflow.run
    async def run(self, input: StudyRunInput) -> StudyRunResult:
        is_first_hop = input.anchors is None
        anchor_vectors = None

        if is_first_hop:
            await workflow.execute_activity(
                "update_run_status",
                UpdateRunStatusInput(run_id=input.run_id, status="running",
                                     started_at=workflow.now().isoformat()),
                start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY,
            )
            try:
                context: StudyContext = await workflow.execute_activity(
                    "fetch_study_context", args=[input.run_id, input.study_id],
                    result_type=StudyContext,
                    start_to_close_timeout=timedelta(seconds=30), retry_policy=RETRY,
                )
            except Exception as exc:
                await self._fail(input.run_id, str(exc))
                raise

            anchor_vectors = await workflow.execute_activity(
                embed_batch, EmbedBatchInput(texts=context.anchors.texts),
                start_to_close_timeout=timedelta(seconds=30), retry_policy=RETRY,
            )
            input.anchors = context.anchors
            input.remaining_pairs = context.pairs
            input.kbq = context.kbq
            input.penalties = context.penalties
            self._total = len(context.pairs)

        anchors = input.anchors
        kbq = input.kbq or ""
        penalties = input.penalties or []
        remaining = input.remaining_pairs or []
        processed = input.processed_so_far
        self._processed = processed
        self._total = self._total or (processed + len(remaining))

        if anchor_vectors is None:
            anchor_vectors = await workflow.execute_activity(
                embed_batch, EmbedBatchInput(texts=anchors.texts),
                start_to_close_timeout=timedelta(seconds=30), retry_policy=RETRY,
            )

        batches_this_run = 0
        try:
            while remaining and batches_this_run < input.max_batches_per_run:
                batch, remaining = remaining[: input.batch_size], remaining[input.batch_size :]

                texts = await workflow.execute_activity(
                    generate_reaction_batch, GenerateReactionBatchInput(pairs=batch, kbq=kbq),
                    start_to_close_timeout=timedelta(seconds=180), retry_policy=RETRY,
                    heartbeat_timeout=timedelta(seconds=30),
                )
                vectors = await workflow.execute_activity(
                    embed_batch, EmbedBatchInput(texts=texts),
                    start_to_close_timeout=timedelta(seconds=60), retry_policy=RETRY,
                )
                scores: list[ScoreResult] = await workflow.execute_activity(
                    score_batch,
                    ScoreBatchInput(response_vectors=vectors, anchor_vectors=anchor_vectors,
                                     anchor_scale_points=anchors.scale_points, delta=input.delta),
                    start_to_close_timeout=timedelta(seconds=30), retry_policy=RETRY,
                )
                penalty_results: list[PenaltyResult] = await workflow.execute_activity(
                    apply_penalties_batch,
                    ApplyPenaltiesBatchInput(texts=texts, scores=scores, penalties=penalties),
                    start_to_close_timeout=timedelta(seconds=30), retry_policy=RETRY,
                )

                rows = [
                    ReactionRow(avatar_id=p.avatar_id, message_id=p.message_id,
                                score=pr.final_score, distribution=s.pmf, penalty=pr.penalty,
                                text=t, status="ok")
                    for p, s, pr, t in zip(batch, scores, penalty_results, texts)
                ]
                await workflow.execute_activity(
                    "persist_reactions", args=[input.run_id, rows],
                    start_to_close_timeout=timedelta(seconds=30), retry_policy=RETRY,
                )

                processed += len(batch)
                self._processed = processed
                batches_this_run += 1
                workflow.logger.info("batch %s done — %s/%s scored", batches_this_run, processed, self._total)
        except Exception as exc:
            await self._fail(input.run_id, str(exc))
            raise

        if remaining:
            workflow.continue_as_new(StudyRunInput(
                run_id=input.run_id, study_id=input.study_id,
                batch_size=input.batch_size, delta=input.delta,
                max_batches_per_run=input.max_batches_per_run,
                anchors=anchors, remaining_pairs=remaining, processed_so_far=processed,
                kbq=kbq, penalties=penalties,
            ))

        await workflow.execute_activity(
            "rollup_message_results", input.run_id,
            start_to_close_timeout=timedelta(seconds=30), retry_policy=RETRY,
        )
        await workflow.execute_activity(
            "generate_run_report", input.run_id,
            start_to_close_timeout=timedelta(seconds=120), retry_policy=RETRY,
        )
        await workflow.execute_activity(
            "update_run_status",
            UpdateRunStatusInput(run_id=input.run_id, status="awaiting_review"),
            start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY,
        )

        await workflow.wait_condition(
            lambda: self._decision is not None,
            timeout=timedelta(days=settings.approval_timeout_days),
        )

        final_status = "finalized" if self._decision == "approve" else "cancelled"
        await workflow.execute_activity(
            "update_run_status",
            UpdateRunStatusInput(
                run_id=input.run_id, status=final_status,
                finished_at=workflow.now().isoformat(),
                error=None if self._decision == "approve" else {"reason": "rejected", "note": self._decision_note},
            ),
            start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY,
        )

        return StudyRunResult(total_pairs_scored=processed, final_status=final_status)

    async def _fail(self, run_id: str, message: str) -> None:
        await workflow.execute_activity(
            "update_run_status",
            UpdateRunStatusInput(run_id=run_id, status="failed",
                                  finished_at=workflow.now().isoformat(), error={"message": message}),
            start_to_close_timeout=timedelta(seconds=15),
        )