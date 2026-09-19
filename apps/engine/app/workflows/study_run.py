"""The durable orchestrator. Owns every status write for its run from
`running` onward — the API only starts it and then drives it with signals
(`finalize`, `cancel`). If the worker crashes mid-run, Temporal
replays this function's history on a new worker and resumes exactly where
it left off; completed activities are NOT re-executed.

Ordering around Gate 2 matters. Scoring finishes, the Bradley-Terry ranking
is written to `runs.run_message_results`, and only THEN does the workflow park
for review — so the numbers a reviewer judges are durable before anyone is
asked to judge them. The narrative report is generated *after* a human
approves, so a rejected or expired run keeps its ranking but never pays for
the two report LLM calls.

Gate 2 (results review) is a real blocking wait: after the ranking is
written the workflow parks at `awaiting_review` on
`workflow.wait_condition` until `POST /runs/{id}/finalize` or
`.../cancel` signals it, or `review_timeout_seconds` elapses and the run
lands on `expired`. Cancelling from `awaiting_review` is how results are
rejected — they stay in the database, they just never finalize. Set `review_gate_enabled=False` (the API passes
`APP_RUN_REVIEW_GATE_ENABLED`) to skip the wait and auto-finalize, which
is what load tests and unattended environments want.

Gate 1 (config approval) has no representation here by design — it
happens entirely in the API before `start_workflow` is ever called."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.activities import (
        AnchorSet,
        ApplyPenaltiesBatchInput,
        EmbedBatchInput,
        GenerateReactionBatchInput,
        Penalty,
        PenaltyResult,
        ReactionResult,
        ReactionRow,
        ScoreBatchInput,
        ScoreResult,
        StudyContext,
        UpdateRunStatusInput,
        apply_penalties_batch,
        embed_batch,
        generate_reaction_batch,
        pairs_for_slice,
        score_batch,
    )

RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1), backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30), maximum_attempts=5,
)


@dataclass
class StudyRunInput:
    """Workflow input — and the state carried across `continue_as_new`.

    Deliberately CONSTANT-SIZE with respect to the panel. It holds the avatar
    ids, the claims and a cursor, never the expanded cross product: a run of
    100 reactions and a run of 100,000 carry the same few KB. Materialising the
    pairs here instead put a 50-respondent run past Temporal's 2 MB payload
    limit, because each pair inlined a ~2.5 KB persona prompt.
    """

    run_id: str
    study_id: str
    #: Reactions per batch. Each batch is one pass of generate → embed → score
    #: → penalise → persist.
    batch_size: int = 50
    delta: float = 0.02
    #: Batches before the workflow calls `continue_as_new` to start a fresh
    #: history. Keeps history bounded on long runs; each batch adds roughly
    #: one persist payload (batch_size reactions of text) to it.
    max_batches_per_run: int = 20
    anchors: AnchorSet | None = None
    #: The sources the cross product is expanded from, on demand.
    avatar_ids: list[str] | None = None
    claims: list[tuple[str, str]] | None = None
    respondents_per_avatar: int = 1
    #: Cursor into the cross product: how many reactions are already done.
    processed_so_far: int = 0
    kbq: str | None = None
    penalties: list[Penalty] | None = None
    #: Gate 2. When False the workflow finalizes without waiting for a human.
    review_gate_enabled: bool = True
    #: How long to park at awaiting_review before giving up -> `expired`.
    review_timeout_seconds: int = 86_400


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
        self._cancel_requested = False
        self._cancel_note: str | None = None
        self._awaiting_review = False
        #: Set once the ranking is written, so a `status` query can show what
        #: the reviewer is being asked to approve. Read from the rollup's
        #: return value — not stored anywhere, runs.run_message_results is the
        #: record of it.
        self._predicted_winner: str | None = None

    @workflow.signal
    async def finalize(self, note: str | None = None) -> None:
        """Gate 2 approval — POST /runs/{id}/finalize. Releases the review
        wait and drives the run to `finalized`."""
        self._decision, self._decision_note = "approve", note

    @workflow.signal(name="approve")
    async def approve(self, note: str | None = None) -> None:
        """Kept as an alias of `finalize` so a client (or an in-flight signal
        from a pre-rename deployment) still lands on the same decision."""
        self._decision, self._decision_note = "approve", note

    @workflow.signal
    async def reject(self, note: str | None = None) -> None:
        """Gate 2 rejection. No longer has an API route of its own — the
        contract's stop endpoint is POST /runs/{id}/cancel, and cancelling
        from awaiting_review is how results are rejected. Kept as a signal so
        a Temporal-level client (or an in-flight signal from an older
        deployment) still resolves to the same outcome: results stay in the
        database, the run ends `cancelled` rather than `finalized`."""
        self._decision, self._decision_note = "reject", note

    @workflow.signal
    async def cancel(self, note: str | None = None) -> None:
        """Cooperative cancel — POST /runs/{id}/cancel, the single stop
        mechanism, valid from any non-terminal state.

        The flag is checked at every point the workflow can safely stop:
        before the first batch, after each batch is persisted, before the
        rollup, before the report, before the awaiting_review write, and
        inside the Gate 2 review wait. Unlike Temporal's built-in
        cancel/terminate that lets the in-flight activity finish, so
        everything already scored stays in runs.run_reactions and the run
        lands on a clean 'cancelled' + finished_at instead of an abrupt kill.
        `?force=true` on that endpoint is the abrupt kill, for a run that
        won't stop on its own."""
        self._cancel_requested = True
        self._cancel_note = note

    @workflow.query
    def progress(self) -> dict:
        return {"processed": self._processed, "total": self._total}

    @workflow.query
    def status(self) -> dict:
        """Everything GET /runs/{id}/status might want straight from the
        workflow. The API answers that route from Postgres instead (it works
        with no worker up), but this is the ground truth when debugging a run
        whose DB projection looks wrong."""
        return {
            "processed": self._processed,
            "total": self._total,
            "awaiting_review": self._awaiting_review,
            "decision": self._decision,
            "cancel_requested": self._cancel_requested,
            "predicted_winner": self._predicted_winner,
        }

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

            total = (
                len(context.avatar_ids) * context.respondents_per_avatar * len(context.claims)
            )
            workflow.logger.info(
                "context loaded from config_snapshot — %s personas x %s respondents "
                "x %s claims = %s reactions, %s anchors, %s penalties; embedding anchors",
                len(context.avatar_ids), context.respondents_per_avatar,
                len(context.claims), total,
                len(context.anchors.texts), len(context.penalties),
            )
            if total == 0:
                await self._fail(input.run_id, "config_snapshot produced 0 reactions to run")
                raise ApplicationError("config_snapshot produced 0 reactions to run", non_retryable=True)

            anchor_vectors = await workflow.execute_activity(
                embed_batch, EmbedBatchInput(texts=context.anchors.texts),
                start_to_close_timeout=timedelta(seconds=120), retry_policy=RETRY,
                heartbeat_timeout=timedelta(seconds=90),
            )
            input.anchors = context.anchors
            input.avatar_ids = context.avatar_ids
            input.claims = context.claims
            input.respondents_per_avatar = context.respondents_per_avatar
            input.kbq = context.kbq
            input.penalties = context.penalties

        anchors = input.anchors
        kbq = input.kbq or ""
        penalties = input.penalties or []
        avatar_ids = input.avatar_ids or []
        claims = input.claims or []
        respondents = input.respondents_per_avatar
        processed = input.processed_so_far
        self._processed = processed
        self._total = len(avatar_ids) * respondents * len(claims)

        if anchor_vectors is None:
            anchor_vectors = await workflow.execute_activity(
                embed_batch, EmbedBatchInput(texts=anchors.texts),
                start_to_close_timeout=timedelta(seconds=120), retry_policy=RETRY,
                heartbeat_timeout=timedelta(seconds=90),
            )

        if self._cancel_requested:
            await self._cancel(input.run_id)
            return StudyRunResult(total_pairs_scored=processed, final_status="cancelled")

        batches_this_run = 0
        try:
            while processed < self._total and batches_this_run < input.max_batches_per_run:
                if self._cancel_requested:
                    await self._cancel(input.run_id)
                    return StudyRunResult(total_pairs_scored=processed, final_status="cancelled")

                # Expand only this batch — the workflow never holds the whole
                # cross product, which is what keeps its payload constant.
                batch = pairs_for_slice(
                    avatar_ids, claims, respondents, processed, input.batch_size
                )
                workflow.logger.info(
                    "batch %s: generating %s reactions (LLM) — %s/%s done",
                    batches_this_run + 1, len(batch), processed, self._total,
                )

                reactions: list[ReactionResult] = await workflow.execute_activity(
                    generate_reaction_batch, GenerateReactionBatchInput(pairs=batch, kbq=kbq),
                    start_to_close_timeout=timedelta(seconds=180), retry_policy=RETRY,
                    heartbeat_timeout=timedelta(seconds=30),
                )
                # A reaction that could not be generated keeps its slot so every
                # list below stays positionally aligned; it is embedded and
                # scored as a placeholder and then discarded at persist time.
                n_failed = sum(1 for r in reactions if not r.ok)
                if n_failed:
                    workflow.logger.warning(
                        "batch %s: %s/%s reactions failed to generate — stored as "
                        "status='failed' and excluded from the ranking",
                        batches_this_run + 1, n_failed, len(reactions),
                    )
                texts = [r.text if r.ok else "(generation failed)" for r in reactions]
                # Generous start_to_close because embed_batch chunks its HTTP
                # requests (the service caps a request at ~25 KB), so a batch
                # of 50 reactions is several round trips of ~20s each. The
                # heartbeat is what actually catches a hang: the activity beats
                # once per chunk, so 90s of silence means it is genuinely
                # stuck rather than merely slow.
                vectors = await workflow.execute_activity(
                    embed_batch, EmbedBatchInput(texts=texts),
                    start_to_close_timeout=timedelta(seconds=300), retry_policy=RETRY,
                    heartbeat_timeout=timedelta(seconds=90),
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
                    ReactionRow(
                        avatar_id=p.avatar_id, message_id=p.message_id,
                        respondent=p.respondent,
                        # A failed reaction stores no score, distribution or
                        # text — rollup_message_results filters on
                        # status='ok' AND score IS NOT NULL, so it simply does
                        # not vote.
                        score=pr.final_score if r.ok else None,
                        distribution=s.pmf if r.ok else None,
                        penalty=pr.penalty if r.ok else None,
                        text=r.text if r.ok else None,
                        status="ok" if r.ok else "failed",
                    )
                    for p, s, pr, r in zip(batch, scores, penalty_results, reactions, strict=True)
                ]
                await workflow.execute_activity(
                    "persist_reactions", args=[input.run_id, rows],
                    start_to_close_timeout=timedelta(seconds=30), retry_policy=RETRY,
                )

                processed += len(batch)
                self._processed = processed
                batches_this_run += 1
                pct = round(processed / self._total * 100, 1) if self._total else 0.0
                workflow.logger.info(
                    "batch %s persisted — %s/%s reactions scored (%.1f%%), %s remaining",
                    batches_this_run, processed, self._total, pct, self._total - processed,
                )
        except Exception as exc:
            await self._fail(input.run_id, str(exc))
            raise

        if processed < self._total and self._cancel_requested:
            # Cancelled on the last batch of this execution: stop here rather
            # than carrying the cursor into a fresh history.
            await self._cancel(input.run_id)
            return StudyRunResult(total_pairs_scored=processed, final_status="cancelled")

        if processed < self._total:
            workflow.logger.info(
                "history hop: %s/%s done after %s batches — continue_as_new",
                processed, self._total, batches_this_run,
            )
            workflow.continue_as_new(StudyRunInput(
                run_id=input.run_id, study_id=input.study_id,
                batch_size=input.batch_size, delta=input.delta,
                max_batches_per_run=input.max_batches_per_run,
                anchors=anchors, avatar_ids=avatar_ids, claims=claims,
                respondents_per_avatar=respondents, processed_so_far=processed,
                kbq=kbq, penalties=penalties,
                review_gate_enabled=input.review_gate_enabled,
                review_timeout_seconds=input.review_timeout_seconds,
            ))

        # The batch loop is done. Everything below still honours a cancel —
        # the rollup and the metrics write are quick, but a cancel that lands
        # here must not be deferred to the review gate.
        if self._cancel_requested:
            await self._cancel(input.run_id)
            return StudyRunResult(total_pairs_scored=processed, final_status="cancelled")

        # --- Compute the ranking, BEFORE the gate ---------------------------
        # This is the run's result record: rollup_message_results writes rank,
        # Bradley-Terry strength, aggregate score and recommendation per claim
        # into runs.run_message_results. It is committed before the workflow
        # pauses for review, so the numbers a reviewer judges are already
        # durable and survive a rejection, an expiry, or a worker restart.
        workflow.logger.info("all %s pairs scored — computing Bradley-Terry ranking", processed)
        winner: dict = await workflow.execute_activity(
            "rollup_message_results", input.run_id,
            start_to_close_timeout=timedelta(seconds=30), retry_policy=RETRY,
        )
        self._predicted_winner = winner.get("text")
        workflow.logger.info(
            "ranking written to runs.run_message_results — winner: %s",
            (self._predicted_winner or "<none>")[:80],
        )

        if self._cancel_requested:
            await self._cancel(input.run_id)
            return StudyRunResult(total_pairs_scored=processed, final_status="cancelled")

        await workflow.execute_activity(
            "update_run_status",
            UpdateRunStatusInput(
                run_id=input.run_id, status="awaiting_review",
                coverage_pct=round(processed / self._total * 100, 2) if self._total else 0.0,
            ),
            start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY,
        )

        # --- Gate 2: human review of the results ---------------------------
        # A genuine blocking wait. Temporal holds the workflow here with no
        # process or thread pinned — the wait survives worker restarts because
        # it's server-side state, not an in-memory sleep. The run sits at
        # `awaiting_review` in Postgres the whole time, which is exactly what
        # the API reports.
        if input.review_gate_enabled and self._decision is None:
            self._awaiting_review = True
            workflow.logger.info(
                "GATE 2: parked at awaiting_review for up to %ss — send `finalize` to approve "
                "(report is generated only then) or `cancel` to reject",
                input.review_timeout_seconds,
            )
            decided = await self._wait_for_review(input.review_timeout_seconds)
            self._awaiting_review = False

            if not decided:
                # Nobody reviewed it in time. `expired` (not `failed`) — the
                # pipeline did its job; the human didn't come back.
                await workflow.execute_activity(
                    "update_run_status",
                    UpdateRunStatusInput(
                        run_id=input.run_id, status="expired",
                        finished_at=workflow.now().isoformat(),
                        error={"reason": "review_timeout",
                               "timeout_seconds": input.review_timeout_seconds},
                    ),
                    start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY,
                )
                return StudyRunResult(total_pairs_scored=processed, final_status="expired")

            if self._cancel_requested:
                await self._cancel(input.run_id)
                return StudyRunResult(total_pairs_scored=processed, final_status="cancelled")
        elif self._decision is None:
            # Gate disabled (APP_RUN_REVIEW_GATE_ENABLED=false): finalize
            # without waiting, the pre-gate behaviour.
            self._decision = "approve"

        # --- Report generation: AFTER the human approves --------------------
        # The two LLM calls that write the narrative only happen once a human
        # has signed off on the ranking. A rejected or expired run therefore
        # never pays for them — its ranking still stands in
        # runs.run_message_results, written before the gate, but no report.
        if self._decision == "approve":
            workflow.logger.info("approved — generating the narrative report (2 LLM calls)")
            await workflow.execute_activity(
                "generate_run_report", input.run_id,
                start_to_close_timeout=timedelta(seconds=120), retry_policy=RETRY,
            )
            workflow.logger.info("report written to runs.run_reports")
        else:
            workflow.logger.info("rejected — skipping report generation; metrics are kept")

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
        workflow.logger.info("run complete — final status: %s", final_status)

        return StudyRunResult(total_pairs_scored=processed, final_status=final_status)

    async def _wait_for_review(self, timeout_seconds: int) -> bool:
        """Block until a finalize (or reject) decision, or a cancel, arrives.

        Returns True if one did, False if `timeout_seconds` elapsed first.
        """
        try:
            await workflow.wait_condition(
                lambda: self._decision is not None or self._cancel_requested,
                timeout=timedelta(seconds=timeout_seconds),
            )
        except TimeoutError:
            return False
        return True

    async def _cancel(self, run_id: str) -> None:
        await workflow.execute_activity(
            "update_run_status",
            UpdateRunStatusInput(
                run_id=run_id, status="cancelled",
                finished_at=workflow.now().isoformat(),
                error={"reason": "cancelled", "note": self._cancel_note},
            ),
            start_to_close_timeout=timedelta(seconds=15), retry_policy=RETRY,
        )

    async def _fail(self, run_id: str, message: str) -> None:
        await workflow.execute_activity(
            "update_run_status",
            UpdateRunStatusInput(run_id=run_id, status="failed",
                                  finished_at=workflow.now().isoformat(), error={"message": message}),
            start_to_close_timeout=timedelta(seconds=15),
        )