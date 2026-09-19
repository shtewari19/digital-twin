# STEPS — Run the Workflow

Copy-paste, in order. Four terminals. Nothing to figure out.

Jump to: [watching the logs](#watching-the-logs) · [every endpoint](#every-endpoint) · [troubleshooting](#troubleshooting)

Architecture overview: [`ARCHITECTURE.md`](ARCHITECTURE.md) · Endpoint → engine: [`API_TO_ENGINE.md`](API_TO_ENGINE.md)

Deeper reading: [`TESTING.md`](TESTING.md) (validate each stage) ·
[`RUN_FLOW.md`](RUN_FLOW.md) (what happens inside) ·
[`CODE_WALKTHROUGH.md`](CODE_WALKTHROUGH.md) (every script).

---

## Read this first — the 404 trap

Every API route resolves you from your bearer token and checks that **you own
the study**. A study you do not own returns **404, not 403** — deliberately, so
the API never confirms that someone else's id exists.

So if you see this after seeding:

```json
{"status": 404, "detail": "No study exists with id 50000000-0000-0000-0000-000000000001."}
```

…nothing is broken. The row is there; it just belongs to `dev@example.com`
while your token belongs to you. **Seed with your own email** (Step 3 below)
and it works.

You only need this if you drive the API with curl. The `workflow.py` path uses
no auth at all and works with the default owner.

---

# Terminal 1 — infrastructure (run once, leave it)

```bash
cd ~/chorus/digital-twin
docker compose up -d
docker ps --format '{{.Names}}: {{.Status}}' | grep -e postgres -e temporal
```

Check the embedding service. **Nothing scores without it:**

```bash
curl -s -o /dev/null -w "embeddings: %{http_code}\n" -m 20 \
  -X POST https://ai.questkart.cloud/embeddings \
  -H 'content-type: application/json' -d '{"texts":["ping"]}'
```

Must print `embeddings: 200`.

---

# Terminal 2 — the worker (leave it running)

**This does all the work. Nothing executes without it.**

```bash
cd ~/chorus/digital-twin/apps/engine
uv run python -m app.worker
```

Wait for exactly this, then leave the terminal alone:

```
INFO:engine.worker:connected to localhost:7233, polling task queue 'study-runs'
```

---

# Terminal 3 — the API (leave it running)

```bash
cd ~/chorus/digital-twin/apps/api
uv run uvicorn app.main:app --reload
```

Wait for `Application startup complete.`

---

# Terminal 4 — everything else

## Step 1 — set your variables

```bash
cd ~/chorus/digital-twin

export API=http://localhost:8000/api/v1
export RUN=40000000-0000-0000-0000-000000000001
export STUDY=50000000-0000-0000-0000-000000000001
export PSQL="docker exec -i chorus-postgres psql -U nms3 -d chorus"
```

## Step 2 — get a token

```bash
cd apps/api
uv run python scripts/get_dev_token.py
```

It prints a URL and a code. **Open the URL in a browser, enter the code, sign
in.** Then copy the token it prints:

```bash
cd ~/chorus/digital-twin
export AUTH="Authorization: Bearer <paste the token here>"
```

> Paste it as one line. A truncated or wrapped paste is the other common cause
> of confusing errors. The token expires after ~1 hour; re-run the script.

Confirm it works and **note your email** — you need it in Step 3:

```bash
curl -s $API/me -H "$AUTH" | jq
```

```json
{
  "id": "ef5d178b-5d87-46a8-9a87-5a73e2092414",
  "name": "Nandish M S",
  "email": "nandish.ms@questkart.in",
  "role": "operator"
}
```

This call is also what **creates your user row** — the seed in Step 3 needs it
to exist, so do not skip it.

## Step 3 — seed the database, owned by YOU

**What the API can and cannot create.** There is no endpoint that creates a
domain, study, claim, anchor or avatar — the API exposes only `GET /domains`
plus the run lifecycle. So the study data always has to be seeded; the only
choice is who creates the **run**:

| | Track A — seed the run too | Track B — API creates the run |
|---|---|---|
| Study data (domain, study, claims, anchors, avatars) | seed sections 1–7 | seed sections 1–7 — **same, unavoidable** |
| The `runs.runs` row | seed section 8, at `approved` | `POST /studies/{id}/runs` → `draft` |
| Then you run | `start` | `estimate` → `approve` → `start` |
| Use it when | fastest path; works with no token at all | exercising the real UI flow |

**Track A** (default — what the rest of these steps assume):

```bash
$PSQL -v owner_email="'nandish.ms@questkart.in'" < apps/api/scripts/seed_hardcoded_run.sql
```

**Track B** — seed the study data only, then jump to
[Step 3b](#step-3b--track-b-create-the-run-through-the-api):

```bash
$PSQL -v owner_email="'nandish.ms@questkart.in'" -v skip_run=1 \
      < apps/api/scripts/seed_hardcoded_run.sql
```

Use the `email` from Step 2. Check the last line says **your** email:

```
                run_id                |  status  |        owned_by         | claims | avatars | anchors | penalties | resp_each | reactions
--------------------------------------+----------+-------------------------+--------+---------+---------+-----------+-----------+-----------
 40000000-0000-0000-0000-000000000001 | approved | nandish.ms@questkart.in |      5 |       4 |       5 |        10 |         5 |       100
```

If `owned_by` shows `dev@example.com`, the API will 404 — re-run with the right
email.

> First time on a brand-new database only, run the schema first:
> `$PSQL < apps/api/scripts/setup.sql`

## Step 3b — Track B: create the run through the API

Skip this if you used Track A. These are the endpoints the UI will call.

### 1. Create the run — builds `config_snapshot` from the tables

```bash
RUN=$(curl -s -X POST $API/studies/$STUDY/runs \
  -H "$AUTH" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"respondents_per_avatar": 5}' | jq -r '.id')
echo "RUN=$RUN"
```

That single call reads `core.studies`, `core.domains`, `core.messages`,
`core.study_avatars` and `core.anchors` — all by `study_id` — and writes **one**
`runs.runs` row with the complete snapshot. Status is `draft`.

`respondents_per_avatar` is yours to set: 4 personas × 5 respondents × 5 claims
= 100 reactions. Omit it and it defaults to 1 (20 reactions).

Confirm the snapshot the API built:

```bash
$PSQL -t -A -c "SELECT jsonb_pretty(config_snapshot - 'claims' - 'anchors' - 'penalties')
                  FROM runs.runs WHERE id='$RUN';"
```

### 2. Estimate — `draft` → `estimated`

```bash
curl -s -X POST $API/runs/$RUN/estimate -H "$AUTH" | jq
```

Arithmetic only, no LLM call. Returns projected time/cost plus `advice`.

### 3. Approve — **GATE 1**, `estimated` → `approved`

```bash
curl -s -X POST $API/runs/$RUN/approve -H "$AUTH" | jq
```

This is the spend authorisation. `start` returns **409** without it.

### 4. Validate before spending

```bash
cd apps/engine && uv run python scripts/validate_run.py --phase config --run-id $RUN && cd ..
```

Then continue at **Step 5** below — everything from there on is identical.

> Verified: a run created this way (no literal JSON anywhere) produced the same
> 8-key snapshot as the seed, passed all 32 config checks, ran 100 reactions and
> passed all 66 result checks.

---

## Step 4 — check the config before spending money

```bash
cd apps/engine && uv run python scripts/validate_run.py --phase config && cd ..
```

Expect `ALL 32 CHECKS PASSED`. This catches bad ids and unmapped personas
*before* any LLM call.

## Step 5 — START the run

Track A's seeded run is already `approved`; Track B approved it in Step 3b.
Either way this is the call:

```bash
curl -s -X POST $API/runs/$RUN/start -H "$AUTH" | jq
```

Expect `202` and `"status": "queued"`.

> On Track A you can still exercise the gates: set the run back to `draft`
> (`$PSQL -c "UPDATE runs.runs SET status='draft' WHERE id='$RUN';"`) then run
> `estimate` → `approve` → `start`.

## Step 6 — watch it (~4 minutes)

The seeded run is 4 personas x **5 respondents each** x 5 claims = **100
reactions**, across 2 batches. Lower `respondents_per_avatar` in the snapshot
for a faster/cheaper run:

```bash
$PSQL -c "UPDATE runs.runs SET config_snapshot =
  jsonb_set(config_snapshot,'{respondents_per_avatar}','1')
  WHERE id='$RUN';"      # -> 20 reactions, ~75 seconds
```


```bash
watch -n 3 "curl -s $API/runs/$RUN/status -H '$AUTH' | jq -c"
```

Or stream it:

```bash
curl -N $API/runs/$RUN/events -H "$AUTH"
```

Wait until the status is **`awaiting_review`**. Terminal 2 will show:

```
parked at awaiting_review — waiting for finalize/cancel
```

## Step 7 — review what the engine decided

At this point the **ranking** exists in `runs.run_message_results`. **The report
does not yet** — it is generated only after you approve.

```bash
curl -s $API/runs/$RUN/results -H "$AUTH" | jq '{status, winner: .ranking[0].text, report}'
```

```json
{
  "status": "awaiting_review",
  "winner": "Fixed 18-month treatment duration with subcutaneous dosing — …",
  "report": null
}
```

`report: null` is correct here.

## Step 8 — APPROVE, which generates the report

```bash
curl -s -X POST "$API/runs/$RUN/finalize?note=approved" -H "$AUTH" | jq
```

Returns `202` with the old status still in the body — the workflow writes
`finalized` a moment later. To **reject** instead (keeps the ranking, never
writes a report):

```bash
curl -s -X POST "$API/runs/$RUN/cancel?note=not+convincing" -H "$AUTH" | jq
```

## Step 9 — read the results

```bash
curl -s $API/runs/$RUN/results -H "$AUTH" | jq '.ranking'
curl -s $API/runs/$RUN/results -H "$AUTH" | jq -r '.report'
```

Remember: **lower score = more compelling**, so rank 1 has the lowest score.

## Step 10 — validate everything

```bash
cd apps/engine
uv run python scripts/validate_run.py        # ALL 66 CHECKS PASSED
uv run python scripts/inspect_workflow.py    # 17 activities over 2 batches, 0 failures
```

---

# Watching the logs

## Terminal 2 — the worker log is the play-by-play

Every stage prints a line. This is a real 100-reaction run, verbatim:

```
INFO:engine.worker:connected to localhost:7233, polling task queue 'study-runs'
INFO:temporalio.activity:run 40000000-… context: 4 personas x 5 respondents = 20 respondents; x 5 claims = 100 reactions | 5 anchors, 10 penalties
INFO:temporalio.workflow:context loaded from config_snapshot — 5 respondents/persona, 100 reactions to generate, 5 anchors, 10 penalties; embedding anchors
INFO:temporalio.workflow:batch 1: generating 50 reactions (LLM)
INFO:temporalio.workflow:batch 1 persisted — 50/100 pairs scored (50.0%), 50 remaining
INFO:temporalio.workflow:batch 2: generating 50 reactions (LLM)
INFO:temporalio.workflow:batch 2 persisted — 100/100 pairs scored (100.0%), 0 remaining
INFO:temporalio.workflow:all 100 pairs scored — computing Bradley-Terry ranking
INFO:temporalio.activity:run 40000000-… ranked 5 claims from 20 respondents | winner: 'Proven remission in triple-class exposed patients who have f' (strength 0.3106, margin 0.007207)
INFO:temporalio.workflow:ranking written to runs.run_message_results — winner: Proven remission in triple-class exposed patients who have failed BCMA-targeted 
INFO:temporalio.workflow:GATE 2: parked at awaiting_review for up to 900s — send `finalize` to approve (report is generated only then) or `cancel` to reject
INFO:temporalio.workflow:approved — generating the narrative report (2 LLM calls)
INFO:temporalio.workflow:report written to runs.run_reports
INFO:temporalio.workflow:run complete — final status: finalized
```

If you **reject** instead, the last three lines become:

```
INFO:temporalio.workflow:rejected — skipping report generation; metrics are kept
INFO:temporalio.workflow:run complete — final status: cancelled
```

Every `temporalio.activity` line also carries `activity_id`, `activity_type`,
**`attempt`**, `workflow_id` and `task_queue` automatically — so a retry is
visible as `attempt: 2`.

Save a run's log to a file:

```bash
cd ~/chorus/digital-twin/apps/engine
uv run python -m app.worker 2>&1 | tee worker.log
```

## Terminal 3 — the API log

One line per request, plus the lifecycle events:

```
INFO:api.runs:run 40000000-… submitted to Temporal (workflow_id=study-run-40000000-…)
INFO:api.runs:run 40000000-…: finalize signal sent
INFO:     127.0.0.1:52344 - "POST /api/v1/runs/40000000-…/start HTTP/1.1" 202 Accepted
```

## Terminal 4 — the durable log is Temporal's

The worker log is stdout and disappears when you close the terminal. Temporal's
history is persisted, so you can read it back at any time:

```bash
cd ~/chorus/digital-twin/apps/engine
uv run python scripts/inspect_workflow.py            # summary
uv run python scripts/inspect_workflow.py --raw      # every single event
```

```
workflow id : study-run-40000000-…
run id      : beb5a021-1912-4a8d-a766-f60e2d7f7e15
type        : study_run_workflow
task queue  : study-runs
status      : COMPLETED
duration    : 254.6s

activities (17):
   1. update_run_status        timeout=  15s  max_attempts=5  attempt=1
   2. fetch_study_context      timeout=  30s  max_attempts=5  attempt=1
   3. embed_batch              timeout= 120s  max_attempts=5  attempt=1
   4. generate_reaction_batch  timeout= 180s  max_attempts=5  attempt=1
   5. embed_batch              timeout= 300s  max_attempts=5  attempt=1
   6. score_batch              timeout=  30s  max_attempts=5  attempt=1
   7. apply_penalties_batch    timeout=  30s  max_attempts=5  attempt=1
   8. persist_reactions        timeout=  30s  max_attempts=5  attempt=1
   9. generate_reaction_batch  timeout= 180s  max_attempts=5  attempt=1
  10. embed_batch              timeout= 300s  max_attempts=5  attempt=1
  11. score_batch              timeout=  30s  max_attempts=5  attempt=1
  12. apply_penalties_batch    timeout=  30s  max_attempts=5  attempt=1
  13. persist_reactions        timeout=  30s  max_attempts=5  attempt=1
  14. rollup_message_results   timeout=  30s  max_attempts=5  attempt=1
  15. update_run_status        timeout=  15s  max_attempts=5  attempt=1
  16. generate_run_report      timeout= 120s  max_attempts=5  attempt=1
  17. update_run_status        timeout=  15s  max_attempts=5  attempt=1

  sequence matches an APPROVED run over 2 batches.
  (ranking before the gate; report only after approval)

signals received     : ['finalize']
activity failures    : 0  (every activity succeeded on its first attempt)
Gate 2 review timer  : 1 started, 1 cancelled  (a signal released the wait before it expired)
continue_as_new hops : 0
total history events : 113
```

**`attempt=1` everywhere is what you want.** Anything higher is flagged
`<- RETRIED Nx` and means a flaky dependency.

A **rejected** run has no `generate_run_report` at all — that
difference is the architecture, visible in Temporal's own history.

---

# To run it again

```bash
cd ~/chorus/digital-twin
$PSQL < apps/api/scripts/reset_run.sql
```

If the previous workflow is still open, Temporal refuses a second one with the
same id. Check and kill it:

```bash
cd apps/engine
uv run python scripts/workflow.py status
uv run python scripts/workflow.py terminate    # only if it says RUNNING
```

---

# Every endpoint

```bash
curl -s localhost:8000/health | jq                                    # public
curl -s $API/me -H "$AUTH" | jq
curl -s $API/domains -H "$AUTH" | jq

# create a NEW run — reads core.* by study_id, writes 1 row (status=draft)
curl -s -X POST $API/studies/$STUDY/runs -H "$AUTH" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: $(uuidgen)" -d '{}' | jq

curl -s -X POST $API/runs/$RUN/estimate -H "$AUTH" | jq    # draft -> estimated
curl -s -X POST $API/runs/$RUN/approve  -H "$AUTH" | jq    # GATE 1, authorise spend
curl -s -X POST $API/runs/$RUN/start    -H "$AUTH" | jq    # -> queued

curl -s $API/runs/$RUN -H "$AUTH" | jq
curl -s $API/runs/$RUN/status -H "$AUTH" | jq
curl -N  $API/runs/$RUN/events -H "$AUTH"
curl -s "$API/studies/$STUDY/runs?limit=10" -H "$AUTH" | jq

curl -s -X POST "$API/runs/$RUN/finalize?note=ok" -H "$AUTH" | jq       # GATE 2
curl -s -X POST "$API/runs/$RUN/cancel?note=stop" -H "$AUTH" | jq       # graceful
curl -s -X POST "$API/runs/$RUN/cancel?force=true" -H "$AUTH" | jq      # hard kill

curl -s $API/runs/$RUN/results -H "$AUTH" | jq
```

Interactive docs: <http://localhost:8000/docs>

**The two approvals are different things:**

| | Endpoint | When | Meaning |
|---|---|---|---|
| **Gate 1** | `approve` | before `start` | "yes, spend the money" |
| — | `start` | — | pipeline runs; **report is not written yet** |
| **Gate 2** | `finalize` | at `awaiting_review` | "yes, I accept these results" → **generates the report**, completes the run |

---

# No token? Use the scripts instead

Same Temporal workflow, no auth, works with the default `dev@example.com` owner:

```bash
cd ~/chorus/digital-twin/apps/engine
uv run python scripts/workflow.py start       # = POST /start
uv run python scripts/workflow.py status      # = GET  /status
uv run python scripts/workflow.py finalize    # = POST /finalize  (Gate 2)
uv run python scripts/workflow.py cancel      # = POST /cancel
```

`estimate` and `approve` are API-only — no workflow exists yet at that point.

---

# Watching it in Temporal

<http://localhost:8080> → search `study-run-40000000-0000-0000-0000-000000000001`

| Tab | What to look for |
|---|---|
| **Summary** | `Running` while it works, `Completed` after you finalize |
| **History** | 13 `ActivityTask*` events. While parked at Gate 2 you see `TimerStarted` and nothing more — until your `finalize` arrives as `WorkflowExecutionSignaled`, immediately followed by `generate_run_report` |
| **Pending Activities** | **first place to look when stuck** — names the activity and its attempt count |
| **Workers** | confirms Terminal 2 is polling `study-runs` |

---

# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `404 No study exists with id 5000…` | The study is owned by someone else. Re-seed with `-v owner_email="'your@email'"` (Step 3). |
| `404 No run exists with id 4000…` | Same cause — ownership is checked through the run's study. |
| `401` on every call | Token missing, wrapped across lines, or expired (~1h). Re-run `get_dev_token.py`. |
| Seed fails: `null value in column "owner_id"` | That email has no user row yet. Call `GET /api/v1/me` with your token first (Step 2), then re-seed. |
| Run sits at `queued` | Terminal 2 isn't running. Start the worker — the task is durable and will pick up. |
| Stuck at 0 reactions, then fails | Embedding endpoint down. Re-check the curl in Terminal 1. |
| `Workflow execution already started` | Previous run still open. `workflow.py status` then `workflow.py terminate`. |
| `409` on `start` | Run isn't `approved`. The problem body names the expected statuses. |
| Prompt edits have no effect | Restart Terminal 2 — prompts are cached per process. |
| All personas sound the same | An `avatar_id` isn't mapped. `validate_run.py --phase config` catches this. |
