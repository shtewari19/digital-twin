# SSR Engine — Run & Validate Guide

Every step needed to take the engine from a cold machine to a finished,
verified run — and how to check each stage independently rather than trusting
what the previous stage reported.

Architecture overview: [`ARCHITECTURE.md`](ARCHITECTURE.md)

**Just want the commands?** [`STEPS.md`](STEPS.md) is the copy-paste version.

For *why* the code is shaped this way, see
[`CODE_WALKTHROUGH.md`](CODE_WALKTHROUGH.md) (line by line, with line numbers)
and [`RUN_FLOW.md`](RUN_FLOW.md) (the flow and the architecture). This file is
the operational procedure.

---

## Contents

- [What you need first](#what-you-need-first)
- [The SQL you must run, in order](#the-sql-you-must-run-in-order)
- [Step 0 — infrastructure](#step-0--infrastructure)
- [Step 1 — schema (once per database)](#step-1--schema-once-per-database)
- [Step 2 — seed the study and run](#step-2--seed-the-study-and-run)
- [Step 3 — validate the configuration *before* spending money](#step-3--validate-the-configuration-before-spending-money)
- [Step 4 — start the worker](#step-4--start-the-worker)
- [Step 5 — start the run](#step-5--start-the-run)
- [Workflow commands — the full set](#workflow-commands--the-full-set)
- [Step 6 — watch it execute](#step-6--watch-it-execute)
- [Step 7 — Gate 2: finalize or reject](#step-7--gate-2-finalize-or-reject)
- [Step 8 — validate the results](#step-8--validate-the-results)
- [Step 9 — validate what Temporal did](#step-9--validate-what-temporal-did)
- [Step 10 — read the output](#step-10--read-the-output)
- [Re-running](#re-running)
- [Testing the cancel path](#testing-the-cancel-path)
- [Known quirks (expected, not bugs)](#known-quirks-expected-not-bugs)
- [Troubleshooting](#troubleshooting)

---

## What you need first

| Requirement | Check |
|---|---|
| Docker running | `docker compose ps` |
| `apps/engine/.env` with Postgres + Temporal + Azure vars | `cd apps/engine && uv run python -c "from app.config import settings; print(settings.asyncpg_dsn)"` |
| `apps/api/.env` (copy from `.env.example`) | `ls apps/api/.env` |
| Embedding service reachable | see below — **this is a hard dependency** |
| Azure OpenAI credentials valid | the run fails at `generate_reaction_batch` without them |

```bash
# The embedding endpoint must answer. Nothing scores without it.
curl -s -o /dev/null -w "embeddings: %{http_code}\n" -m 20 \
  -X POST https://ai.questkart.cloud/embeddings \
  -H 'content-type: application/json' -d '{"texts":["ping"]}'
# expect: embeddings: 200
```

Throughout this guide:

```bash
RUN=40000000-0000-0000-0000-000000000001
STUDY=50000000-0000-0000-0000-000000000001
PSQL="docker compose exec -T postgres psql -U $APP_POSTGRES_USER -d $APP_POSTGRES_DB"
```

---

## The SQL you must run, in order

Three files, and only the first two are needed for a first run:

| # | File | When | What it does |
|---|---|---|---|
| 1 | [`apps/api/scripts/setup.sql`](apps/api/scripts/setup.sql) | **once per database** | Creates all three schemas (`core`, `platform`, `runs`), every table, index and trigger. |
| 2 | [`apps/api/scripts/seed_hardcoded_run.sql`](apps/api/scripts/seed_hardcoded_run.sql) | **before every fresh test** | Inserts 1 domain, 1 study, 5 anchors, 5 claims, 12 avatars, 4 study↔avatar links, and **1 run already at `approved` with a complete `config_snapshot`**. Idempotent. |
| 3 | [`apps/api/scripts/reset_run.sql`](apps/api/scripts/reset_run.sql) | **between re-runs** | Deletes the run's results and puts it back to `approved`. Leaves `config_snapshot` alone. |

You do **not** need to write any SQL by hand. Everything is in those three
files with fixed, hand-readable ids.

---

## Step 0 — infrastructure

```bash
docker compose up -d
docker compose ps        # postgres, temporal, temporal-ui, redis should be up
```

**Validate:** Temporal UI loads at <http://localhost:8080>, and:

```bash
$PSQL -c "SELECT version();"
```

---

## Step 1 — schema (once per database)

```bash
$PSQL < apps/api/scripts/setup.sql
```

**Validate** — all three schemas and the key tables exist:

```bash
$PSQL -c "\dn"
$PSQL -c "\dt core.*"
$PSQL -c "\dt runs.*"
```

Expect schemas `core`, `platform`, `runs`; and in `runs`: `runs`,
`run_reactions`, `run_message_results`, `run_reports`, `exports`.

---

## Step 2 — seed the study and run

```bash
$PSQL < apps/api/scripts/seed_hardcoded_run.sql
```

**If you will drive the API with curl, pass your own email** — the study is
otherwise owned by `dev@example.com` and every call returns 404 (ownership is
checked, and a study you do not own reads as 404, never 403):

```bash
$PSQL -v owner_email="'you@example.com'" < apps/api/scripts/seed_hardcoded_run.sql
```

That email needs a `core.users` row, which is created the first time you call
`GET /api/v1/me` with your token — so sign in once before seeding.

The script prints its own verification row:

```
                run_id                |  status  | claims | avatars | anchors | penalties | pairs
--------------------------------------+----------+--------+---------+---------+-----------+-------
 40000000-0000-0000-0000-000000000001 | approved |      5 |       4 |       5 |        10 |    20
```

**Validate** the row counts:

```bash
$PSQL -c "
SELECT 'domains' t, count(*) FROM core.domains WHERE id='d0000000-0000-0000-0000-000000000001'
UNION ALL SELECT 'studies',  count(*) FROM core.studies WHERE id='$STUDY'
UNION ALL SELECT 'anchors',  count(*) FROM core.anchors WHERE scope_id='$STUDY'
UNION ALL SELECT 'claims',   count(*) FROM core.messages WHERE study_id='$STUDY'
UNION ALL SELECT 'avatars',  count(*) FROM core.avatars WHERE id::text LIKE 'a0000000%'
UNION ALL SELECT 'runs',     count(*) FROM runs.runs WHERE id='$RUN';"
```

Expect `1, 1, 5, 5, 12, 1`.

**Inspect the snapshot** — this one column is the engine's entire input:

```bash
$PSQL -t -A -c "
SELECT jsonb_pretty(config_snapshot - 'claims' - 'anchors' - 'penalties')
  FROM runs.runs WHERE id='$RUN';"
```

It should have exactly **8 keys** — `kbq`, `claims`, `avatar_ids`, `anchors`,
`penalties` (what the engine reads) plus `domain`, `study`, `snapshot_at`
(provenance). No `pair_count`, no `repetitions`: the pair count is always
`len(avatar_ids) × len(claims)`, derived wherever it is needed.

---

## Step 3 — validate the configuration *before* spending money

This is the step worth not skipping. Every failure it catches would otherwise
surface only *after* 20 LLM calls had been paid for.

```bash
cd apps/engine
uv run python scripts/validate_run.py --phase config
```

32 checks, no worker or LLM needed. It verifies:

- the snapshot has the 8 expected keys and none of the removed ones
- `kbq` is not duplicated inside `study`
- **every `claims[].id` and `avatar_ids[]` actually exists** — these are
  foreign keys, so a bad id kills the run at `persist_reactions`
- anchors are ascending, unique, and all have text
- every penalty has `trigger`/`adjustment`/`reason` and a positive adjustment
- all 12 personas parse from `fixtures/avatar_prompts.txt`
- every `avatar_id` is mapped in `AVATAR_ID_TO_PERSONA` — **an unmapped id
  silently falls back to the default persona**, which this catches
- `core.avatars.profile` is a pointer string, proving prompts come from the file
- `fetch_study_context` produces the right pair count, and every pair's prompt
  **byte-matches** its block in the text file and is distinct from the others

Ends with `ALL 32 CHECKS PASSED`; exit code 1 on any failure, so it can gate CI.

Sanity-check the prompt file on its own:

```bash
uv run python -m app.avatar_prompts     # lists all 12 personas with sizes
```

---

## Step 4 — start the worker

```bash
cd apps/engine
uv run python -m app.worker
```

**Validate** — expect exactly this line, then silence until a run starts:

```
INFO:engine.worker:connected to localhost:7233, polling task queue 'study-runs'
```

The API is only needed if you want to drive the run over HTTP (Step 5,
option A). The pipeline itself does not need it.

```bash
cd apps/api && uv run uvicorn app.main:app --reload
curl -s localhost:8000/health        # {"status":"ok"}
```

---

## Step 5 — start the run

The run is seeded at `approved`, which is what `start` requires.

### Option A — over the API (needs a bearer token)

```bash
TOKEN=$(cd apps/api && uv run python scripts/get_dev_token.py)
AUTH="Authorization: Bearer $TOKEN"
curl -s -X POST localhost:8000/api/v1/runs/$RUN/start -H "$AUTH"    # 202
```

To walk the full gate sequence instead, set the run to `draft` first and call
`estimate` → `approve` → `start`.

### Option B — straight to Temporal (no auth needed)

Same code path the API's `start_workflow` uses. Useful when Entra isn't
configured locally.

```bash
cd apps/engine
uv run python scripts/workflow.py start
```

It defaults to the seeded run id and watches until the workflow parks at
Gate 2. `--no-wait` returns immediately; `--no-gate` skips the review gate and
auto-finalizes; `--run-id <uuid>` targets a different run.

Do **not** hand-write the equivalent `python -c` snippet with a `$RUN` shell
variable — if the variable is unset it interpolates to an empty string and
starts a workflow with `run_id=''`, which fails a few seconds later on an
invalid UUID. The script validates its ids up front and refuses.

---

## Workflow commands — the full set

All Temporal-side actions, no shell variables, defaulting to the seeded run:

| Command | What it does |
|---|---|
| `python scripts/workflow.py start` | Start the run. Watches until Gate 2 or completion. |
| `python scripts/workflow.py status` | Where it is right now — progress, Gate 2 state, final result. |
| `python scripts/workflow.py finalize` | Gate 2: accept the results → `finalized`. |
| `python scripts/workflow.py cancel` | Graceful stop. Keeps everything already scored → `cancelled`. |
| `python scripts/workflow.py terminate` | Hard kill. No cleanup runs — last resort. |
| `python scripts/workflow.py list` | Every workflow on the server, newest first. |
| `python scripts/inspect_workflow.py` | Full activity history, attempts, signals, timers. |

Add `--run-id <uuid>` to any of them. Run `--help` on any subcommand for its
options.

## Step 6 — watch it execute

Expect roughly **4 minutes** for 100 reactions (4 personas × 5 respondents × 5 claims, 2 batches).

```bash
# poll the database — works with or without the API up
watch -n 3 "$PSQL -t -A -F' | ' -c \"
SELECT status, coalesce(coverage_pct::text,'-'),
  (SELECT count(*) FROM runs.run_reactions WHERE run_id='$RUN') || ' reactions',
  (SELECT count(*) FROM runs.run_message_results WHERE run_id='$RUN') || ' ranked',
  (SELECT count(*) FROM runs.run_reports WHERE run_id='$RUN') || ' reports'
FROM runs.runs WHERE id='$RUN'\""
```

**Validate** — you should see this exact progression:

```
approved        | -      | 0 reactions   | 0 ranked | 0 reports
running         | -      | 0 reactions   | 0 ranked | 0 reports   <- workflow's 1st activity
running         | -      | 50 reactions  | 0 ranked | 0 reports   <- batch 1 persisted
running         | -      | 100 reactions | 5 ranked | 0 reports   <- batch 2 + rollup
awaiting_review | 100.00 | 100 reactions | 5 ranked | 0 reports   <- Gate 2, NO report yet
finalized       | 100.00 | 100 reactions | 5 ranked | 1 reports   <- after your finalize
```

The worker log mirrors it:

```
run 40000000-… context: 4 personas x 5 respondents = 20 respondents; x 5 claims = 100 reactions | 5 anchors, 10 penalties
batch 2 persisted — 100/100 pairs scored (100.0%), 0 remaining
parked at awaiting_review — waiting for finalize/cancel
```

Over the API instead, if it's running:

```bash
curl -s localhost:8000/api/v1/runs/$RUN/status -H "$AUTH"
curl -N localhost:8000/api/v1/runs/$RUN/events -H "$AUTH"    # SSE
```

---

## Step 7 — Gate 2: finalize or reject

The workflow **blocks** at `awaiting_review` — a real server-side wait, not a
sleep. It stays there until signalled or until `review_timeout_seconds`
elapses (then `expired`).

```bash
# accept the results
curl -s -X POST "localhost:8000/api/v1/runs/$RUN/finalize?note=looks+good" -H "$AUTH"

# OR reject them — results stay in the database, run ends 'cancelled'
curl -s -X POST "localhost:8000/api/v1/runs/$RUN/cancel?note=rerun+needed" -H "$AUTH"
```

Without the API:

```bash
cd apps/engine
uv run python scripts/workflow.py finalize      # accept
uv run python scripts/workflow.py cancel        # reject; results are kept
```

Both return **202** with the *old* status still in the body — the API signals
the workflow and returns; the workflow does the status write a moment later.
Poll `/status` to see it flip.

---

## Step 8 — validate the results

```bash
cd apps/engine
uv run python scripts/validate_run.py
```

66 checks. The important ones don't trust the pipeline's own output — they
**re-derive it**:

| Group | What it proves |
|---|---|
| `runs.runs` | terminal status, `started_at` < `finished_at`, `coverage_pct = 100`, `error` NULL |
| `run_reactions` | exactly 20 rows, all pairs unique, all `status='ok'`, every reaction has real text |
| **the maths** | every stored `score` is recomputed from the stored `distribution` as `Σ(scale_point × p) + penalties` and compared — a bug in `_compute_pmf` or `_apply_penalties` fails here |
| **the pmf** | every distribution sums to 1.0 and has one bucket per anchor |
| `run_message_results` | 5 rows, ranks 1..N with no gaps, BT strengths sum to 1.0 and descend, recommendations within the CHECK constraint |
| `run_reports` | row exists, 6 sections, `summary` has `cohort_breakdown` + `penalty_hits`, one cohort per persona |

Ends with `ALL 48 CHECKS PASSED`.

**Prove the validator actually works** — break something and watch it fail:

```bash
$PSQL -c "UPDATE runs.runs SET config_snapshot = jsonb_set(config_snapshot,'{avatar_ids}',
  (config_snapshot->'avatar_ids') || '[\"a0000000-0000-0000-0000-0000000000ff\"]'::jsonb)
  WHERE id='$RUN';"
cd apps/engine && uv run python scripts/validate_run.py --phase config   # FAILs, exit 1
$PSQL < apps/api/scripts/seed_hardcoded_run.sql                          # restore
```

---

## Step 9 — validate what Temporal did

The database shows the *outcome*; this shows the *orchestration*.

```bash
cd apps/engine
uv run python scripts/inspect_workflow.py
```

```
status      : COMPLETED
duration    : 78.1s

activities (12):
   1. update_run_status        timeout=  15s  max_attempts=5  attempt=1
   2. fetch_study_context      timeout=  30s  max_attempts=5  attempt=1
   3. embed_batch              timeout=  30s  max_attempts=5  attempt=1
   4. generate_reaction_batch  timeout= 180s  max_attempts=5  attempt=1
   5. embed_batch              timeout=  60s  max_attempts=5  attempt=1
   6. score_batch              timeout=  30s  max_attempts=5  attempt=1
   7. apply_penalties_batch    timeout=  30s  max_attempts=5  attempt=1
   8. persist_reactions        timeout=  30s  max_attempts=5  attempt=1
   9. rollup_message_results   timeout=  30s  max_attempts=5  attempt=1
  10. generate_run_report      timeout= 120s  max_attempts=5  attempt=1
  11. update_run_status        timeout=  15s  max_attempts=5  attempt=1
  12. update_run_status        timeout=  15s  max_attempts=5  attempt=1

  sequence matches the expected single-batch pipeline exactly.

signals received     : ['finalize']
activity failures    : 0  (every activity succeeded on its first attempt)
Gate 2 review timer  : 1 started, 1 cancelled  (a signal released the wait before it expired)
continue_as_new hops : 0
total history events : 83
```

**What to check:**

- **17 activities** (2 batches × 5 steps, plus prelude and tail) — the pipeline ran end to end
- **`attempt=1` everywhere** — no retries; anything higher means a flaky
  dependency (the script flags it with `<- RETRIED Nx`)
- **`signals received: ['finalize']`** — your Gate 2 decision arrived
- **timer `1 started, 1 cancelled`** — proves the review gate is a genuine
  server-side wait that the signal released, not a busy loop
- **`continue_as_new: 0`** — 100 reactions still fits one workflow history; a much larger panel shows hops

`--raw` prints every history event. The same data is in the Temporal UI at
<http://localhost:8080> → search `study-run-$RUN`.

---

## Step 10 — read the output

```bash
# the ranking
$PSQL -c "
SELECT rmr.rank, rmr.recommendation, round(rmr.bt_strength,4) AS strength,
       round(rmr.aggregate_score,2) AS score, left(m.text,50) AS claim
  FROM runs.run_message_results rmr JOIN core.messages m ON m.id=rmr.message_id
 WHERE rmr.run_id='$RUN' ORDER BY rmr.rank;"

# one reaction with its probability distribution
$PSQL -c "
SELECT a.name, round(rr.score,2) AS score, round(rr.penalty,2) AS penalty,
       rr.distribution, left(rr.reaction,150) AS reaction
  FROM runs.run_reactions rr JOIN core.avatars a ON a.id=rr.avatar_id
 WHERE rr.run_id='$RUN' LIMIT 1;"

# the full narrative report
$PSQL -t -A -c "SELECT report FROM runs.run_reports WHERE run_id='$RUN';"
```

Remember: **lower score = more compelling** (scale point 1 is the most
compelling anchor), so rank 1 has the *lowest* aggregate score.

Over the API:

```bash
curl -s localhost:8000/api/v1/runs/$RUN/results -H "$AUTH" | jq '.ranking'
curl -s localhost:8000/api/v1/runs/$RUN/results -H "$AUTH" | jq -r '.report'
```

---

## Re-running

```bash
$PSQL < apps/api/scripts/reset_run.sql
```

Prints confirmation:

```
 status  | coverage_pct | pairs_to_run | reactions | ranked | reports
---------+--------------+--------------+-----------+--------+---------
 approved|              |           20 |         0 |      0 |       0
```

**Also terminate the old workflow if it is still open.** The workflow id is
`study-run-<run_id>` and Temporal refuses a second execution with the same id
while the first is running. A run that already reached
`finalized`/`cancelled`/`expired`/`failed` is already closed and needs nothing.

```bash
cd apps/engine
uv run python scripts/workflow.py status        # RUNNING?
uv run python scripts/workflow.py terminate     # then kill it
```

### Changing the persona mix

All 12 personas are already seeded. Swap them with one statement — prompts
resolve from the text file automatically, and the pair count follows:

```sql
UPDATE runs.runs
   SET config_snapshot = jsonb_set(config_snapshot, '{avatar_ids}',
         '["a0000000-0000-0000-0000-000000000005",
           "a0000000-0000-0000-0000-000000000006",
           "a0000000-0000-0000-0000-000000000007"]'::jsonb)
 WHERE id = '40000000-0000-0000-0000-000000000001';
```

Then re-run `validate_run.py --phase config` to confirm the new ids resolve.

---

## Testing the cancel path

Cancel is cooperative: the workflow finishes the work already in flight, saves
it, and stops at the next safe checkpoint — it does not abort mid-pipeline.

```bash
# clone the run under a second id
$PSQL -c "
INSERT INTO runs.runs (id, study_id, status, config_snapshot, model_config)
SELECT '40000000-0000-0000-0000-000000000002', study_id, 'approved', config_snapshot, model_config
  FROM runs.runs WHERE id='$RUN'
ON CONFLICT (id) DO UPDATE SET status='approved', workflow_id=NULL, error=NULL,
  coverage_pct=NULL, started_at=NULL, finished_at=NULL;"

# start it (Step 5, using run id …002), wait ~20s, then signal cancel
```

**Validate** — expect `cancelled`, `finished_at` set, `error.reason='cancelled'`
with your note, **reactions preserved**, and **no ranking or report** (it
stopped before those activities):

```bash
$PSQL -c "
SELECT status, error->>'reason' AS reason, error->>'note' AS note,
  (SELECT count(*) FROM runs.run_reactions WHERE run_id='40000000-0000-0000-0000-000000000002') AS reactions_kept,
  (SELECT count(*) FROM runs.run_message_results WHERE run_id='40000000-0000-0000-0000-000000000002') AS ranked,
  (SELECT count(*) FROM runs.run_reports WHERE run_id='40000000-0000-0000-0000-000000000002') AS reports
FROM runs.runs WHERE id='40000000-0000-0000-0000-000000000002';"
```

`inspect_workflow.py --run-id 40000000-0000-0000-0000-000000000002` shows the
signal arriving mid-batch and the remaining batch steps still completing before
the workflow stops — that is the design, not a leak.

---

## Known quirks (expected, not bugs)

**`baseline_lift_pct` can be NULL.** Bradley-Terry assigns strength exactly
`0` to a claim that wins **no** pairwise comparison — one that scored worst
from every persona. The lift formula is `(winner − lowest) / lowest`, so it
guards against dividing by zero and stores NULL. Likely with small panels.
`validate_run.py` reports it as a NOTE rather than a failure.

**Tied BT strengths.** Two claims can come back with identical strengths; the
order between them is then arbitrary. Both are symptoms of unregularised
Bradley-Terry on small data. A smoothing prior (add 0.5 to every pairwise cell)
would fix both but shifts every strength number, so it has not been applied.

**Rankings are not reproducible across runs.** With 4 personas the noise
(sd ≈ 0.12) is larger than the gaps between the top claims (≈ 0.03–0.07), so
the winner can change between two identical runs. "Drop the bottom two" is
trustworthy; "this one wins" is not. Widen the panel — all 12 personas are
seeded. See [`RUN_FLOW.md`](RUN_FLOW.md).

**Editing the prompts file needs a worker restart.** `load_prompts()` is
`@lru_cache`d, so the file is read once per process.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Run sits at `queued`, nothing happens | No worker polling. Start `python -m app.worker` — the task is durable and will run. |
| `processed: 0` for minutes, then the run fails | The embedding endpoint is down. Check it with the `curl` in [prerequisites](#what-you-need-first); `inspect_workflow.py` shows `embed_batch` retrying. |
| `Workflow execution already started` | The previous execution is still open. Terminate it — see [Re-running](#re-running). |
| Prompt edits have no effect | Restart the worker (`lru_cache`). |
| All personas sound identical | An `avatar_id` isn't in `AVATAR_ID_TO_PERSONA` so it fell back to the default. `validate_run.py --phase config` catches this. |
| FK violation at `persist_reactions` | A `claims[].id` or `avatar_ids[]` in the snapshot doesn't exist. `validate_run.py --phase config` catches this **before** any spend. |
| Ranking looks inverted | Anchor `scale_point` order is reversed. Point 1 must be the *most* compelling. |
| `/status` never reaches 100% | Reactions written ≠ `avatar_ids × claims`. Check for failed pairs: `SELECT status, count(*) FROM runs.run_reactions WHERE run_id='…' GROUP BY 1;` |
| API returns 401 | Bearer token missing or expired — `uv run python scripts/get_dev_token.py`. Or drive the run through Temporal directly (Step 5, option B). |
| API returns 409 on `start` | Run isn't `approved`, or you already hold the max active runs. The problem body names the expected statuses. |
