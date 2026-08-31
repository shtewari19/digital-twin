# Run & Test Runbook

The exact sequence to bring the app up from nothing and prove it works end
to end. No decisions to make — every command is copy-paste. Run from the
repo root (`digital-twin/`) unless a step says otherwise. For background on
*why* each step exists, see [`TESTING.md`](TESTING.md) and
[`apps/engine/DESIGN.md`](apps/engine/DESIGN.md).

## 1. Infra up

```bash
docker compose up -d
docker compose ps
```
All four services (`postgres`, `redis`, `temporal`, `temporal-ui`) must say
`Up`. If `postgres` fails with "port already in use", something else is on
5432 — pin it explicitly:
```bash
APP_POSTGRES_PORT=5433 docker compose up -d postgres
```

Find the **real** port Postgres is listening on (don't assume 5432):
```bash
docker port digital-twin-postgres-1
```
Note that port — call it `<PORT>` below.

## 2. Point both apps at the same DB

Open `apps/api/.env` and `apps/engine/engine/.env`. Both must have the
**identical** Postgres block:

```dotenv
APP_POSTGRES_USER=core_api
APP_POSTGRES_PASSWORD=dev_password
APP_POSTGRES_HOST=localhost
APP_POSTGRES_PORT=<PORT from step 1>
APP_POSTGRES_DB=core_api
```

`apps/engine/engine/.env` also needs a real Azure OpenAI key:

```dotenv
AZURE_OPENAI_API_KEY=<your key>
AZURE_OPENAI_ENDPOINT=<your endpoint>
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o-mini
AZURE_OPENAI_API_VERSION=2024-02-15-preview
EMBEDDING_MODEL_ENDPOINT=https://ai.questkart.cloud/embeddings
```

## 3. Schema + dev user (skip if already done once against this DB)

```bash
cd apps/api
uv sync
uv run python scripts/apply_schema.py
uv run python scripts/seed_dev_data.py
```

## 4. Start both services (two separate terminals, leave both running)

```bash
# terminal A
cd apps/api && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

```bash
# terminal B
cd apps/engine && uv sync && uv run python -m app.worker
```

**Confirm both are actually healthy before continuing:**

```bash
curl http://localhost:8000/health
# must print: {"status":"ok"}
```

Terminal B's log must show:
```
INFO:engine.worker:connected to localhost:7233, polling task queue 'study-runs'
```

If either of these doesn't show, stop here — nothing past this point will work.

## 5. Seed a cheap test study

```bash
cd apps/engine
uv run python scripts/seed_scale_test.py --scale small
```
This prints a `study_id`. Copy it.

## 6. Run it

```bash
STUDY_ID=<paste study_id from step 5>

RUN=$(curl -s -X POST http://localhost:8000/api/v1/studies/$STUDY_ID/runs)
echo $RUN
RUN_ID=$(echo "$RUN" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
echo "RUN_ID=$RUN_ID"

uv run python scripts/seed_scale_test.py --set-run-config $RUN_ID

curl -X POST http://localhost:8000/api/v1/runs/$RUN_ID/start
```

## 7. Watch it finish

```bash
watch -n5 "curl -s http://localhost:8000/api/v1/runs/$RUN_ID | python3 -m json.tool"
```

Wait for `"status": "finalized"` (takes ~45-60s). If you see `"failed"`
instead, run the same `curl` once and read the `error` field — that tells
you exactly what broke (almost always a missing/wrong Azure key at this stage).

## 8. The actual proof — check the data landed

The ranking and report are available through the API now
(`GET /runs/{run_id}/results` — added on top of `RunMessageResult`/`RunReport`,
see `apps/api/app/api/v1/runs.py`):

```bash
curl -s http://localhost:8000/api/v1/runs/$RUN_ID/results | python3 -m json.tool
```

That single call returns `status`, the full `ranking` array (per-message
`rank`/`bt_strength`/`aggregate_score`/`recommendation`), the full Markdown
`report` (all six sections — see `apps/engine/DESIGN.md`'s "Report
generation"), and `baseline_lift_pct`. To save just the report to a real
`.md` file instead of reading escaped JSON:

```bash
curl -s http://localhost:8000/api/v1/runs/$RUN_ID/results \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['report'])" > report.md
```

The one thing this endpoint doesn't cover — raw reaction rows — still needs
the DB directly:

```bash
docker exec digital-twin-postgres-1 psql -U core_api -d core_api -c "
SELECT count(*) FILTER (WHERE status='ok') AS ok_reactions,
       count(*) FILTER (WHERE reaction IS NOT NULL AND reaction != '') AS with_text
FROM runs.run_reactions WHERE run_id='$RUN_ID';
"
```

**Pass criteria — all three must hold:**

| Check | Expected |
|---|---|
| `ok_reactions` = `with_text` | both equal 18 (3 personas × 3 messages × 2 reps) |
| `/results` → `ranking` | 3 entries, `rank` 1–3, `bt_strength` values roughly summing to 1.0, rank 1 = `recommended` |
| `/results` → report | `baseline_lift_pct` is a number (not null), `report` contains all six `##` sections and is well over 1000 characters |

If all three hold, **the whole app works end to end** — API, Temporal, the
engine's reaction generation, scoring, penalties, Bradley-Terry ranking, and
report generation. That's the complete feature set.

## 9. Only if you want the full scale test

```bash
uv run python scripts/seed_scale_test.py --scale full
```
Same steps 6–8 again, but this is 1000 avatars × 15 messages = 15,000 real
Azure calls — can take hours. Don't run this as your "does it work" check;
steps 5–8 above already prove that cheaply.
