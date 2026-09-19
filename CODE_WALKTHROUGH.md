# Code Walkthrough — Line by Line, Start to Finish

Every function, class and database write on the path from an API call to a
finished report, with file names and line numbers.

Line numbers were extracted from the source, not typed by hand. If a number
drifts, regenerate with:

```bash
python - <<'PY'
import ast, pathlib
for f in ("apps/api/app/api/v1/runs.py", "apps/engine/app/activities.py",
          "apps/engine/app/workflows/study_run.py"):
    t = ast.parse(pathlib.Path(f).read_text())
    print(f"\n### {f}")
    for n in ast.walk(t):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            print(f"  {n.lineno:>4}  {n.name}")
PY
```

**Companions:** [`ARCHITECTURE.md`](ARCHITECTURE.md) (one-diagram overview) ·
[`API_TO_ENGINE.md`](API_TO_ENGINE.md) (per-endpoint demo script) ·
[`STEPS.md`](STEPS.md) (run it)

---

## Contents

1. [Your mental model — confirmed](#1-your-mental-model--confirmed)
2. [The file map with line numbers](#2-the-file-map-with-line-numbers)
3. [Stage A — the UI writes study data](#stage-a--the-ui-writes-study-data)
4. [Stage B — `POST /studies/{id}/runs` builds the snapshot](#stage-b--post-studiesidruns-builds-the-snapshot)
5. [Stage C — estimate and approve](#stage-c--estimate-and-approve)
6. [Stage D — `POST /runs/{id}/start` hands off to Temporal](#stage-d--post-runsidstart-hands-off-to-temporal)
7. [Stage E — the worker picks it up](#stage-e--the-worker-picks-it-up)
8. [Stage F — reading the parameters](#stage-f--reading-the-parameters)
9. [Stage G — the batch loop](#stage-g--the-batch-loop)
10. [Stage H — ranking](#stage-h--ranking)
11. [Stage I — Gate 2](#stage-i--gate-2)
12. [Stage J — the report](#stage-j--the-report)
13. [Every database write, in order](#13-every-database-write-in-order)
14. [Class reference](#14-class-reference)

---

## 1. Your mental model — confirmed

> The UI inserts the study data into the `core.*` tables. Then
> `POST /studies/{study_id}/runs` reads it all back, restructures it, and
> inserts **one row** into `runs.runs` with status `draft`. That is all it does.
> `seed_hardcoded_run.sql` also contains that run INSERT, which is how we do it
> manually for now. Then `start` reads `config_snapshot` and begins the process.

Correct, with **one addition**: `start` requires status `approved`, not `draft`.

```
create ──> draft ──estimate──> estimated ──approve──> approved ──start──> queued
```

[`runs.py:110`](apps/api/app/api/v1/runs.py#L110) declares
`_STARTABLE = {S.APPROVED}`, and [`runs.py:650`](apps/api/app/api/v1/runs.py#L650)
enforces it. Calling `start` on a `draft` run returns **409**, not 202.

The seed sidesteps this by inserting the row already at `'approved'`
([`seed_hardcoded_run.sql:312`](apps/api/scripts/seed_hardcoded_run.sql)), which
is why `start` works immediately against the seeded run.

### Is `seed_hardcoded_run.sql` still needed once the API creates runs?

**Partly.** The file does two separable jobs:

| Sections | What they create | Can the API do it? |
|---|---|---|
| **1–7** | dev user, domain, study, anchors, claims, avatars, study↔avatar links | **No.** There is no endpoint that creates any of them — only `GET /domains`. This file is the only way to get study data in. |
| **8** | one `runs.runs` row with a literal `config_snapshot` | **Yes.** `POST /studies/{study_id}/runs` does exactly this, by reading sections 1–7 back out of the tables. |

Skip section 8 when the API owns run creation:

```bash
docker exec -i chorus-postgres psql -U nms3 -d chorus \
  -v skip_run=1 < apps/api/scripts/seed_hardcoded_run.sql
```

It then prints the study id and tells you to `POST /studies/{id}/runs`. Keep it
(the default) for the **no-auth path** — `workflow.py` starts a run that already
exists and never touches the API.

Verified: a run created purely by `_build_config_snapshot`, with no literal JSON
anywhere, produced the same 8-key snapshot, passed all 32 config checks, ran 100
reactions, and passed all 66 result checks.

**So the two paths are:**

| | Today (manual) | With the UI |
|---|---|---|
| study data | `seed_hardcoded_run.sql` sections 1–7 | UI writes `core.*` |
| run row | seed section 8, literal JSON, at `approved` | `POST /studies/{id}/runs` → `draft` |
| then | `start` | `estimate` → `approve` → `start` |

Skip the seed's run row with `-v skip_run=1` when the API owns run creation.

---

## 2. The file map with line numbers

| File | Lines | Role |
|---|---|---|
| [`apps/api/app/api/v1/runs.py`](apps/api/app/api/v1/runs.py) | 1027 | every run endpoint + state machine + snapshot builder |
| [`apps/api/app/api/deps.py`](apps/api/app/api/deps.py) | 133 | auth, user JIT-provisioning, DB session |
| [`apps/api/app/core/temporal.py`](apps/api/app/core/temporal.py) | 23 | shared Temporal client |
| [`apps/api/app/db/models/run.py`](apps/api/app/db/models/run.py) | 69 | `Run` ORM model + `RunStatus` enum |
| [`apps/engine/app/worker.py`](apps/engine/app/worker.py) | 52 | worker entrypoint; registers 1 workflow + 9 activities |
| [`apps/engine/app/workflows/study_run.py`](apps/engine/app/workflows/study_run.py) | 494 | the durable orchestrator |
| [`apps/engine/app/activities.py`](apps/engine/app/activities.py) | 1142 | all I/O, maths and DB writes |
| [`apps/engine/app/avatar_prompts.py`](apps/engine/app/avatar_prompts.py) | 161 | persona prompt loader |
| [`apps/engine/app/llm.py`](apps/engine/app/llm.py) | 45 | Azure OpenAI client |
| [`apps/engine/app/db.py`](apps/engine/app/db.py) | 17 | asyncpg pool |

---

## Stage A — the UI writes study data

**No code of ours runs here yet.** There is no endpoint that creates a domain,
study, claim, anchor or avatar — the API exposes only `GET /api/v1/domains`.
Today `seed_hardcoded_run.sql` sections 1–7 do it.

| Table | Holds | Why it must exist first |
|---|---|---|
| `core.users` | the owner | `core.studies.owner_id` is `NOT NULL REFERENCES` |
| `core.domains` | market context | studies belong to a domain |
| `core.studies` | the KBQ in `outcome_dimension`; penalties in `intent->'penalties'` | the snapshot is built from it |
| `core.anchors` | 1–5 reference sentences | define the scale |
| `core.messages` | the claims | **FK target** of `run_reactions.message_id` |
| `core.avatars` | persona identity; `profile` is a pointer string | **FK target** of `run_reactions.avatar_id` |
| `core.study_avatars` | which personas are on this study | read by the snapshot builder |

---

## Stage B — `POST /studies/{id}/runs` builds the snapshot

### Entry: `create_run` — [`runs.py:351`](apps/api/app/api/v1/runs.py#L351)

```
create_run(study_id, session, current_user, body, idempotency_key)
 │
 ├─ deps.get_current_user            deps.py:53
 │    ├─ extract_bearer / validate_token       core/auth.py
 │    ├─ SELECT * FROM core.users WHERE auth_provider_id = <oid>
 │    └─ INSERT if absent (JIT provisioning)
 │
 ├─ _owns_study(session, study_id, user_id)    runs.py:145
 │    └─ SELECT 1 FROM core.studies
 │        WHERE id = :sid AND owner_id = :uid AND deleted_at IS NULL
 │       → not yours ⇒ not_found() ⇒ HTTP 404 (never 403)
 │
 ├─ idempotency.claim(...)                     core/idempotency.py
 │    └─ replay of an Idempotency-Key returns the ORIGINAL run
 │
 ├─ _build_config_snapshot(session, study_id, body)   runs.py:407   ◀── the work
 │
 ├─ _pair_count(snapshot) == 0 ⇒ unprocessable() ⇒ HTTP 422   runs.py:245
 │
 ├─ RunRow(study_id=…, status=S.DRAFT, config_snapshot=snapshot)  runs.py:385
 ├─ session.add(run); await session.commit()          ◀── THE 1 ROW
 └─ idempotency.record(...)
```

### `_build_config_snapshot` — [`runs.py:407`](apps/api/app/api/v1/runs.py#L407)

Five reads, one dict out. This is the "read from all the other tables by
study_id and restructure it" step:

| # | Query | Produces |
|---|---|---|
| 1 | `SELECT s.name, s.domain_id, d.name, s.outcome_dimension, s.intent FROM core.studies s JOIN core.domains d` | `kbq`, `domain`, `study` |
| 2 | `SELECT id, text FROM core.messages WHERE study_id = :sid ORDER BY position, id` | `claims[]` |
| 3 | `SELECT avatar_id FROM core.study_avatars WHERE study_id = :sid` | `avatar_ids[]` |
| 4 | `SELECT id, scale_point, text FROM core.anchors WHERE scope_id = :sid OR domain` | `anchors[]` |
| 5 | `intent->'penalties'` (already loaded in 1) | `penalties[]` |
| — | `body.respondents_per_avatar` | panel size |

Output — the 8 keys:

```jsonc
{
  "kbq": "…",                       // read by the engine
  "claims":     [{"id","text"}],    // read by the engine
  "avatar_ids": ["…"],              // read by the engine
  "respondents_per_avatar": 5,      // read by the engine
  "anchors":    [{"id","scale_point","text"}],  // read by the engine
  "penalties":  [{"trigger","adjustment","reason"}],  // read by the engine

  "domain": {"id","name"},          // provenance only
  "study":  {"id","name"},          // provenance only
  "snapshot_at": "…"                // provenance only
}
```

**Why the text and not just ids:** the snapshot is frozen here. Editing
`core.messages` afterwards cannot change what this run executes.

**Result of Stage B:** exactly **one** `runs.runs` row, `status='draft'`.
Nothing else happens. No Temporal, no engine, no LLM.

---

## Stage C — estimate and approve

### `estimate_run` — [`runs.py:530`](apps/api/app/api/v1/runs.py#L530)

Arithmetic only, no LLM:

```
personas    = len(snapshot["avatar_ids"])
respondents = _respondents_per_avatar(snapshot)        runs.py:231
claims      = len(snapshot["claims"])
reactions   = personas * respondents * claims
time/cost   = reactions * APP_ESTIMATE_* bounds
advice      = _estimate_advice(...)                    runs.py:573
```

**Writes:** `runs.runs.estimate`, `status='estimated'`.

### `approve_run` — [`runs.py:596`](apps/api/app/api/v1/runs.py#L596) — **GATE 1**

`_require_status(run, _APPROVABLE, "approve")`, then
`status='approved'`. No workflow exists yet. This is the spend authorisation.

---

## Stage D — `POST /runs/{id}/start` hands off to Temporal

### `start_run` — [`runs.py:630`](apps/api/app/api/v1/runs.py#L630)

```
 1. _load_run(session, run_id, user_id)              runs.py:132
 2. _require_status(run, _STARTABLE, "start")        runs.py:650   ⇒ 409 unless approved
 3. _assert_no_active_run(session, user)             runs.py:686   ⇒ 409 if slot full
 4. run.status      = S.QUEUED
    run.workflow_id = f"study-run-{run_id}"
    await session.commit()                     ◀── COMMIT BEFORE TEMPORAL
 5. get_temporal_client()                            core/temporal.py:19
    await client.start_workflow(
        STUDY_RUN_WORKFLOW_NAME,                     core/temporal.py (= "study_run_workflow")
        { run_id, study_id, review_gate_enabled, review_timeout_seconds },
        id=run.workflow_id, task_queue=settings.task_queue)
 6. on exception → status back to APPROVED, workflow_id=None, re-raise
```

Two deliberate choices:

- **Step 4 before step 5.** A crash between them can never leave a run looking
  `approved` while a workflow is already running.
- **`running` is not written here.** The workflow's first activity writes it, so
  the status is true even if this process dies immediately after returning 202.

---

## Stage E — the worker picks it up

### `worker.py:24` — `main()`

```python
pool   = await create_pool()                    db.py:13
client = await Client.connect(settings.temporal_host, namespace=…)
data   = StudyDataActivities(pool)              activities.py:522
with ThreadPoolExecutor(max_workers=10) as ex:
    Worker(client, task_queue="study-runs",
           workflows=[StudyRunWorkflow],        study_run.py:105
           activities=[ embed_batch,            activities.py:308
                        score_batch,            activities.py:342
                        generate_reaction_batch,activities.py:377
                        apply_penalties_batch,  activities.py:434
                        data.fetch_study_context,     activities.py:527
                        data.persist_reactions,       activities.py:689
                        data.rollup_message_results,  activities.py:713
                        data.generate_run_report,     activities.py:812
                        data.update_run_status ],     activities.py:953
           activity_executor=ex)
```

The first four are plain `def` — blocking (`requests`, OpenAI SDK, numpy) — so
they run on the thread pool. The five `StudyDataActivities` methods are
`async def` because asyncpg is natively async.

### `StudyRunWorkflow.run` — [`study_run.py:179`](apps/engine/app/workflows/study_run.py#L179)

The orchestrator. Contains **no I/O** — Temporal replays it to recover from
crashes, so it must be deterministic.

---

## Stage F — reading the parameters

### Activity 1 · `update_run_status` — [`activities.py:953`](apps/engine/app/activities.py#L953)

```sql
UPDATE runs.runs
   SET status='running', started_at=COALESCE($3, started_at), … WHERE id=$1
```

Every optional field uses `COALESCE($n, column)` so a status-only call cannot
null out a previously written timestamp. **The only writer of run status from
`running` onward.**

### Activity 2 · `fetch_study_context` — [`activities.py:527`](apps/engine/app/activities.py#L527)

**The answer to "how does start read the params".** One query is the whole
story:

```sql
SELECT config_snapshot FROM runs.runs WHERE id = $1   -- $1 = run_id
```

```
fetch_study_context(run_id, study_id)
 ├─ SELECT domain_id, outcome_dimension FROM core.studies WHERE id=$1
 ├─ SELECT config_snapshot FROM runs.runs WHERE id=$1      ◀── BY RUN ID
 ├─ kbq         = config["kbq"] or study.outcome_dimension
 ├─ penalties   = [Penalty(...) for p in config["penalties"]]   activities.py:52
 ├─ anchors     = _anchors_from_config(config)       activities.py:447
 │                 └─ else _anchors_from_db(...)     activities.py:632  (fallback)
 ├─ claims      = _claims_from_config(config)        activities.py:473
 │                 └─ else _claims_from_db(...)      activities.py:655  (fallback)
 ├─ respondents = _respondents_per_avatar(config)    activities.py:491
 ├─ avatar_ids  = _avatar_ids_from_config(config)    activities.py:508
 ├─ _avatar_names(conn, avatar_ids)                  activities.py:672
 │     └─ raises if any id is missing from core.avatars — BEFORE any spend
 ├─ verify every avatar resolves to a prompt — raises otherwise
 └─ return StudyContext(kbq, anchors, avatar_ids, claims, penalties, respondents)
                                                     activities.py:59
```

**`StudyContext` carries SOURCES, not an expanded pair list.** That is what
keeps the workflow's payload constant regardless of panel size.

### Activity 3 · `embed_batch` (anchors) — [`activities.py:308`](apps/engine/app/activities.py#L308)

Embeds the 5 anchor texts **once for the whole run**. Chunks its HTTP requests
via `_embedding_chunks` ([`activities.py:280`](apps/engine/app/activities.py#L280)),
capped on bytes because the service returns 500 past ~25 KB.

---

## Stage G — the batch loop

`study_run.py:179`, inside `while processed < self._total`:

```
pairs_for_slice(avatar_ids, claims, respondents, processed, batch_size)
                                                     activities.py:165
   └─ pure index arithmetic — expands ONLY this batch
       avatar_idx, rem   = divmod(i, respondents*len(claims))
       respondent, claim = divmod(rem, len(claims))
       → list[Pair]                                  activities.py:22
```

| # | Activity | Line | What it does |
|---|---|---|---|
| 4 | `generate_reaction_batch` | [377](apps/engine/app/activities.py#L377) | one LLM call per Pair, fanned across `APP_REACTION_CONCURRENCY` threads |
| | ├ `prompt_for_avatar` | [avatar_prompts.py:128](apps/engine/app/avatar_prompts.py#L128) | id → persona key → prompt text (resolved once per avatar per batch) |
| | ├ `_reaction_prompt` | [356](apps/engine/app/activities.py#L356) | the user message: CLAIM + KBQ + "do NOT give a numeric rating" |
| | ├ `_one` | [397](apps/engine/app/activities.py#L397) | per-call try/except → `ReactionResult(ok=False)` on failure |
| | └ `llm.call_chat` | [llm.py:29](apps/engine/app/llm.py#L29) | system = persona prompt, user = the above |
| 5 | `embed_batch` | [308](apps/engine/app/activities.py#L308) | embeds the reaction texts, chunked |
| 6 | `score_batch` | [342](apps/engine/app/activities.py#L342) | calls `_compute_pmf` per reaction |
| | └ `_compute_pmf` | [212](apps/engine/app/activities.py#L212) | cosine → shift → normalise → `E[scale]` |
| | └ `_cosine_similarity` | [206](apps/engine/app/activities.py#L206) | |
| 7 | `apply_penalties_batch` | [434](apps/engine/app/activities.py#L434) | calls `_apply_penalties` per reaction |
| | └ `_apply_penalties` | [222](apps/engine/app/activities.py#L222) | substring hit → **adds** to the score |
| 8 | `persist_reactions` | [689](apps/engine/app/activities.py#L689) | **INSERT → `runs.run_reactions`** |

### The scoring maths — `_compute_pmf`, [`activities.py:212`](apps/engine/app/activities.py#L212)

```python
sims     = [cosine(response_vec, av) for av in anchor_vecs]   # 1 similarity
adjusted = [s - min(sims) + delta for s in sims]              # 2 shift
pmf      = [a / sum(adjusted) for a in adjusted]              # 3 normalise
mean_ssr = sum(sp * p for sp, p in zip(scale_points, pmf))    # 4 expected value
```

Step 2 is the load-bearing one: sentence-embedding cosines cluster in 0.6–0.9,
so without re-basing the minimum, every claim would score ≈3.0.

### `persist_reactions` — [`activities.py:689`](apps/engine/app/activities.py#L689)

```sql
INSERT INTO runs.run_reactions
    (run_id, avatar_id, message_id, respondent,
     score, distribution, penalty, reaction, status)
VALUES ($1,…,$9)
ON CONFLICT (run_id, avatar_id, message_id, respondent)
DO UPDATE SET …, updated_at = now()
```

Idempotent by construction — Temporal may retry after a partial failure.

A reaction that failed to generate is written with `status='failed'` and NULL
score/distribution/text ([`study_run.py`](apps/engine/app/workflows/study_run.py),
the `ReactionRow` construction), so it simply does not vote.

### Between batches

`continue_as_new` after `max_batches_per_run` batches, carrying only the
cursor — `processed_so_far` — not the remaining work.

---

## Stage H — ranking

### `rollup_message_results` — [`activities.py:713`](apps/engine/app/activities.py#L713)

```
SELECT avatar_id, respondent, message_id, score
  FROM runs.run_reactions
 WHERE run_id=$1 AND status='ok' AND score IS NOT NULL
   │
   ├─ by_respondent[(avatar_id, respondent)][message_id] = score
   │     ◀── keyed on the JUDGE, not the persona. Keying on the persona alone
   │         would collapse all N respondents and keep only the last one.
   │
   ├─ for each judge: every pair of claims = one comparison, lower score wins
   ├─ _bradley_terry(n, wins)                          activities.py:235
   │     500-iteration minorisation-maximisation, strengths sum to 1
   ├─ rank = sorted by strength desc
   ├─ recommendation: rank 1 → 'recommended'; ≤ max(2, n//3) → 'runner_up'; else 'drop'
   └─ INSERT INTO runs.run_message_results
          (run_id, message_id, aggregate_score, bt_strength, rank, recommendation)
      ON CONFLICT (run_id, message_id) DO UPDATE …
```

Returns rank 1 so the workflow can log the winner — **nothing is stored twice**.

---

## Stage I — Gate 2

`update_run_status` → `status='awaiting_review'`, `coverage_pct`.

Then [`study_run.py:463`](apps/engine/app/workflows/study_run.py#L463):

```python
await workflow.wait_condition(
    lambda: self._decision is not None or self._cancel_requested,
    timeout=timedelta(seconds=review_timeout_seconds),
)
```

A genuine server-side wait — **no thread, process or connection pinned.** Three
exits:

| Signal | Handler | Result |
|---|---|---|
| `finalize` | [`study_run.py:121`](apps/engine/app/workflows/study_run.py#L121) | report is generated, `finalized` |
| `cancel` | [`study_run.py:143`](apps/engine/app/workflows/study_run.py#L143) | `cancelled`, **no report** |
| timeout | — | `expired`, **no report** |

The API side: `finalize_run` [`runs.py:724`](apps/api/app/api/v1/runs.py#L724)
sends the signal via `_signal` [`runs.py:183`](apps/api/app/api/v1/runs.py#L183)
and returns **202 with the OLD status** — the workflow writes the new one.

`cancel_run` [`runs.py:758`](apps/api/app/api/v1/runs.py#L758) has four
branches; `_write_cancelled` [`runs.py:845`](apps/api/app/api/v1/runs.py#L845)
is the API-writes-it fallback.

---

## Stage J — the report

### `generate_run_report` — [`activities.py:812`](apps/engine/app/activities.py#L812)

**Runs only after approval.** Six sections, two LLM calls.

```
├─ SELECT s.outcome_dimension, r.config_snapshot FROM runs.runs JOIN core.studies
├─ SELECT rmr.*, m.text FROM run_message_results JOIN core.messages   → rankings
├─ SELECT rr.*, a.name FROM run_reactions JOIN core.avatars           → reactions
├─ by_respondent keyed on (avatar_id, respondent)
├─ cohorts via _persona_family(avatar_name)          activities.py:257
├─ penalty_hits: re-run _apply_penalties per reaction to recover WHICH fired
├─ llm.call_chat(_exec_summary_prompt(...))          activities.py:1001  ◀ LLM 1
├─ llm.call_chat(_interpretation_prompt(...))        activities.py:1025  ◀ LLM 2
├─ _assemble_report_md(...)                          activities.py:1109
│    ├─ _rankings_table_md                           activities.py:1053
│    ├─ _cohort_breakdown_md                         activities.py:1061
│    ├─ _penalty_impact_md                           activities.py:1087
│    └─ _strategic_recommendation_md                 activities.py:1097
├─ _baseline_lift_pct(winner, lowest)                activities.py:978
│    NULL when lowest == 0 or the value exceeds numeric(6,2)
└─ INSERT INTO runs.run_reports (run_id, report, baseline_lift_pct, summary)
   ON CONFLICT (run_id) DO UPDATE …
```

Then `update_run_status` → `status='finalized'`, `finished_at`.

---

## 13. Every database write, in order

| # | Table | Written by | File:line | Rows (100-reaction run) |
|---|---|---|---|---|
| 1 | `core.users` | `get_current_user` (JIT) | [deps.py:53](apps/api/app/api/deps.py#L53) | 1, first sign-in |
| 2 | `runs.runs` | `create_run` | [runs.py:351](apps/api/app/api/v1/runs.py#L351) | **1** — `draft` + `config_snapshot` |
| 3 | `runs.runs.estimate` | `estimate_run` | [runs.py:530](apps/api/app/api/v1/runs.py#L530) | same row |
| 4 | `runs.runs.status` | `approve_run` | [runs.py:596](apps/api/app/api/v1/runs.py#L596) | → `approved` |
| 5 | `runs.runs.status`, `workflow_id` | `start_run` | [runs.py:630](apps/api/app/api/v1/runs.py#L630) | → `queued` |
| 6 | `runs.runs` | `update_run_status` | [activities.py:953](apps/engine/app/activities.py#L953) | → `running`, `started_at` |
| 7 | **`runs.run_reactions`** | `persist_reactions` | [activities.py:689](apps/engine/app/activities.py#L689) | **100** (one per persona×respondent×claim) |
| 8 | **`runs.run_message_results`** | `rollup_message_results` | [activities.py:713](apps/engine/app/activities.py#L713) | **5** (one per claim) |
| 9 | `runs.runs` | `update_run_status` | [activities.py:953](apps/engine/app/activities.py#L953) | → `awaiting_review`, `coverage_pct` |
| 10 | **`runs.run_reports`** | `generate_run_report` | [activities.py:812](apps/engine/app/activities.py#L812) | **1** — only if approved |
| 11 | `runs.runs` | `update_run_status` | [activities.py:953](apps/engine/app/activities.py#L953) | → `finalized`, `finished_at` |

### The metrics, column by column

| Column | Table | Meaning |
|---|---|---|
| `reaction` | `run_reactions` | the raw LLM text |
| `score` | `run_reactions` | `E[scale]` + penalties. **Lower = more compelling** |
| `distribution` | `run_reactions` | the 5-bucket pmf — lets any score be re-derived |
| `penalty` | `run_reactions` | summed adjustment from trigger phrases |
| `respondent` | `run_reactions` | 1..N; part of the unique key |
| `status` | `run_reactions` | `ok` / `failed` — failed rows do not vote |
| `bt_strength` | `run_message_results` | Bradley-Terry strength; all rows sum to 1 |
| `aggregate_score` | `run_message_results` | mean raw score for the claim |
| `rank` | `run_message_results` | 1..N by strength |
| `recommendation` | `run_message_results` | `recommended` / `runner_up` / `drop` |
| `report` | `run_reports` | six-section markdown |
| `baseline_lift_pct` | `run_reports` | winner's strength lift over the weakest |
| `summary` | `run_reports` | jsonb: cohort breakdown + penalty hits |
| `coverage_pct` | `runs.runs` | % of planned reactions that landed |

**There is no summary/metrics column.** Predicted winner, margin, reaction
counts and score statistics are all queries over the two result tables.

---

## 14. Class reference

### API

| Class | File:line | Purpose |
|---|---|---|
| `RunStatus` | [run.py:15](apps/api/app/db/models/run.py#L15) | mirrors the CHECK constraint: draft…expired |
| `Run` | [run.py:34](apps/api/app/db/models/run.py#L34) | ORM model for `runs.runs` |
| `RunCreate` | `schemas/runs.py` | request body: `model_config`, `respondents_per_avatar` |
| `RunResultsOut` | `schemas/run.py` | `/results` response: ranking + report |

### Engine — dataclasses crossing the Temporal boundary

| Class | File:line | Carries |
|---|---|---|
| `Pair` | [activities.py:22](apps/engine/app/activities.py#L22) | avatar_id, message_id, message_text, respondent. **No prompt** — that would be 2.5 KB per pair |
| `AnchorSet` | [activities.py:45](apps/engine/app/activities.py#L45) | ids, texts, scale_points |
| `Penalty` | [activities.py:52](apps/engine/app/activities.py#L52) | trigger, adjustment, reason |
| `StudyContext` | [activities.py:59](apps/engine/app/activities.py#L59) | the SOURCES: kbq, anchors, avatar_ids, claims, penalties, respondents |
| `ScoreResult` | [activities.py:97](apps/engine/app/activities.py#L97) | similarities, pmf, mean_ssr |
| `ReactionResult` | [activities.py:110](apps/engine/app/activities.py#L110) | text, ok, error — per-reaction failure isolation |
| `PenaltyResult` | [activities.py:132](apps/engine/app/activities.py#L132) | final_score, penalty, triggered |
| `ReactionRow` | [activities.py:139](apps/engine/app/activities.py#L139) | exactly what `persist_reactions` writes |
| `UpdateRunStatusInput` | [activities.py:153](apps/engine/app/activities.py#L153) | status + optional timestamps, error, coverage |
| `StudyDataActivities` | [activities.py:522](apps/engine/app/activities.py#L522) | holds the asyncpg pool; owns every `runs.*` write |

### Engine — workflow

| Class | File:line | Purpose |
|---|---|---|
| `StudyRunInput` | [study_run.py:63](apps/engine/app/workflows/study_run.py#L63) | **constant-size**: sources + cursor, never the expanded cross product |
| `StudyRunResult` | [study_run.py:99](apps/engine/app/workflows/study_run.py#L99) | total_pairs_scored, final_status |
| `StudyRunWorkflow` | [study_run.py:105](apps/engine/app/workflows/study_run.py#L105) | the orchestrator |

Signals and queries on `StudyRunWorkflow`:

| Member | Line | Kind |
|---|---|---|
| `finalize` | [121](apps/engine/app/workflows/study_run.py#L121) | signal — Gate 2 approve |
| `approve` | [127](apps/engine/app/workflows/study_run.py#L127) | signal — alias of finalize |
| `reject` | [133](apps/engine/app/workflows/study_run.py#L133) | signal — no API route; kept for compatibility |
| `cancel` | [143](apps/engine/app/workflows/study_run.py#L143) | signal — cooperative stop |
| `progress` | [160](apps/engine/app/workflows/study_run.py#L160) | query |
| `status` | [164](apps/engine/app/workflows/study_run.py#L164) | query — full live state |
| `run` | [179](apps/engine/app/workflows/study_run.py#L179) | the body |
| `_wait_for_review` | [463](apps/engine/app/workflows/study_run.py#L463) | the Gate 2 block |
| `_cancel` | [477](apps/engine/app/workflows/study_run.py#L477) | writes the cancelled status |
| `_fail` | [488](apps/engine/app/workflows/study_run.py#L488) | writes the failed status |

---

## 15. Two things that trip people up

### Why there is no `source .venv/bin/activate`

`uv run` does it. It finds the nearest `pyproject.toml`, resolves that
project's `.venv`, syncs dependencies and runs inside it:

```bash
cd apps/engine && uv run python -m app.worker
# equivalent to: source .venv/bin/activate && python -m app.worker
```

`apps/api` and `apps/engine` have separate virtualenvs, so the **directory you
are in** is what selects the environment.

### Read/write matrix — which file touches which table

| Table | Read by | Written by |
|---|---|---|
| `core.users` | `deps.py` (auth) | `deps.py` (JIT), seed |
| `core.domains` | `_build_config_snapshot` | seed |
| `core.studies` | `_build_config_snapshot`, `fetch_study_context`, `generate_run_report` | seed |
| `core.messages` | `_build_config_snapshot`; joins in rollup/report | seed |
| `core.anchors` | `_build_config_snapshot`; engine **fallback only** | seed |
| `core.avatars` | `fetch_study_context` (names/FK check), `generate_run_report` (cohorts) | seed |
| `core.study_avatars` | `_build_config_snapshot` only | seed |
| **`runs.runs`** | every API route; **`fetch_study_context` ← the snapshot** | API pre-start, `update_run_status` post-start |
| `runs.run_reactions` | `_progress`, `rollup_message_results`, `generate_run_report` | **`persist_reactions`** |
| `runs.run_message_results` | `/results`, `generate_run_report` | **`rollup_message_results`** |
| `runs.run_reports` | `/results` | **`generate_run_report`** |
| `platform.idempotency_keys` | `core/idempotency.py` | `core/idempotency.py` |

> **One line to remember:** `runs.runs.config_snapshot` in;
> `run_reactions` → `run_message_results` → `run_reports` out.
