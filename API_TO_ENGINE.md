# API → Engine — What Each Endpoint Triggers

A demo-ready walkthrough: for every endpoint, the exact call chain from the
HTTP request down to the database write. Written to be read aloud.

**Overview:** [`ARCHITECTURE.md`](ARCHITECTURE.md) ·
**Run it:** [`STEPS.md`](STEPS.md) ·
**File by file:** [`CODE_WALKTHROUGH.md`](CODE_WALKTHROUGH.md)

---

## The one thing to say first

> The API never runs the pipeline. It only **writes config** and **talks to
> Temporal**. The engine worker does every piece of real work, and it owns the
> run's status from `running` onward.

Only **one** endpoint starts the engine: `POST /runs/{id}/start`. Two more send
it messages once it is running (`finalize`, `cancel`). Everything else is pure
database work inside the API.

| Endpoint | Touches the engine? | What it really does |
|---|---|---|
| `POST /studies/{id}/runs` | no | builds `config_snapshot`, writes one row |
| `POST /runs/{id}/estimate` | no | arithmetic on that snapshot |
| `POST /runs/{id}/approve` | no | one status write — **Gate 1** |
| `POST /runs/{id}/start` | **yes — starts the workflow** | hands off to Temporal |
| `POST /runs/{id}/finalize` | **yes — signal** | **Gate 2**, releases the wait |
| `POST /runs/{id}/cancel` | **yes — signal or terminate** | cooperative stop |
| `GET /runs/{id}/status` | no | reads Postgres |
| `GET /runs/{id}/events` | no | polls Postgres, streams SSE |
| `GET /runs/{id}/results` | no | reads Postgres |

---

## Every request starts the same way

```
HTTP request
  └─ deps.py :: get_current_user
       ├─ extract_bearer(Authorization)            auth.py
       ├─ validate_token()  RS256 vs Entra JWKS    auth.py
       ├─ SELECT * FROM core.users WHERE auth_provider_id = <oid>
       └─ no row? INSERT one (JIT provisioning)
  └─ runs.py :: _load_run(run_id, user_id)
       ├─ SELECT * FROM runs.runs WHERE id = …
       └─ _owns_study() — SELECT 1 FROM core.studies
                          WHERE id = … AND owner_id = <caller>
            not owned  ->  404, never 403
```

> **Demo note.** A run you do not own reads as **404, not 403** — on purpose, so
> the API never confirms that someone else's id exists. This is also the single
> most common "it's broken" moment: the row is there, it just isn't yours.

---

## 1 · `POST /studies/{study_id}/runs` — create

**Engine involvement: none.** This is where the run's entire configuration is
frozen.

```
create_run()                                        runs.py:319
  ├─ _owns_study()                          404 if not yours
  ├─ idempotency.claim()                    Idempotency-Key replay → same run
  ├─ _build_config_snapshot(session, study_id, body)      runs.py:377
  │    ├─ SELECT name, domain_id, d.name, outcome_dimension, intent
  │    │       FROM core.studies JOIN core.domains
  │    ├─ SELECT id, text        FROM core.messages       -> claims[]
  │    ├─ SELECT avatar_id       FROM core.study_avatars  -> avatar_ids[]
  │    ├─ SELECT id, scale_point, text FROM core.anchors  -> anchors[]
  │    ├─ intent->'penalties'                             -> penalties[]
  │    └─ body.respondents_per_avatar                     -> panel size
  ├─ _pair_count(snapshot) == 0  ->  422
  └─ INSERT INTO runs.runs (status='draft', config_snapshot=…)
```

**Writes:** one `runs.runs` row.
**Key point for the demo:** the snapshot carries the claim and anchor *text*,
not just ids — so editing the study afterwards cannot change what this run
executes.

---

### Is `seed_hardcoded_run.sql` still needed once the API creates runs?

**Partly.** The file does two separable jobs:

| Sections | What they create | Can the API do it? |
|---|---|---|
| **1–7** | dev user, domain, study, anchors, claims, avatars, study↔avatar links | **No.** There is no endpoint that creates a domain, study, claim, anchor or avatar — only `GET /domains`. This file is the only way to get study data in. |
| **8** | one `runs.runs` row with a literal `config_snapshot` | **Yes.** `POST /studies/{study_id}/runs` does exactly this, by reading sections 1–7 back out of the tables. |

So skip section 8 when the API owns run creation:

```bash
docker exec -i chorus-postgres psql -U nms3 -d chorus \
  -v skip_run=1 < apps/api/scripts/seed_hardcoded_run.sql
```

It then prints the study id and tells you to `POST /studies/{id}/runs`.

Keep section 8 (the default) for the **no-auth path** — `workflow.py` starts a
run that already exists and never touches the API, so it needs that row.

Verified: a run created purely by `_build_config_snapshot` — no literal JSON
anywhere — produced the same 8-key snapshot, passed all 32 config checks, ran
100 reactions across 4 personas × 5 respondents × 5 claims, and passed all 66
result checks.

---

## 2 · `POST /runs/{run_id}/estimate`

**Engine involvement: none. No LLM call.** Deliberately arithmetic.

```
estimate_run()                                      runs.py:487
  ├─ _require_status(draft | configured | estimated)
  ├─ personas    = len(snapshot.avatar_ids)
  ├─ respondents = snapshot.respondents_per_avatar
  ├─ claims      = len(snapshot.claims)
  ├─ reactions   = personas × respondents × claims
  ├─ time/cost   = reactions × APP_ESTIMATE_* bounds
  ├─ _estimate_advice()   e.g. "only 8 respondents — ranking will be noisy"
  └─ UPDATE runs.runs SET estimate = …, status = 'estimated'
```

**Writes:** `runs.runs.estimate`, `status`.

---

## 3 · `POST /runs/{run_id}/approve` — **GATE 1**

**Engine involvement: none.** No workflow exists yet. This is the money gate.

```
approve_run()                                       runs.py:554
  ├─ _require_status(estimated)
  └─ UPDATE runs.runs SET status = 'approved'
```

---

## 4 · `POST /runs/{run_id}/start` — **the engine starts here**

This is the only endpoint that creates a workflow.

```
start_run()                                         runs.py:588
  ├─ _require_status(approved)
  ├─ _assert_no_active_run()        409 if the caller's slot is full
  ├─ UPDATE runs.runs SET status='queued',
  │         workflow_id='study-run-<run_id>'
  ├─ COMMIT                          <-- BEFORE talking to Temporal
  └─ client.start_workflow(
         "study_run_workflow",
         { run_id, study_id, review_gate_enabled, review_timeout_seconds },
         id="study-run-<run_id>", task_queue="study-runs")
       on failure -> roll back to 'approved', clear workflow_id, re-raise
```

**Two things worth saying out loud:**

1. The commit happens **before** `start_workflow`, so a crash between them can
   never leave a run looking `approved` while a workflow is already grinding.
2. `running` is **not** written here. The workflow's own first activity writes
   it — so the status is true even if the API process dies immediately after.

### …and then the engine takes over

```
Temporal server  ──dispatch──>  worker.py (polling 'study-runs')
                                  └─ StudyRunWorkflow.run(input)
```

| # | Activity | Reads | Writes |
|---|---|---|---|
| 1 | `update_run_status` | — | `runs.runs`: status=**running**, started_at |
| 2 | `fetch_study_context` | **`runs.runs.config_snapshot` BY run_id**, `core.studies`, `core.avatars` (names), `avatar_prompts.txt` | — |
| 3 | `embed_batch` | anchors (once for the whole run) | — |
| | **batch loop**, `batch_size` reactions at a time | | |
| 4 | `generate_reaction_batch` | persona prompt + KBQ + claim → Azure OpenAI | — |
| 5 | `embed_batch` | reaction texts (chunked ≤20 KB) | — |
| 6 | `score_batch` | cosine → shift → pmf → E[scale] | — |
| 7 | `apply_penalties_batch` | substring hit → +adjustment | — |
| 8 | `persist_reactions` | — | **`runs.run_reactions`** |
| | *repeat; `continue_as_new` every `max_batches_per_run`* | | |
| 9 | `rollup_message_results` | `runs.run_reactions` | **`runs.run_message_results`** |
| 10 | `update_run_status` | — | status=**awaiting_review**, coverage_pct |
| | **⏸ GATE 2 — the workflow blocks here** | | |
| 11 | `generate_run_report` *(only if approved)* | ranking + reactions + 2 LLM calls | **`runs.run_reports`** |
| 12 | `update_run_status` | — | status=**finalized**, finished_at |

### How `fetch_study_context` reads the parameters

One query is the whole story:

```sql
SELECT config_snapshot FROM runs.runs WHERE id = $1   -- $1 = run_id
```

```
fetch_study_context(run_id, study_id)              activities.py
  ├─ kbq        = config["kbq"]        or study.outcome_dimension
  ├─ claims     = config["claims"]     or core.messages    (fallback)
  ├─ avatar_ids = config["avatar_ids"] or study_avatars    (fallback)
  ├─ anchors    = config["anchors"]    or core.anchors     (fallback)
  ├─ penalties  = config["penalties"]
  ├─ respondents= config["respondents_per_avatar"]  (default 1)
  ├─ verify every avatar resolves to a prompt   -> raise BEFORE any spend
  └─ return StudyContext(sources, NOT an expanded pair list)
```

The cross product is expanded **one batch at a time** by `pairs_for_slice()`,
so the workflow's carried state stays ~1 KB whether the run is 100 reactions or
100,000.

---

## 5 · `POST /runs/{run_id}/finalize` — **GATE 2**

**Engine involvement: a signal.** The API does **not** write `finalized`.

```
finalize_run()                                      runs.py:682
  ├─ _require_status(awaiting_review)
  └─ handle.signal("finalize", note)
        └─ StudyRunWorkflow.finalize()  ->  self._decision = "approve"
              └─ releases workflow.wait_condition(...)
                   └─ generate_run_report   -> runs.run_reports
                   └─ update_run_status     -> finalized
```

Returns **202 with the OLD status still in the body** — the workflow writes the
new one a moment later. That is not a bug; it is the ownership rule.

---

## 6 · `POST /runs/{run_id}/cancel`

Four branches, and the demo-worthy one is the last.

```
cancel_run()                                        runs.py:716
  ├─ already terminal            -> 409
  ├─ no workflow yet             -> UPDATE status='cancelled'   (API writes it)
  ├─ ?force=true                 -> handle.terminate()          (hard kill)
  └─ default                     -> handle.signal("cancel")     (cooperative)
         └─ the workflow checks the flag at 9 safe points,
            finishes the in-flight batch, persists it,
            then writes 'cancelled' itself
```

**Cancelling from `awaiting_review` is how results are rejected.** The ranking
stays in the database and stays readable through `/results`; the report is
simply never generated — so a rejection costs **zero** LLM calls.

---

## 7 · `GET /runs/{id}/status` and `/events`

**Engine involvement: none — deliberately.**

```
get_run_status()                                    runs.py:820
  └─ _progress(session, run)
       ├─ SELECT count(*) FROM runs.run_reactions WHERE run_id = …
       └─ _pair_count(config_snapshot)
              = len(avatar_ids) × respondents_per_avatar × len(claims)
```

Both numbers come from Postgres, not from a Temporal query — so status works
even with **no worker running** and never blocks on the workflow being
reachable. `/events` is the same two numbers polled on an interval and pushed
as SSE.

---

## 8 · `GET /runs/{id}/results`

```
get_run_results()                                   runs.py:932
  ├─ SELECT … FROM runs.run_message_results JOIN core.messages  -> ranking
  └─ SELECT … FROM runs.run_reports                             -> report
```

Two things arrive at **different times**, which is the point of the
architecture:

| Field | Available from | Survives a rejection? |
|---|---|---|
| `ranking` | `awaiting_review` — **before** the gate | **yes** |
| `report` | only after `finalize` | **no** — never written |

Never errors for a run without results; it returns the empty shape.

---

## Where each metric is saved

| Metric | Table | Written by | When |
|---|---|---|---|
| reaction text, score, pmf, penalty, ok/failed | `runs.run_reactions` | `persist_reactions` | after each batch |
| rank, BT strength, aggregate score, recommendation | `runs.run_message_results` | `rollup_message_results` | before Gate 2 |
| narrative, baseline lift, cohorts, penalty hits | `runs.run_reports` | `generate_run_report` | after approval |
| status, coverage_pct, started/finished, error | `runs.runs` | `update_run_status` | every transition |
| estimate | `runs.runs.estimate` | `estimate_run` (API) | at estimate |
| **config_snapshot** | `runs.runs` | `_build_config_snapshot` (API) | at create |

There is deliberately **no summary column** duplicating the results: the
"predicted winner", margin, reaction counts and score statistics are all
queries over the two result tables.

---

## Handling a big panel

Everything below is configurable — nothing about panel size is hardcoded.

| Knob | Where | Default | Raise/lower when |
|---|---|---|---|
| `respondents_per_avatar` | `config_snapshot` (per run) | 1 | more respondents = tighter ranking, linear cost |
| `batch_size` | workflow input | 50 | smaller batches = finer progress, more overhead |
| `max_batches_per_run` | workflow input | 20 | how often `continue_as_new` starts a fresh history |
| `APP_REACTION_CONCURRENCY` | engine `.env` | 8 | lower it if Azure rate-limits |
| `APP_EMBEDDING_MAX_REQUEST_BYTES` | engine `.env` | 20 000 | the embedding service 500s past ~25 KB |
| `APP_EMBEDDING_BATCH_SIZE` | engine `.env` | 25 | upper bound on texts per request |

```bash
# tune batching at start time
uv run python scripts/workflow.py start --batch-size 25 --max-batches 10
```

### Three things that make a heavy run safe

1. **Constant-size workflow state.** The cross product is never materialised:
   `StudyRunInput` carries avatar ids, claims and a cursor, and
   `pairs_for_slice()` expands one batch on demand. A 15,000-reaction run
   carries the same ~1.4 KB as a 100-reaction one. *(Inlining the pairs put a
   50-respondent run at 2.8 MB, past Temporal's 2 MB payload limit.)*
2. **Per-reaction failure isolation.** One failed LLM call is stored as
   `status='failed'` and excluded from the ranking; the other 49 in the batch
   are kept. Only a systemic failure — more than half a batch — raises, so
   Temporal's retry covers the case worth retrying.
3. **Bounded history.** `continue_as_new` starts a fresh Temporal history every
   `max_batches_per_run` batches, carrying only the cursor.
   `inspect_workflow.py` follows the chain and reports the hop count.

---

## Demo script — six commands

```bash
RUN=40000000-0000-0000-0000-000000000001

curl -s -X POST $API/runs/$RUN/approve -H "$AUTH" | jq       # Gate 1
curl -s -X POST $API/runs/$RUN/start   -H "$AUTH" | jq       # engine starts
curl -s $API/runs/$RUN/status -H "$AUTH" | jq                # poll
curl -s $API/runs/$RUN/results -H "$AUTH" | jq '{status, winner:.ranking[0].text, report}'
                                                             # report is null — correct
curl -s -X POST "$API/runs/$RUN/finalize?note=ok" -H "$AUTH" | jq   # Gate 2
curl -s $API/runs/$RUN/results -H "$AUTH" | jq -r '.report'  # now it exists
```

Show the worker log beside it — every stage prints a line — and the Temporal UI
at <http://localhost:8080> for the activity history, retry counts and the Gate 2
timer.
