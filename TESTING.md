# SSR Engine — Testing & Verification Guide

Covers the message-testing pipeline end to end: `apps/api` (create/start/approve
a run) + `apps/engine` (Temporal worker that generates reactions, scores them,
ranks messages, and writes the report). Follow this top to bottom on a clean
checkout to confirm the app works; the SQL checks in step 5 are the actual
proof — a "Running"/"awaiting_review" status alone doesn't confirm anything.

## Prerequisites

- Docker Desktop (or Docker Engine) running
- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- An Azure OpenAI key with a chat deployment (e.g. `gpt-4o-mini`)

## 1. One-time setup

From the repo root:

```bash
docker compose up -d
docker compose ps   # postgres, redis, temporal, temporal-ui should all be "Up"
```

Check what host port Postgres actually published — it is **not always 5432**:

```bash
docker port digital-twin-postgres-1
# 5432/tcp -> 0.0.0.0:<PORT>   <- use this PORT below
```

Configure both apps' env files (copy from `.env.example` if `.env` doesn't
exist yet) and make sure the Postgres block in **both**
`apps/api/.env` and `apps/engine/engine/.env` matches — the API creates the
run row, the engine worker reads/writes it, they must point at the same DB:

```dotenv
APP_POSTGRES_USER=core_api
APP_POSTGRES_PASSWORD=dev_password
APP_POSTGRES_HOST=localhost
APP_POSTGRES_PORT=<PORT from docker port, above>
APP_POSTGRES_DB=core_api

APP_TEMPORAL_HOST=localhost:7233
APP_TEMPORAL_NAMESPACE=default
APP_TASK_QUEUE=study-runs
```

`apps/engine/engine/.env` additionally needs real Azure OpenAI credentials —
without these the pipeline will fail at the reaction-generation step with a
"Missing credentials" error:

```dotenv
AZURE_OPENAI_API_KEY=<your key>
AZURE_OPENAI_ENDPOINT=<your endpoint, e.g. https://<resource>.services.ai.azure.com/>
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o-mini
AZURE_OPENAI_API_VERSION=2024-02-15-preview
EMBEDDING_MODEL_ENDPOINT=https://ai.questkart.cloud/embeddings
```

Install dependencies and apply the schema:

```bash
cd apps/api && uv sync
uv run python scripts/apply_schema.py      # errors if already applied — fine, skip
uv run python scripts/seed_dev_data.py     # seeds the dev user + 4 domains

cd ../engine && uv sync
```

## 2. Start both services

In two separate terminals (or background them):

```bash
# terminal 1
cd apps/api && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# terminal 2
cd apps/engine && uv run python -m app.worker
```

Confirm both came up clean:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

Worker log should read:
```
INFO:engine.worker:connected to localhost:7233, polling task queue 'study-runs'
```

Temporal UI: **http://localhost:8080**

## 3. Seed test data

```bash
cd apps/engine
uv run python scripts/seed_scale_test.py --scale small
```

This seeds a domain/study/anchors/messages/avatars from
`apps/engine/fixtures/scale_test.yaml` at a cheap size (3 personas × 3
messages × 2 respondents = 18 reactions, real Azure calls but low cost).
Idempotent — re-running it is a no-op and prints the same `study_id`. It
prints the exact next commands, including the `study_id` to use below.

## 4. Run a study end to end

```bash
STUDY_ID=<from step 3>

RUN=$(curl -s -X POST http://localhost:8000/api/v1/studies/$STUDY_ID/runs)
RUN_ID=$(echo "$RUN" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")

# writes this fixture's 10 penalties into the run's config — there's no API
# route for this yet, so it's a direct helper script call
uv run python scripts/seed_scale_test.py --set-run-config $RUN_ID

curl -X POST http://localhost:8000/api/v1/runs/$RUN_ID/start
```

Poll until it stops changing (~30-60s for the small fixture):

```bash
watch -n5 "curl -s http://localhost:8000/api/v1/runs/$RUN_ID | python3 -m json.tool"
```

Expected transition: `queued` → `running` → `finalized` (auto-approved — see
below). Only `failed` means something actually broke — check the `error`
field on the run and the worker log.

**Note on `awaiting_review`:** the workflow briefly passes through this
status after the report is written, but does not pause there — it
auto-approves itself and proceeds straight to `finalized` (see
`StudyRunWorkflow.run` in `apps/engine/app/workflows/study_run.py`). The
`approve`/`reject` signals and the `/runs/{id}/approve` and `/reject` API
routes still exist and work — if you send one in the brief window before
the workflow reaches that point, it wins — but nothing requires you to call
them, and by the time you poll status it will typically already be
`finalized`.

## 5. Verify the actual data (this is the real check)

Status fields alone don't prove correctness — confirm the data.

The ranking and report are available through the API — no direct DB access
needed for these two:

```bash
curl -s http://localhost:8000/api/v1/runs/<run_id>/results | python3 -m json.tool
```

Returns `status`, the full `ranking` array (per message: `rank`,
`bt_strength`, `aggregate_score`, `recommendation`), the full Markdown
`report` (all six sections — Executive Summary, Rankings, Cohort Breakdown +
stakeholder-alignment check, Interpretation, Penalty Impact Summary,
Strategic Recommendation), and `baseline_lift_pct`.

Raw reaction rows still need the DB directly:

```sql
-- every pair should have a real score, and penalty >= 0 (nonzero when a
-- trigger phrase like "reimbursement" or "CAR-T" appears in the reaction text)
SELECT avatar_id, message_id, score, penalty, status
FROM runs.run_reactions WHERE run_id = '<run_id>';

-- reaction text must be real generated prose, not empty/NULL
SELECT reaction FROM runs.run_reactions WHERE run_id = '<run_id>' LIMIT 1;
```

Checklist — all of these should be true:
- [ ] Every seeded avatar/message pair has a `run_reactions` row with `status='ok'`
- [ ] `reaction` text is real, coherent, and reads in the avatar's persona voice
- [ ] `penalty` is nonzero on reactions whose text mentions a trigger phrase (see `fixtures/scale_test.yaml`), zero otherwise
- [ ] `/results` → `ranking` has exactly one entry per message, `rank` 1..N, `bt_strength` values summing to ~1.0
- [ ] Rank 1 has `recommendation = 'recommended'`
- [ ] `/results` → `report` contains all six `##` sections and references the actual winning/losing message text and real persona-family names

## 6. Manual approve / reject (optional — not required)

The run auto-approves itself and reaches `finalized` on its own (see the note
in step 4), so you shouldn't normally need this. `/approve` and `/reject`
still exist for a manual override — mainly useful if you're testing the
signal path itself, or reintroducing a real review gate later:

```bash
curl -X POST http://localhost:8000/api/v1/runs/$RUN_ID/approve
curl -X POST http://localhost:8000/api/v1/runs/$RUN_ID/reject
```

## 7. Full-scale run (optional — real cost/time)

```bash
uv run python scripts/seed_scale_test.py --scale full
```

50 personas × 20 respondents × 15 messages = **1000 avatars, 15,000
reactions** — real Azure OpenAI calls for each one. Watch the worker log for
`batch %s done — %s/%s scored` lines (50 pairs/batch) to gauge real
throughput before committing to the full run; at the default concurrency
(`APP_REACTION_CONCURRENCY=8`) this can take multiple hours. Raise that
setting in `apps/engine/engine/.env` for more parallelism if your Azure
quota allows it.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `pydantic_core.ValidationError: AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT Field required` on worker startup | Not set in `apps/engine/engine/.env` |
| Run fails with `openai.OpenAIError: Missing credentials` | Same as above — worker was started before the key was added; restart it |
| Worker can't reach Postgres / wrong data appears | `APP_POSTGRES_PORT` doesn't match `docker port digital-twin-postgres-1`, or `apps/api/.env` and `apps/engine/engine/.env` point at different databases |
| `GET /api/v1/domains` returns nothing / `run not found` | Schema not applied or dev data not seeded — rerun step 1 |
| Run stays at `running` for a long time, no rows in `run_reactions` | Something's actually stuck — check the worker log for exceptions, and confirm Azure/embedding endpoints are reachable |
| Run never leaves `awaiting_review` | Shouldn't happen — the workflow auto-approves itself immediately after writing that status. If it does, check the worker log for an exception between the `generate_run_report` and final `update_run_status` activity calls |
| `curl` to `localhost:8000` hangs | A stale/zombie uvicorn process is holding the port — `lsof -i :8000` and kill it, then restart |
| `docker compose up -d` fails to bind Postgres's port | Something else is using host port 5432/5433 already, or the container was recreated without the same `APP_POSTGRES_PORT` env var as before — check `docker port digital-twin-postgres-1` and re-align both `.env` files |
