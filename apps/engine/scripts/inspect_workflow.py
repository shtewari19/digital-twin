"""Inspect what Temporal actually did for a run.

The database tells you the *outcome*; this tells you the *orchestration* —
which activities ran, in what order, how many attempts each took, whether the
Gate 2 timer fired, and which signals arrived. It is the command-line
equivalent of opening the workflow in the Temporal UI (localhost:8080), and
the fastest way to answer "why is this run stuck".

Usage (from apps/engine/):

    uv run python scripts/inspect_workflow.py
    uv run python scripts/inspect_workflow.py --run-id <uuid>
    uv run python scripts/inspect_workflow.py --raw      # every history event
"""

from __future__ import annotations

import argparse
import asyncio
import collections

from temporalio.api.enums.v1 import EventType
from temporalio.client import Client

from app.config import settings

DEFAULT_RUN = "40000000-0000-0000-0000-000000000001"

#: The five activities the batch loop repeats, once per batch of `batch_size`
#: pairs. A 100-reaction run at batch_size=50 runs this twice.
BATCH_STEPS = [
    "generate_reaction_batch",
    "embed_batch",
    "score_batch",
    "apply_penalties_batch",
    "persist_reactions",
]
PRELUDE = ["update_run_status", "fetch_study_context", "embed_batch"]  # -> running, context, anchors
#: After the loop: ranking (before the gate), awaiting_review, then — only if a
#: human approves — the report, then finalized.
APPROVED_TAIL = ["rollup_message_results", "update_run_status", "generate_run_report", "update_run_status"]
#: A rejected run stops at the gate: the ranking is written, no report is.
REJECTED_TAIL = ["rollup_message_results", "update_run_status", "update_run_status"]


def _describe_sequence(actual: list[str]) -> str:
    """Match the activity list against the expected shape for any batch count."""
    if actual[: len(PRELUDE)] != PRELUDE:
        return "  NOTE: prelude differs from the expected update_run_status/fetch_study_context/embed_batch."
    body = actual[len(PRELUDE) :]
    for tail, label in ((APPROVED_TAIL, "approved"), (REJECTED_TAIL, "rejected")):
        if len(body) < len(tail):
            continue
        loop, got_tail = body[: len(body) - len(tail)], body[len(body) - len(tail) :]
        if got_tail != tail or len(loop) % len(BATCH_STEPS):
            continue
        batches = len(loop) // len(BATCH_STEPS)
        if all(loop[i * 5 : i * 5 + 5] == BATCH_STEPS for i in range(batches)):
            plural = "" if batches == 1 else "es"
            if label == "approved":
                return (
                    f"  sequence matches an APPROVED run over {batches} batch{plural}.\n"
                    "  (ranking before the gate; report only after approval)"
                )
            return (
                f"  sequence matches a REJECTED run over {batches} batch{plural}: "
                "ranking written, no report generated."
            )
    return "  NOTE: sequence differs from the expected shape — check for retries or a cancel mid-batch."


def _name(event_type: int) -> str:
    return EventType.Name(event_type).replace("EVENT_TYPE_", "")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", default=DEFAULT_RUN)
    ap.add_argument("--raw", action="store_true", help="print every history event")
    args = ap.parse_args()

    workflow_id = f"study-run-{args.run_id}"
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    handle = client.get_workflow_handle(workflow_id)

    desc = await handle.describe()

    # A run that hopped via continue_as_new is a CHAIN of executions sharing one
    # workflow id. describe() and fetch_history_events() only see the latest,
    # so walk backwards to report the real length — on a large panel most of the
    # work sits in earlier links.
    chain, run_id, guard = [], desc.run_id, 0
    while run_id and guard < 200:
        guard += 1
        chain.append(run_id)
        prev = None
        async for e in client.get_workflow_handle(workflow_id, run_id=run_id).fetch_history_events():
            if _name(e.event_type) == "WORKFLOW_EXECUTION_STARTED":
                prev = e.workflow_execution_started_event_attributes.continued_execution_run_id or None
            break
        run_id = prev
    chain.reverse()

    print(f"workflow id : {desc.id}")
    print(f"run id      : {desc.run_id}")
    print(f"type        : {desc.workflow_type}")
    print(f"task queue  : {desc.task_queue}")
    print(f"status      : {desc.status.name}")
    if desc.close_time:
        print(f"duration    : {(desc.close_time - desc.start_time).total_seconds():.1f}s")
    else:
        print("duration    : still running")

    scheduled: dict[int, tuple[str, int, int]] = {}
    order: list[int] = []
    attempts: dict[int, int] = {}
    failures: list[tuple[str, str]] = []
    signals: list[str] = []
    kinds: collections.Counter = collections.Counter()

    async for event in handle.fetch_history_events():
        kind = _name(event.event_type)
        kinds[kind] += 1
        if args.raw:
            print(f"  [{event.event_id:>3}] {kind}")

        if kind == "ACTIVITY_TASK_SCHEDULED":
            a = event.activity_task_scheduled_event_attributes
            scheduled[event.event_id] = (
                a.activity_type.name,
                a.start_to_close_timeout.seconds,
                a.retry_policy.maximum_attempts,
            )
            order.append(event.event_id)
        elif kind == "ACTIVITY_TASK_STARTED":
            a = event.activity_task_started_event_attributes
            attempts[a.scheduled_event_id] = a.attempt
        elif kind == "ACTIVITY_TASK_FAILED":
            a = event.activity_task_failed_event_attributes
            name = scheduled.get(a.scheduled_event_id, ("?",))[0]
            failures.append((name, a.failure.message[:120]))
        elif kind == "WORKFLOW_EXECUTION_SIGNALED":
            signals.append(event.workflow_execution_signaled_event_attributes.signal_name)

    print(f"\nactivities ({len(order)}):")
    for i, eid in enumerate(order, start=1):
        name, timeout, max_attempts = scheduled[eid]
        used = attempts.get(eid, 0)
        flag = "" if used <= 1 else f"   <- RETRIED {used}x"
        print(f"  {i:>2}. {name:<24s} timeout={timeout:>4}s  max_attempts={max_attempts}  attempt={used}{flag}")

    actual = [scheduled[e][0] for e in order]
    print()
    if len(chain) > 1:
        print(
            f"  (one link of a {len(chain)}-execution continue_as_new chain — the "
            "sequence below is this link only)"
        )
    print(_describe_sequence(actual))

    print(f"\nsignals received     : {signals or 'none'}")
    if failures:
        print(f"activity failures    : {len(failures)}")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
    else:
        print("activity failures    : 0  (every activity succeeded on its first attempt)")

    timers_started = kinds.get("TIMER_STARTED", 0)
    timers_cancelled = kinds.get("TIMER_CANCELED", 0)
    print(f"Gate 2 review timer  : {timers_started} started, {timers_cancelled} cancelled", end="")
    if timers_started and timers_cancelled:
        print("  (a signal released the wait before it expired)")
    elif timers_started:
        print("  (timer fired -> the run should be 'expired')")
    else:
        print("  (gate disabled, or a decision was already in)")
    if len(chain) > 1:
        print(
            f"continue_as_new      : {len(chain) - 1} hop(s) — this is execution "
            f"{len(chain)} of {len(chain)}; the activities above are THIS link only"
        )
        print(f"                       chain: {' -> '.join(r[:8] for r in chain)}")
    else:
        print("continue_as_new      : 0 hops (the whole run fits one history)")
    print(f"history events (this execution): {sum(kinds.values())}")


if __name__ == "__main__":
    asyncio.run(main())
