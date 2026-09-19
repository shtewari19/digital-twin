"""Drive a study run's Temporal workflow from the command line.

Every Temporal-side action in one place: start it, check on it, release the
Gate 2 review, cancel it, or kill it. Use this instead of pasting inline
`python -c` snippets — those interpolate shell variables, and an unset `$RUN`
silently becomes an empty string, starting a workflow with `run_id=''` that
fails a few seconds later on an invalid UUID.

The run id defaults to the seeded hardcoded run, so the common case needs no
arguments at all.

Usage (from apps/engine/):

    uv run python scripts/workflow.py start        # begin the run
    uv run python scripts/workflow.py status       # where is it right now?
    uv run python scripts/workflow.py finalize     # Gate 2: accept the results
    uv run python scripts/workflow.py cancel       # graceful stop / reject
    uv run python scripts/workflow.py terminate    # hard kill (last resort)
    uv run python scripts/workflow.py list         # every workflow on the server

    # any of them against a different run
    uv run python scripts/workflow.py status --run-id <uuid>

`start` blocks and prints progress until the workflow parks at Gate 2 or
finishes; pass --no-wait to fire and return immediately.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError

from app.config import settings

DEFAULT_RUN = "40000000-0000-0000-0000-000000000001"
DEFAULT_STUDY = "50000000-0000-0000-0000-000000000001"
WORKFLOW_NAME = "study_run_workflow"  # must match @workflow.defn(name=...)


def workflow_id(run_id: str) -> str:
    """The deterministic id the API also uses, so both address the same
    execution and Temporal enforces one live workflow per run."""
    return f"study-run-{run_id}"


def _validate(label: str, value: str) -> str:
    """Fail loudly on a malformed id rather than letting Temporal start a
    workflow that dies mid-activity on an invalid UUID."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        sys.exit(
            f"error: --{label} is not a valid UUID: {value!r}\n"
            f"       (an empty value usually means a shell variable wasn't set)"
        )
    return value


async def connect() -> Client:
    return await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)


async def cmd_start(args: argparse.Namespace) -> int:
    run_id = _validate("run-id", args.run_id)
    study_id = _validate("study-id", args.study_id)
    client = await connect()

    try:
        handle = await client.start_workflow(
            WORKFLOW_NAME,
            {
                "run_id": run_id,
                "study_id": study_id,
                "review_gate_enabled": not args.no_gate,
                "review_timeout_seconds": args.review_timeout,
                "batch_size": args.batch_size,
                "max_batches_per_run": args.max_batches,
            },
            id=workflow_id(run_id),
            task_queue=settings.task_queue,
        )
    except Exception as exc:
        if "already started" in str(exc).lower():
            sys.exit(
                f"error: {workflow_id(run_id)} is already running.\n"
                f"       Terminate it first:  python scripts/workflow.py terminate\n"
                f"       Then reset the run:  psql ... < apps/api/scripts/reset_run.sql"
            )
        raise

    print(f"started {handle.id}")
    print(f"  task queue : {settings.task_queue}")
    gate = "disabled (auto-finalize)" if args.no_gate else f"enabled, {args.review_timeout}s timeout"
    print(f"  review gate: {gate}")
    print(f"  batching   : {args.batch_size}/batch, continue_as_new every {args.max_batches} batches")
    if args.no_wait:
        print("\nnot waiting (--no-wait). Check on it with:  python scripts/workflow.py status")
        return 0

    print("\nwatching (Ctrl-C to stop watching — the run keeps going):")
    last = None
    while True:
        await asyncio.sleep(5)
        try:
            state = await handle.query("status")
        except RPCError:
            break  # workflow closed
        line = f"  {state['processed']}/{state['total']} scored"
        if state["awaiting_review"]:
            line += "   <- PARKED AT GATE 2"
        if line != last:
            print(line)
            last = line
        if state["awaiting_review"]:
            print("\nthe workflow is blocked waiting for a decision. Release it with:")
            print("  python scripts/workflow.py finalize     # accept")
            print("  python scripts/workflow.py cancel       # reject (results are kept)")
            return 0

    result = await handle.result()
    print(f"\nfinished: {result}")
    return 0


async def cmd_status(args: argparse.Namespace) -> int:
    run_id = _validate("run-id", args.run_id)
    handle = (await connect()).get_workflow_handle(workflow_id(run_id))
    try:
        desc = await handle.describe()
    except RPCError as exc:
        sys.exit(f"error: no workflow {workflow_id(run_id)} — {exc.message}")

    print(f"workflow : {desc.id}")
    print(f"status   : {desc.status.name}")
    if desc.close_time:
        print(f"duration : {(desc.close_time - desc.start_time).total_seconds():.1f}s")

    if desc.status == WorkflowExecutionStatus.RUNNING:
        state = await handle.query("status")
        print(f"progress : {state['processed']}/{state['total']} scored")
        print(f"gate 2   : {'PARKED — waiting for finalize/cancel' if state['awaiting_review'] else 'not reached'}")
        if state["cancel_requested"]:
            print("cancel   : requested, stopping at the next safe point")
    else:
        try:
            print(f"result   : {await handle.result()}")
        except Exception as exc:  # a failed/cancelled run raises here; report it, don't crash
            print(f"result   : {type(exc).__name__}: {exc}")
    return 0


async def _signal(args: argparse.Namespace, name: str, note: str) -> int:
    run_id = _validate("run-id", args.run_id)
    handle = (await connect()).get_workflow_handle(workflow_id(run_id))
    try:
        await handle.signal(name, args.note or note)
    except RPCError as exc:
        sys.exit(
            f"error: could not signal {workflow_id(run_id)} — {exc.message}\n"
            f"       (the workflow has probably already closed; check `status`)"
        )
    print(f"{name} signal sent to {workflow_id(run_id)}")
    print("the workflow writes the new status itself, a moment later — poll `status` to see it.")
    return 0


async def cmd_finalize(args: argparse.Namespace) -> int:
    return await _signal(args, "finalize", "finalized via scripts/workflow.py")


async def cmd_cancel(args: argparse.Namespace) -> int:
    return await _signal(args, "cancel", "cancelled via scripts/workflow.py")


async def cmd_terminate(args: argparse.Namespace) -> int:
    run_id = _validate("run-id", args.run_id)
    handle = (await connect()).get_workflow_handle(workflow_id(run_id))
    try:
        await handle.terminate(reason=args.note or "terminated via scripts/workflow.py")
    except RPCError as exc:
        sys.exit(f"error: {exc.message}  (already closed?)")
    print(f"TERMINATED {workflow_id(run_id)}")
    print("note: this is the hard kill — the workflow ran no cleanup, so runs.runs may")
    print("      still show a live-looking status. Prefer `cancel` where possible.")
    return 0


async def cmd_list(_args: argparse.Namespace) -> int:
    client = await connect()
    print(f"{'STATUS':<12} {'WORKFLOW ID':<52} STARTED")
    async for wf in client.list_workflows():
        print(f"{wf.status.name:<12} {wf.id:<52} {wf.start_time:%Y-%m-%d %H:%M:%S}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name: str, fn, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--run-id", default=DEFAULT_RUN)
        p.set_defaults(fn=fn)
        return p

    p = add("start", cmd_start, "start the workflow for a run")
    p.add_argument("--study-id", default=DEFAULT_STUDY)
    p.add_argument("--no-wait", action="store_true", help="return immediately")
    p.add_argument("--no-gate", action="store_true", help="skip Gate 2 and auto-finalize")
    p.add_argument("--review-timeout", type=int, default=900, help="Gate 2 timeout, seconds")
    p.add_argument(
        "--batch-size", type=int, default=50,
        help="reactions per batch (one generate/embed/score/penalise/persist pass)",
    )
    p.add_argument(
        "--max-batches", type=int, default=20,
        help="batches before continue_as_new starts a fresh Temporal history",
    )

    add("status", cmd_status, "where the workflow is right now")
    add("finalize", cmd_finalize, "Gate 2: accept the results").add_argument("--note", default=None)
    add("cancel", cmd_cancel, "graceful stop; keeps results already scored").add_argument("--note", default=None)
    add("terminate", cmd_terminate, "hard kill (last resort)").add_argument("--note", default=None)

    lp = sub.add_parser("list", help="every workflow on the server")
    lp.set_defaults(fn=cmd_list)

    args = ap.parse_args()
    for attr in ("note",):
        if not hasattr(args, attr):
            setattr(args, attr, None)
    return asyncio.run(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
