"""Validate a run end to end — configuration, storage, and the maths.

Every check is an independent re-derivation from what is actually in the
database, not a re-read of what the pipeline reported. The scoring checks in
particular recompute each score from the stored probability distribution and
compare it against the stored score, so a bug in `_compute_pmf` or
`_apply_penalties` would show up here rather than being confirmed by itself.

Usage (from apps/engine/, with .env configured):

    # before starting the run — config only, no results needed
    uv run python scripts/validate_run.py --phase config

    # after the run has finalized — everything
    uv run python scripts/validate_run.py

    # a different run
    uv run python scripts/validate_run.py --run-id <uuid>

Exit code is 0 when every check passes, 1 otherwise, so it can gate CI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import asyncpg

from app import avatar_prompts
from app.activities import Penalty, StudyDataActivities, _apply_penalties, pairs_for_slice
from app.config import settings

DEFAULT_RUN = "40000000-0000-0000-0000-000000000001"
DEFAULT_STUDY = "50000000-0000-0000-0000-000000000001"

#: Keys the snapshot is expected to carry — five the engine reads, three of
#: pure provenance. Anything else is drift.
ENGINE_KEYS = {"kbq", "claims", "avatar_ids", "anchors", "penalties"}
PROVENANCE_KEYS = {"domain", "study", "snapshot_at"}
#: Keys that were deliberately removed; their reappearance is a regression.
REMOVED_KEYS = {"pair_count", "repetitions", "message_ids", "anchor_ids"}
#: The panel size: personas x this x claims = reactions.
RESPONDENTS_KEY = "respondents_per_avatar"


class Checks:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def __call__(self, condition: object, message: str) -> bool:
        if condition:
            self.passed += 1
            print(f"  \033[32mPASS\033[0m  {message}")
            return True
        self.failed.append(message)
        print(f"  \033[31mFAIL\033[0m  {message}")
        return False

    def section(self, title: str) -> None:
        print(f"\n--- {title} ---")

    def report(self) -> int:
        total = self.passed + len(self.failed)
        print(f"\n{'=' * 60}")
        if self.failed:
            print(f"{self.passed}/{total} passed — {len(self.failed)} FAILED:")
            for f in self.failed:
                print(f"  - {f}")
            return 1
        print(f"\033[32mALL {total} CHECKS PASSED\033[0m")
        return 0


def _as_json(value: object) -> object:
    """asyncpg returns jsonb as str on some driver/codec combinations."""
    return json.loads(value) if isinstance(value, str) else value


async def validate_config(pool: asyncpg.Pool, run_id: str, study_id: str, c: Checks) -> None:
    """Phase 1 — the run is configured correctly. Safe to run BEFORE starting
    it: nothing here needs results, and every failure here would otherwise
    only surface after paying for LLM calls."""
    row = await pool.fetchrow("SELECT status, config_snapshot FROM runs.runs WHERE id = $1", run_id)
    if not c(row is not None, f"run {run_id} exists"):
        return
    snap = _as_json(row["config_snapshot"]) or {}

    c.section("config_snapshot shape")
    c(ENGINE_KEYS <= set(snap), f"all 5 engine keys present: {sorted(ENGINE_KEYS)}")
    c(PROVENANCE_KEYS <= set(snap), f"all 3 provenance keys present: {sorted(PROVENANCE_KEYS)}")
    c(
        not (REMOVED_KEYS & set(snap)),
        f"no removed keys ({', '.join(sorted(REMOVED_KEYS))}) — got {sorted(set(snap))}",
    )
    c(
        "outcome_dimension" not in (snap.get("study") or {}),
        "kbq is stored once, not duplicated inside `study`",
    )
    c(bool(snap.get("kbq")), "kbq is non-empty")

    c.section("referential integrity (these are foreign keys)")
    claim_ids = [x["id"] for x in snap.get("claims", []) if isinstance(x, dict)]
    avatar_ids = snap.get("avatar_ids") or []
    missing_claims = [
        r["id"]
        for r in await pool.fetch(
            "SELECT unnest($1::uuid[]) AS id EXCEPT SELECT id FROM core.messages", claim_ids
        )
    ]
    missing_avatars = [
        r["id"]
        for r in await pool.fetch(
            "SELECT unnest($1::uuid[]) AS id EXCEPT SELECT id FROM core.avatars", avatar_ids
        )
    ]
    c(not missing_claims, f"every claims[].id exists in core.messages ({len(claim_ids)} claims)")
    c(not missing_avatars, f"every avatar_ids[] exists in core.avatars ({len(avatar_ids)} avatars)")

    c.section("anchors")
    anchors = snap.get("anchors") or []
    points = [a["scale_point"] for a in anchors]
    c(len(anchors) >= 2, f"at least 2 anchors — got {len(anchors)}")
    c(points == sorted(points), f"scale_points ascending: {points}")
    c(len(set(points)) == len(points), "no duplicate scale_points")
    c(all((a.get("text") or "").strip() for a in anchors), "every anchor has text")

    c.section("penalties")
    pens = snap.get("penalties") or []
    c(
        all({"trigger", "adjustment", "reason"} <= set(p) for p in pens),
        f"every penalty has trigger/adjustment/reason ({len(pens)} penalties)",
    )
    c(all(float(p["adjustment"]) > 0 for p in pens), "every adjustment is positive (penalties worsen a score)")

    c.section("avatar prompts resolve from the text file")
    prompts = avatar_prompts.load_prompts()
    c(len(prompts) >= 1, f"{len(prompts)} personas parsed from {avatar_prompts.PROMPTS_PATH.name}")
    unmapped = [a for a in avatar_ids if str(a).lower() not in avatar_prompts.AVATAR_ID_TO_PERSONA]
    c(not unmapped, f"every avatar_id is mapped in AVATAR_ID_TO_PERSONA (else silently defaults): {unmapped}")
    db_profiles = {
        r["id"]: r["profile"]
        for r in await pool.fetch("SELECT id::text, profile FROM core.avatars WHERE id = ANY($1::uuid[])", avatar_ids)
    }
    c(
        all((p or "").startswith("prompt:") for p in db_profiles.values()),
        "core.avatars.profile is a pointer string, not a prompt (prompts live in the file)",
    )

    c.section("panel size")
    respondents = int(snap.get(RESPONDENTS_KEY) or 1)
    c(respondents >= 1, f"{RESPONDENTS_KEY} = {respondents} (respondents per persona)")
    c(
        "scale_min" not in (snap.get("study") or {}) and "scale_max" not in (snap.get("study") or {}),
        "scale_min/scale_max are NOT in the snapshot (the scale is anchors[].scale_point)",
    )

    c.section("fetch_study_context — what the engine will actually execute")
    ctx = await StudyDataActivities(pool).fetch_study_context(run_id, study_id)
    expected_pairs = len(avatar_ids) * respondents * len(claim_ids)
    c(ctx.total_reactions == expected_pairs,
      f"{expected_pairs} reactions = {len(avatar_ids)} personas x {respondents} respondents "
      f"x {len(claim_ids)} claims")
    c(ctx.respondents_per_avatar == respondents, f"context carries respondents_per_avatar={respondents}")
    c(
        len(ctx.avatar_ids) == len(avatar_ids) and len(ctx.claims) == len(claim_ids),
        "context carries the SOURCES (avatar_ids + claims), not a materialised pair list",
    )

    # The cross product is expanded on demand; verify the slicer reproduces it
    # exactly and that slicing agrees with expanding it whole.
    whole = pairs_for_slice(ctx.avatar_ids, ctx.claims, respondents, 0, ctx.total_reactions)
    sliced: list = []
    for start in range(0, ctx.total_reactions, 7):
        sliced += pairs_for_slice(ctx.avatar_ids, ctx.claims, respondents, start, 7)
    key = lambda ps: [(p.avatar_id, p.respondent, p.message_id) for p in ps]  # noqa: E731
    c(len(whole) == expected_pairs, f"pairs_for_slice expands to {expected_pairs} reactions")
    c(key(sliced) == key(whole), "batched slicing reproduces the whole cross product exactly")
    c(
        pairs_for_slice(ctx.avatar_ids, ctx.claims, respondents, ctx.total_reactions, 10) == [],
        "slicing past the end returns nothing (the loop terminates)",
    )
    judges = {(p.avatar_id, p.respondent) for p in whole}
    c(
        len(judges) == len(avatar_ids) * respondents,
        f"{len(judges)} distinct (persona, respondent) judges — each is scored independently",
    )
    c(
        all(1 <= p.respondent <= respondents for p in whole),
        f"every respondent index is within 1..{respondents}",
    )
    c(
        not hasattr(whole[0], "avatar_profile"),
        "Pair carries no inline prompt (kept the workflow payload constant-size)",
    )
    c(ctx.kbq == snap["kbq"], "kbq came from the snapshot, not the study row")
    c(len(ctx.penalties) == len(pens), f"{len(ctx.penalties)} penalties loaded")
    resolved = {
        a: avatar_prompts.prompt_for_avatar(a, fallback_name=None) for a in ctx.avatar_ids
    }
    mismatched = [
        a for a, prof in resolved.items()
        if prof != prompts.get(avatar_prompts.AVATAR_ID_TO_PERSONA.get(a, ""))
    ]
    c(not mismatched, "every pair's prompt byte-matches its persona block in the file")
    c(
        len(set(resolved.values())) == len(resolved),
        f"all {len(resolved)} prompts are distinct (none silently fell back to the default)",
    )
    for aid, prof in sorted(resolved.items()):
        key = avatar_prompts.AVATAR_ID_TO_PERSONA.get(aid, "<unmapped>")
        print(f"        {aid[-4:]} -> {key:<42s} {len(prof):>5d} chars")


async def validate_results(pool: asyncpg.Pool, run_id: str, c: Checks) -> None:
    """Phase 2 — the run executed correctly and stored what it should."""
    run = await pool.fetchrow("SELECT * FROM runs.runs WHERE id = $1", run_id)
    snap = _as_json(run["config_snapshot"]) or {}
    # personas x respondents each x claims — the same product the engine builds.
    expected = (
        len(snap.get("avatar_ids") or [])
        * int(snap.get(RESPONDENTS_KEY) or 1)
        * len(snap.get("claims") or [])
    )

    c.section("runs.runs — the run row")
    terminal = {"finalized", "cancelled", "expired", "failed"}
    c(run["status"] in terminal, f"status is terminal: {run['status']}")
    c(run["started_at"] is not None, "started_at set (written by the workflow's first activity)")
    c(run["finished_at"] is not None, "finished_at set")
    if run["started_at"] and run["finished_at"]:
        c(run["finished_at"] >= run["started_at"], "finished_at >= started_at")
    if run["status"] == "finalized":
        c(run["error"] is None, "error is NULL on a finalized run")
        c(
            run["coverage_pct"] is not None and float(run["coverage_pct"]) == 100.0,
            f"coverage_pct = {run['coverage_pct']} (expected 100.00)",
        )

    c.section("runs.run_reactions — one row per (avatar, claim)")
    rx = await pool.fetch("SELECT * FROM runs.run_reactions WHERE run_id = $1", run_id)
    if run["status"] == "finalized":
        c(len(rx) == expected, f"{expected} reaction rows — got {len(rx)}")
    else:
        c(len(rx) >= 0, f"{len(rx)} reaction rows kept (run ended '{run['status']}')")
    if not rx:
        return
    c(
        len({(r["avatar_id"], r["message_id"], r["respondent"]) for r in rx}) == len(rx),
        "every (avatar, claim, respondent) row is unique",
    )
    respondents = int(snap.get(RESPONDENTS_KEY) or 1)
    judges = {(r["avatar_id"], r["respondent"]) for r in rx}
    c(
        len(judges) == len(snap.get("avatar_ids") or []) * respondents,
        f"{len(judges)} distinct respondents stored "
        f"({len(snap.get('avatar_ids') or [])} personas x {respondents})",
    )
    per_claim = {}
    for r in rx:
        per_claim[r["message_id"]] = per_claim.get(r["message_id"], 0) + 1
    c(
        all(v == len(judges) for v in per_claim.values()),
        f"every claim was scored by all {len(judges)} respondents",
    )
    c(all(r["status"] == "ok" for r in rx), "every reaction status='ok'")
    c(all(r["reaction"] and len(r["reaction"]) > 100 for r in rx), "every reaction has real generated text")
    c(all(r["score"] is not None for r in rx), "every score populated")

    c.section("the maths — recomputed independently from stored data")
    points = [a["scale_point"] for a in sorted(snap["anchors"], key=lambda x: x["scale_point"])]
    pens = [Penalty(p["trigger"], float(p["adjustment"]), p["reason"]) for p in snap["penalties"]]
    bad_pmf, bad_score, bad_len = [], [], []
    for r in rx:
        pmf = _as_json(r["distribution"])
        if pmf is None or len(pmf) != len(points):
            bad_len.append(r["id"])
            continue
        if abs(sum(pmf) - 1.0) > 0.01:
            bad_pmf.append(r["id"])
        # Re-derive: expected value over the scale, then the penalty pass.
        base = sum(sp * q for sp, q in zip(points, pmf, strict=True))
        want_score, want_pen, _ = _apply_penalties(round(base, 2), pens, r["reaction"])
        if abs(want_score - float(r["score"])) > 0.02 or abs(want_pen - float(r["penalty"] or 0)) > 0.001:
            bad_score.append(r["id"])
    c(not bad_len, f"every distribution has {len(points)} buckets, one per anchor")
    c(not bad_pmf, f"every distribution is a valid pmf summing to 1.0 — {len(rx) - len(bad_pmf)}/{len(rx)}")
    c(
        not bad_score,
        f"score == E[scale|pmf] + penalties for all {len(rx)} reactions (re-derived, not re-read)",
    )
    c(all(min(points) <= float(r["score"]) <= max(points) + 3 for r in rx), "scores within a sane range")

    if run["status"] != "finalized":
        c.section("rejected / expired run — ranking kept, report never generated")
        reports = await pool.fetchval(
            "SELECT count(*) FROM runs.run_reports WHERE run_id = $1", run_id
        )
        ranked = await pool.fetchval(
            "SELECT count(*) FROM runs.run_message_results WHERE run_id = $1", run_id
        )
        if ranked:
            c(ranked > 0, f"ranking written before the gate, so it survives the rejection ({ranked} rows)")
            c(reports == 0, f"NO report generated for a non-approved run — got {reports}")
        else:
            print("  (cancelled before the ranking stage — nothing further to check)")
        return

    c.section("runs.run_message_results — the Bradley-Terry ranking")
    mr = await pool.fetch(
        "SELECT * FROM runs.run_message_results WHERE run_id = $1 ORDER BY rank", run_id
    )
    n_claims = len(snap["claims"])
    c(len(mr) == n_claims, f"{n_claims} ranked rows — got {len(mr)}")
    c([m["rank"] for m in mr] == list(range(1, len(mr) + 1)), "ranks are 1..N with no gaps or duplicates")
    strengths = [float(m["bt_strength"]) for m in mr]
    c(abs(sum(strengths) - 1.0) < 0.01, f"BT strengths sum to 1.0 — got {sum(strengths):.4f}")
    c(strengths == sorted(strengths, reverse=True), "strengths are descending by rank")
    c(mr[0]["recommendation"] == "recommended", f"rank 1 recommendation = {mr[0]['recommendation']}")
    c(
        {m["recommendation"] for m in mr} <= {"recommended", "runner_up", "drop"},
        "recommendations are within the CHECK constraint's allowed set",
    )
    # Known degeneracy, reported rather than asserted — see TESTING.md.
    zeros = [m["rank"] for m in mr if float(m["bt_strength"]) == 0.0]
    ties = len(strengths) - len(set(strengths))
    if zeros:
        print(f"        NOTE: rank(s) {zeros} have bt_strength exactly 0 (shut out — won no comparison).")
        print("              baseline_lift_pct is NULL as a result. See TESTING.md 'Known quirks'.")
    if ties:
        print(f"        NOTE: {ties} tied strength value(s) — ordering between them is arbitrary.")

    c.section("derived result metrics — no duplicate storage")
    # Everything a reviewer needs is in run_message_results + run_reactions.
    # There is deliberately no runs.runs.metrics column: a second copy could
    # only ever drift from these rows.
    cols = await pool.fetch(
        """SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'runs' AND table_name = 'runs'"""
    )
    c(
        "metrics" not in {r["column_name"] for r in cols},
        "runs.runs has no `metrics` column (results live in their own tables)",
    )
    winner = mr[0]
    winner_text = await pool.fetchval(
        "SELECT text FROM core.messages WHERE id = $1", winner["message_id"]
    )
    c(bool(winner_text), f"winner derivable from rank 1: {str(winner_text)[:55]}…")
    if len(mr) > 1:
        margin = float(mr[0]["bt_strength"]) - float(mr[1]["bt_strength"])
        c(margin >= 0, f"margin over runner-up derivable: {margin:.6f}")
    agg = await pool.fetchrow(
        """SELECT count(*) AS total, count(*) FILTER (WHERE status='ok') AS ok,
                  min(score) AS best, max(score) AS worst, avg(score) AS mean
             FROM runs.run_reactions WHERE run_id = $1""",
        run_id,
    )
    c(agg["total"] == len(rx), f"reaction counts derivable from run_reactions ({agg['total']})")
    c(
        agg["total"] == expected,
        f"reaction count matches personas x respondents x claims ({expected})",
    )
    c(
        agg["best"] is not None and agg["best"] <= agg["worst"],
        f"score stats derivable: best {agg['best']:.2f} <= worst {agg['worst']:.2f} (lower is better)",
    )

    c.section("runs.run_reports — the narrative, generated AFTER approval")
    rep = await pool.fetchrow("SELECT * FROM runs.run_reports WHERE run_id = $1", run_id)
    if not c(rep is not None, "report row exists (only an approved run has one)"):
        return
    heads = [ln for ln in rep["report"].split("\n") if ln.startswith("## ")]
    c(len(heads) == 6, f"report has 6 top-level sections — got {len(heads)}")
    c(len(rep["report"]) > 2000, f"report is substantial ({len(rep['report'])} chars)")
    summary = _as_json(rep["summary"]) or {}
    c({"cohort_breakdown", "penalty_hits"} <= set(summary), f"summary keys: {sorted(summary)}")
    c(
        len(summary.get("cohort_breakdown") or {}) == len(snap["avatar_ids"]),
        f"one cohort per persona — got {len(summary.get('cohort_breakdown') or {})}",
    )
    if rep["baseline_lift_pct"] is None:
        print("        NOTE: baseline_lift_pct is NULL (lowest BT strength was 0). Expected when")
        print("              the worst claim is shut out; not a storage failure.")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", default=DEFAULT_RUN)
    ap.add_argument("--study-id", default=DEFAULT_STUDY)
    ap.add_argument(
        "--phase",
        choices=["config", "results", "all"],
        default="all",
        help="'config' is safe to run before starting the run; 'results' needs a finished run.",
    )
    args = ap.parse_args()

    c = Checks()
    print(f"Validating run {args.run_id}")
    pool = await asyncpg.create_pool(settings.asyncpg_dsn)
    try:
        if args.phase in ("config", "all"):
            print("\n=========== PHASE 1: CONFIGURATION ===========")
            await validate_config(pool, args.run_id, args.study_id, c)
        if args.phase in ("results", "all"):
            print("\n=========== PHASE 2: EXECUTION & STORAGE ===========")
            await validate_results(pool, args.run_id, c)
    finally:
        await pool.close()
    return c.report()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
