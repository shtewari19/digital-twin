# Temporal Workflow Orchestration — Study Run POC
### End-to-end technical write-up: every file, config, and connection touched

---

## 1. What this ticket set out to prove

Before any real pipeline logic gets built, we needed to prove one loop works reliably:

```
Frontend → API → Temporal Server → Worker → Workflow completes → API records it
```

Nothing in this ticket does real work — the workflow is a deliberate no-op. The goal was purely to wire the plumbing so every later ticket (real pipeline steps, human approval gates, retries) has a proven foundation to build on.

---

## 2. Infrastructure layer — `docker-compose.yml`

**What changed:** two new services added at the repo root, alongside the existing `postgres` and `redis`.

```yaml
temporal:
  image: temporalio/auto-setup:1.24.2
  depends_on:
    postgres:
      condition: service_healthy
  environment:
    - DB=postgres12
    - DB_PORT=5432
    - POSTGRES_USER=${APP_POSTGRES_USER:-core_api}
    - POSTGRES_PWD=${APP_POSTGRES_PASSWORD:-dev_password}
    - POSTGRES_SEEDS=postgres
  ports:
    - "7233:7233"

temporal-ui:
  image: temporalio/ui:2.31.2
  depends_on:
    - temporal
  environment:
    - TEMPORAL_ADDRESS=temporal:7233
  ports:
    - "8080:8080"
```

**What each piece does:**

| Setting | Purpose |
|---|---|
| `temporalio/auto-setup` image | A Temporal server build that runs its own schema migrations on startup — it creates `temporal` and `temporal_visibility` databases on whatever Postgres you point it at. No manual `CREATE DATABASE` step needed. |
| `POSTGRES_SEEDS=postgres` | Tells Temporal's server which Postgres host to connect to — reuses your **existing** `postgres` container/service by its Docker Compose service name (Docker's internal DNS resolves `postgres` to that container's IP). |
| Port `7233` | The **gRPC frontend** — this is the single port every Temporal client (your API, your worker) actually talks to. Everything — starting workflows, workers polling for tasks, querying history — goes over this one port. |
| Port `8080` (`temporal-ui`) | A separate container serving the web UI, which itself connects to `temporal:7233` internally to render what's happening. Purely observational — nothing in your app depends on it. |
| `depends_on: postgres (service_healthy)` | Compose won't start the `temporal` container until Postgres reports healthy — avoids a race where Temporal tries to migrate a schema before Postgres is accepting connections. |

**Why this design, not a separate Temporal-only Postgres:** fewer moving parts locally — one Postgres container serves both your application schema (`core`, `runs`, `platform`) and Temporal's own internal schema (`temporal`, `temporal_visibility`), in the same instance, as separate databases. In production, these would likely split onto separate managed instances, but for local dev this keeps `docker compose up` simple.


### 2.1 Environment configuration — local vs production

The Temporal connection values should not be hardcoded in the API or worker. The current `.env` uses the existing `APP_` prefix:

```env
APP_TEMPORAL_HOST=localhost:7233
APP_TEMPORAL_NAMESPACE=default
APP_TASK_QUEUE=study-runs
```

The API reads these through its `Settings` class. The engine is a separate Python application, so it should not import `apps/api/app/core/config.py`. Instead, the worker loads environment variables with `python-dotenv`:

```python
from dotenv import load_dotenv

load_dotenv()
```

and then:

```python
temporal_host = os.getenv("APP_TEMPORAL_HOST")
temporal_namespace = os.getenv("APP_TEMPORAL_NAMESPACE")
task_queue = os.getenv("APP_TASK_QUEUE")
```

`load_dotenv()` is intentionally simple: when a local `.env` is available it loads it; in a server/container deployment, normal environment variables can be supplied by Docker, the shell, or the deployment platform instead.

#### Local development

With the Temporal services running through Docker Compose:

```bash
cd ~/chorus/digital-twin

docker compose up -d postgres redis temporal temporal-ui
```

Check the services:

```bash
docker compose ps
```

Check Temporal startup logs:

```bash
docker compose logs -f temporal
```

Check the Temporal UI:

```text
http://localhost:8080
```

The API and worker are running outside Docker in the current development setup, so both use:

```env
APP_TEMPORAL_HOST=localhost:7233
```

Start the worker:

```bash
cd ~/chorus/digital-twin/apps/engine
source .venv/bin/activate
python -m app.worker
```

Start the API separately:

```bash
cd ~/chorus/digital-twin/apps/api
source .venv/bin/activate
uvicorn app.main:app --reload
```

The local connection path is:

```text
API (localhost) ──────┐
                      ├──► localhost:7233 ──► Temporal
Worker (localhost) ───┘
                                      │
                                      └──► temporal-ui :8080
```

#### Production deployment

The important rule is that `APP_TEMPORAL_HOST` depends on where the worker/API and Temporal server are running.

If API, engine, and Temporal are all containers on the same Docker/Compose network:

```env
APP_TEMPORAL_HOST=temporal:7233
APP_TEMPORAL_NAMESPACE=default
APP_TASK_QUEUE=study-runs
```

`temporal` is the Docker Compose service name, so containers can resolve it through Docker's internal DNS.

If Temporal is installed on a separate server, use the private/internal DNS name or hostname reachable from the API and worker, for example:

```env
APP_TEMPORAL_HOST=temporal.internal.example.com:7233
```

Do not keep:

```env
APP_TEMPORAL_HOST=localhost:7233
```

when the API/worker are running on a different machine from Temporal.

For a production server using this Compose setup, the basic startup command is:

```bash
docker compose up -d postgres redis temporal temporal-ui
```

Then verify:

```bash
docker compose ps
docker compose logs --tail=100 temporal
```

The production API and worker should be configured with the production environment variables and started as their own services/processes.

#### Production security and persistence

The local `temporalio/auto-setup` Compose setup is intended to keep development simple. For production, do not expose Temporal's gRPC port `7233` directly to the public internet. Keep it reachable only by trusted API/worker networks, and put appropriate authentication/TLS/network controls around production Temporal access.

Also use persistent production storage and proper database backups. For a serious production deployment, use Temporal Cloud or follow Temporal's production self-hosting architecture rather than treating the local development Compose file as a highly available production deployment.

#### Environment summary

| Environment | `APP_TEMPORAL_HOST` | Who connects |
|---|---|---|
| Local API/worker outside Docker | `localhost:7233` | WSL/local processes |
| API/worker inside same Docker network | `temporal:7233` | Docker containers |
| Temporal on separate server | `<internal-host>:7233` | API/worker over private network |
| Temporal Cloud | Provider endpoint | API/worker using provider configuration |

The application code does not need to change between these environments; only the environment-specific configuration changes.

---

## 3. `apps/engine` — the workflow definition and the worker process

This is a **new subsystem** — previously `apps/engine` had no real code.

### 3.1 `apps/engine/app/workflows/study_run.py` — the workflow

```python
from temporalio import workflow

@workflow.defn(name="study_run_workflow")
class StudyRunWorkflow:
    @workflow.run
    async def run(self, study_id: str) -> None:
        workflow.logger.info("study_run_workflow started study_id=%s", study_id)
        workflow.logger.info("study_run_workflow completed study_id=%s", study_id)
        return
```

**Line-by-line:**
- `@workflow.defn(name="study_run_workflow")` — registers this class as a Temporal workflow **type**, under the string name `"study_run_workflow"`. This name is the contract between the API (which starts workflows by name) and the worker (which registers the class to handle that name). They never share Python imports — the string is the only coupling.
- `@workflow.run` — marks the `run` method as the workflow's entry point. A workflow class can have exactly one of these.
- `async def run(self, study_id: str) -> None:` — takes one argument (`study_id`, passed as a plain string — Temporal serializes arguments to JSON under the hood, so keep them simple types or JSON-serializable objects), does nothing, returns `None`.
- `workflow.logger` — a logger that's aware it's running **inside** a workflow's deterministic execution context. Regular Python logging (`import logging; logging.info(...)`) also works but doesn't get the workflow-context metadata (`run_id`, `workflow_id`, `attempt`) automatically attached — that's why your log lines show that dictionary of context after the message.

**Why it's a no-op on purpose:** Temporal workflow code must be **deterministic** — no direct I/O, no `random()`, no `datetime.now()` inside the workflow body. All real work (DB calls, HTTP calls, ML inference) has to go through **activities**, which are ordinary non-deterministic Python functions the workflow calls out to. This ticket doesn't add any activities yet — that's the next ticket, once the pipeline steps (reaction → embed → cosine → shift → normalize → penalty → rank → report) get built one at a time.

### 3.2 `apps/engine/app/worker.py` — the worker process

```python
import asyncio
import logging
from temporalio.client import Client
from temporalio.worker import Worker
from app.workflows.study_run import StudyRunWorkflow

TEMPORAL_HOST = "localhost:7233"
TASK_QUEUE = "study-runs"

async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect(TEMPORAL_HOST, namespace="default")
    worker = Worker(client, task_queue=TASK_QUEUE, workflows=[StudyRunWorkflow])
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
```

**What this process actually does, step by step:**
1. `Client.connect("localhost:7233", namespace="default")` — opens a persistent gRPC connection to the Temporal server. The `namespace` is Temporal's tenant-isolation concept (like a schema) — `default` is the out-of-the-box namespace every local Temporal server has.
2. `Worker(client, task_queue="study-runs", workflows=[StudyRunWorkflow])` — builds a worker bound to one **task queue** (`"study-runs"` — an arbitrary name that's the routing key between "who submits work" and "who's willing to do it") and registers `StudyRunWorkflow` as the only workflow type this worker knows how to execute. If the server hands it a different workflow type, it can't run it.
3. `await worker.run()` — blocks forever, long-polling the server: "give me tasks on `study-runs`." This is why it's a separate long-running process (`python -m app.worker`) rather than something the API calls inline — it needs to sit there and listen continuously, independent of any single HTTP request's lifecycle.

**Why `localhost:7233` is hardcoded here (a known simplification):** for this walking-skeleton ticket it's fine since everything runs on one dev machine. Before this goes to a shared environment, this should move to an env var read the same way `apps/api`'s config does, since `localhost` won't resolve to the right host outside local dev.

---

## 4. `apps/api` — the HTTP surface, the DB model, and the Temporal client

### 4.1 `apps/api/app/core/temporal.py` — the shared Temporal client

```python
from temporalio.client import Client

_client: Client | None = None
STUDY_RUN_WORKFLOW_NAME = "study_run_workflow"

async def init_temporal_client() -> Client:
    global _client
    _client = await Client.connect(settings.TEMPORAL_HOST, namespace=settings.TEMPORAL_NAMESPACE)
    return _client

def get_temporal_client() -> Client:
    if _client is None:
        raise RuntimeError("Temporal client not initialized")
    return _client
```

**Why a module-level singleton, not a per-request connection:** `Client.connect` opens a gRPC channel — doing that on every incoming HTTP request would add real latency and unnecessary connection churn. Instead, `init_temporal_client()` is called **once**, at API process startup (wired into `main.py`'s startup hook — see 4.5), and every request afterward just reuses the already-open connection via `get_temporal_client()`. This mirrors exactly how your DB connection pool works — one long-lived pool, not one connection per request.

**Why the workflow is started by string name (`STUDY_RUN_WORKFLOW_NAME`), not by importing the `StudyRunWorkflow` class:** `apps/api` and `apps/engine` are separate Python packages with separate dependency trees — the API has no reason to import the engine's code just to reference a class. Temporal's client API supports starting a workflow purely by its registered name string plus the task queue it should land on, which keeps the two services fully decoupled — the API only needs to know the *contract* (name + queue), not the implementation.

### 4.2 `apps/api/app/db/models/run.py` — the ORM model

```python
class RunStatus(str, enum.Enum):
    DRAFT = "draft"
    CONFIGURED = "configured"
    ESTIMATED = "estimated"
    APPROVED = "approved"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    FINALIZED = "finalized"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

class Run(Base):
    __tablename__ = "runs"
    __table_args__ = {"schema": "runs"}   # -> runs.runs, not public.runs

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    study_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.studies.id"), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, values_callable=lambda e: [m.value for m in e]),
        default=RunStatus.DRAFT, nullable=False,
    )
    workflow_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # + config_snapshot, model_config, estimate, actuals, error (all jsonb, unused by this ticket)
    # + started_at, finished_at, expires_at (unused by this ticket)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
```

**Why this maps to a table your team already created, not one this ticket invented:** the real DDL (in `setup.sql`) is:

```sql
CREATE TABLE runs.runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    study_id uuid NOT NULL REFERENCES core.studies(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN
      ('draft','configured','estimated','approved','queued','running',
       'awaiting_review','finalized','failed','cancelled','expired')),
    workflow_id text,
    ...
);
```

Three details had to match this **exactly**, or every insert/update would throw a Postgres error at runtime:
- **Schema qualification** — the table lives in the `runs` Postgres schema (`runs.runs`), not the default `public` schema. `__table_args__ = {"schema": "runs"}` tells SQLAlchemy to emit `runs.runs` in every generated query.
- **Lowercase status values** — the table's `CHECK` constraint only accepts lowercase strings (`'draft'`, `'running'`, etc). The Python `Enum` members are uppercase (`RunStatus.DRAFT`) for normal Python style, but `values_callable=lambda e: [m.value for m in e]` tells SQLAlchemy to send the lowercase `.value` (`"draft"`) to Postgres, not the uppercase member name.
- **`workflow_id`, not `temporal_workflow_id`** — matching the actual column name already in the DDL.

**Why 11 status values exist when this ticket only uses 5:** `runs.runs` is the single table that will track a run through its **entire** lifecycle across many future tickets — cost estimation (`estimated`), human sign-off (`approved`, `awaiting_review`), cancellation (`cancelled`), and expiry (`expired`). This ticket's flow only exercises `draft → queued → running → finalized`/`failed`, but the model declares the full enum so it never silently accepts an invalid value and so future tickets don't need to touch this file again.

### 4.3 `apps/api/app/schemas/run.py` — the API response shape

```python
class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    study_id: uuid.UUID
    status: RunStatus
    workflow_id: str | None = None
```

This is the Pydantic model FastAPI uses to serialize a `Run` ORM object into the JSON your `curl` calls got back. `from_attributes=True` (formerly `orm_mode`) tells Pydantic it's allowed to read this shape directly off a SQLAlchemy object's attributes (`run.id`, `run.status`, ...) rather than requiring a plain dict.

### 4.4 `apps/api/app/api/v1/runs.py` — the two endpoints

**`POST /studies/{study_id}/runs`**
```python
run = Run(study_id=study_id, status=RunStatus.DRAFT)
db.add(run)
await db.commit()
await db.refresh(run)
return run
```
Straightforward insert: builds a `Run` row with `status=draft`, `INSERT`s it, commits the transaction, then `refresh`es so the in-memory object picks up server-generated fields (`id` from `gen_random_uuid()`, `created_at` from `now()`) before it's serialized back to the caller. This is the `201 Created` you saw in the logs.

**`POST /runs/{run_id}/start`** — the more involved one:
```python
run = await db.get(Run, run_id)
if run.status != RunStatus.DRAFT:
    raise HTTPException(409, ...)

run.status = RunStatus.QUEUED
await db.commit()

client = get_temporal_client()
workflow_id = f"study-run-{run_id}"
await client.start_workflow(
    STUDY_RUN_WORKFLOW_NAME, str(run.study_id),
    id=workflow_id, task_queue=settings.TEMPORAL_TASK_QUEUE_STUDY_RUNS,
)

run.status = RunStatus.RUNNING
run.workflow_id = workflow_id
await db.commit()

asyncio.create_task(_finalize_when_done(run_id, workflow_id))
return run
```

Walking through exactly what happens, matching your screenshots:
1. **Load + guard** — fetches the row, rejects (`409 Conflict`) if it's not `draft`. This stops the same run being started twice.
2. **`draft → queued`, committed** — written to Postgres *before* Temporal is even contacted. This is deliberate: if the process crashed here, the row would sit at `queued` rather than silently vanishing mid-transition.
3. **`client.start_workflow(...)`** — this is the actual handoff to Temporal. `STUDY_RUN_WORKFLOW_NAME` (`"study_run_workflow"`) tells the server which workflow type to run; `str(run.study_id)` is the single argument passed into `StudyRunWorkflow.run(self, study_id)`; `id=workflow_id` sets Temporal's own unique identifier for this execution (`study-run-<run uuid>` — human-readable and traceable back to your row, which is exactly the string you saw in the Temporal UI's title); `task_queue=...` tells the server which queue to place the task on — this **must** match the `TASK_QUEUE` your worker registered against (`"study-runs"`), or the workflow would sit queued forever with no worker listening for it.
4. **`queued → running`, `workflow_id` stored, committed** — the row now carries a durable pointer (`run.workflow_id`) back to the exact Temporal execution, so it can be looked up later (in the UI, in logs, or programmatically via `get_workflow_handle`).
5. **`asyncio.create_task(_finalize_when_done(...))`** — fires off a background coroutine *without waiting for it*, so the HTTP response (the `200 OK` in your logs) returns immediately once the row is `running`, rather than blocking the caller until the workflow actually finishes.

**`_finalize_when_done` — the completion watcher:**
```python
async def _finalize_when_done(run_id, workflow_id):
    handle = client.get_workflow_handle(workflow_id)
    async with AsyncSessionLocal() as db:
        run = await db.get(Run, run_id)
        try:
            await handle.result()          # blocks until Temporal reports completion
            run.status = RunStatus.FINALIZED
        except Exception:
            run.status = RunStatus.FAILED
        await db.commit()
```
`handle.result()` is a **long-poll against the Temporal server** — it suspends this coroutine (cheaply, no busy-waiting) until the server tells it the workflow finished, then either returns the workflow's return value (`None`, in our no-op case) or raises if the workflow itself raised. This is exactly the `running → finalized` transition you watched happen in Postgres within about 60ms, matching the Temporal UI's `History` timing.

**Important nuance — a fresh DB session, not the request's session:** notice `_finalize_when_done` opens its own `AsyncSessionLocal()` rather than reusing the `db` session that was injected into the endpoint. That's required: by the time this coroutine runs, the original HTTP request (and its session) has already returned to the caller and been closed. Reusing a closed session would throw.

### 4.5 `apps/api/app/main.py` — startup wiring

```python
await init_temporal_client()
```
Added to whatever startup hook already existed. This is what makes `get_temporal_client()` non-`None` by the time the first request arrives — without this line, the very first `/start` call would hit the `RuntimeError` guard in `core/temporal.py`.

### 4.6 `apps/api/app/core/config.py` — new settings

```python
TEMPORAL_HOST: str = "localhost:7233"
TEMPORAL_NAMESPACE: str = "default"
TEMPORAL_TASK_QUEUE_STUDY_RUNS: str = "study-runs"
```
Added to the existing `Settings` class, following whatever env-loading convention was already there, plus mirrored into `.env.example` so the values are discoverable without reading code.

### 4.7 `apps/api/app/api/v1/router.py` — route registration

```python
from app.api.v1 import domains, runs
router.include_router(runs.router, tags=["runs"])
```
Without this line, FastAPI would never see the two new endpoints — `runs.py` defining an `APIRouter` isn't enough on its own; it has to be mounted onto the app's main router tree.

---

## 5. Database connections — what's talking to Postgres, and how

There are **two independent connections into the same Postgres instance**, which is worth being explicit about since it's easy to assume there's one shared "the database" when there are actually two separate schemas being written by two separate processes:

| Connection | Who owns it | What it writes | Schema |
|---|---|---|---|
| Your app's async SQLAlchemy engine (`apps/api/app/db/session.py`) | The FastAPI process | `runs.runs` rows — draft/queued/running/finalized | `runs`, `core`, `platform` (your application schemas) |
| Temporal's internal Postgres schema | The `temporal` Docker container (via `auto-setup`) | Workflow event history, task queues, visibility records | `temporal`, `temporal_visibility` (Temporal's own, opaque to your app) |

Your application code **never queries Temporal's internal tables directly** — everything about workflow state is retrieved through the Temporal client/gRPC API (`get_workflow_handle`, `handle.result()`, the UI), never raw SQL against `temporal.*`. That boundary is intentional and shouldn't be crossed even for debugging convenience — Temporal owns that schema's shape and can change it between versions.

---

## 6. Full request trace — what actually happened in your test run

Matching your three screenshots to the code, in order:

1. `POST /api/v1/studies/59c3d3ee.../runs` → `create_run()` in `runs.py` → `INSERT INTO runs.runs (study_id, status) VALUES (..., 'draft')` → `201 Created`, id `a3a52585-...`
2. `POST /api/v1/runs/a3a52585.../start` → `start_run()`:
   - `UPDATE runs.runs SET status='queued' WHERE id='a3a52585-...'`
   - `client.start_workflow("study_run_workflow", "59c3d3ee-...", id="study-run-a3a52585-...", task_queue="study-runs")` → Temporal server records a new workflow execution, places a task on the `study-runs` queue
   - Your worker process (already polling that queue) picks it up within milliseconds → executes `StudyRunWorkflow.run("59c3d3ee-...")` → logs `started` → logs `completed` → returns `None`
   - `UPDATE runs.runs SET status='running', workflow_id='study-run-a3a52585-...'`
   - `200 OK` returned to curl immediately
   - In the background: `_finalize_when_done` is polling `handle.result()`, gets the `None` result back the instant the workflow returns, `UPDATE runs.runs SET status='finalized'`
3. Temporal UI, queried independently of your app: shows the same `workflow_id` (`study-run-a3a52585-...`), `Completed`, `Result: null`, `60ms` total duration, `3` state transitions (Started → Task completed → Workflow completed) — an independent confirmation of exactly what your DB row and worker logs already showed.

---

## 7. Complete list of files touched

| File | Status | Purpose |
|---|---|---|
| `docker-compose.yml` | Edited | Added `temporal` + `temporal-ui` services |
| `apps/engine/pyproject.toml` | Edited | Added `temporalio` dependency |
| `apps/engine/app/workflows/__init__.py` | New | Package marker |
| `apps/engine/app/workflows/study_run.py` | New | The no-op workflow definition |
| `apps/engine/app/worker.py` | New | The worker entrypoint process |
| `apps/api/pyproject.toml` | Edited | Added `temporalio` dependency |
| `apps/api/app/core/config.py` | Edited | Added Temporal host/namespace/queue settings |
| `apps/api/app/core/temporal.py` | New | Shared Temporal client singleton |
| `apps/api/app/db/models/run.py` | New | `Run` ORM model mapped to `runs.runs` |
| `apps/api/app/db/models/__init__.py` | Edited | Export the new `Run` model |
| `apps/api/app/schemas/run.py` | New | `RunOut` response schema |
| `apps/api/app/api/v1/runs.py` | New | The two endpoints + the background finalizer |
| `apps/api/app/api/v1/router.py` | Edited | Mounted the new `runs` router |
| `apps/api/app/main.py` | Edited | Calls `init_temporal_client()` on startup |
| `.env.example` | Edited | Documented the three new `TEMPORAL_*` vars |
| `apps/api/scripts/setup.sql` | Reviewed, not modified | Confirmed `runs.runs` already existed with the right shape — no schema change needed |

---

## 8. Known simplifications (call these out explicitly in review)

1. **`_finalize_when_done` lives in the API process.** If the API restarts while a run is `running`, that watcher task dies and the row is stuck at `running` — Temporal itself still completes the workflow correctly, but nothing tells Postgres. Durable fix for later: have the *workflow* call an activity that writes `finalized` directly, so completion-recording survives an API restart too.
2. **`tenant_id` is never set** on run creation, even though the column exists. Needs to be threaded through from whatever auth/tenant context exists elsewhere in the API once that's wired up.
3. **The engine now reads `APP_TEMPORAL_HOST`, `APP_TEMPORAL_NAMESPACE`, and `APP_TASK_QUEUE` from the environment** rather than hardcoding `localhost:7233`; this keeps local and deployed environments configurable without changing worker code.
4. **No row locking** on the `start_run` read-then-write — a low-probability race if two `/start` calls hit the same run simultaneously.

None of these block this ticket — the acceptance criteria didn't require them — but they're the honest list of what's still simplified.


---

## 9. Current recommended configuration

For this repository, keep the environment-specific values outside the Python source code.

### Local `.env`

```env
APP_POSTGRES_USER=chorus_admin
APP_POSTGRES_PASSWORD=<local-secret>
APP_POSTGRES_HOST=<postgres-host>
APP_POSTGRES_PORT=5432
APP_POSTGRES_DB=chorus

APP_TEMPORAL_HOST=localhost:7233
APP_TEMPORAL_NAMESPACE=default
APP_TASK_QUEUE=study-runs

APP_DEV_USER_ID=00000000-0000-0000-0000-000000000001
```

### Production environment

If API, engine, and Temporal share the same Docker network:

```env
APP_POSTGRES_USER=<production-user>
APP_POSTGRES_PASSWORD=<production-secret>
APP_POSTGRES_HOST=postgres
APP_POSTGRES_PORT=5432
APP_POSTGRES_DB=chorus

APP_TEMPORAL_HOST=temporal:7233
APP_TEMPORAL_NAMESPACE=default
APP_TASK_QUEUE=study-runs

APP_DEV_USER_ID=<production-value-or-remove-when-real-auth-is-enabled>
```

Do not commit a real `.env` or production secrets to Git. Commit `.env.example` with placeholder values only.

### Useful local commands

```bash
# Start infrastructure
docker compose up -d postgres redis temporal temporal-ui

# Check status
docker compose ps

# Follow Temporal logs
docker compose logs -f temporal

# Stop infrastructure
docker compose down

# Start the worker
cd apps/engine
source .venv/bin/activate
python -m app.worker

# Start the API
cd apps/api
source .venv/bin/activate
uvicorn app.main:app --reload
```

The core flow remains:

```text
Frontend
   │
   ▼
FastAPI
   │
   │ start_workflow(...)
   ▼
Temporal Server :7233
   │
   │ task queue = study-runs
   ▼
Temporal Worker
   │
   ▼
StudyRunWorkflow
```


---

## 10. Test the no-op Temporal workflow

Once the following are running:

1. PostgreSQL
2. Temporal Server
3. Temporal UI
4. API
5. Temporal Worker

you can drive the complete no-op workflow through the API.

### Step 1 — Create a run

Use an existing study ID:

```bash
curl -X POST \
  http://localhost:8000/api/v1/studies/<existing-study-id>/runs
```

Expected response:

```json
{
  "id": "<run-id>",
  "study_id": "<existing-study-id>",
  "status": "DRAFT"
}
```

Copy the returned `run-id`.

### Step 2 — Start the run

Use the `run-id` returned from Step 1:

```bash
curl -X POST \
  http://localhost:8000/api/v1/runs/<run-id>/start
```

Expected response:

```json
{
  "id": "<run-id>",
  "study_id": "<existing-study-id>",
  "status": "RUNNING"
}
```

### Step 3 — Verify the workflow

The API sends the workflow to Temporal using:

```text
workflow type = study_run_workflow
task queue    = study-runs
```

The worker polling `study-runs` picks up the workflow and executes the no-op `StudyRunWorkflow`.

Open the Temporal UI:

```text
http://localhost:8080
```

You should see the workflow execution with a workflow ID similar to:

```text
study-run-<run-id>
```

The workflow should move to:

```text
Completed
```

The API's background completion watcher then updates the run from:

```text
RUNNING → FINALIZED
```

### Complete local test

```bash
# 1. Create run
curl -X POST \
  http://localhost:8000/api/v1/studies/<existing-study-id>/runs

# Copy the returned run ID.

# 2. Start run
curl -X POST \
  http://localhost:8000/api/v1/runs/<run-id>/start

# 3. Open Temporal UI
# http://localhost:8080
```

This validates the complete POC path:

```text
curl
  ↓
FastAPI
  ↓
runs.runs
  ↓
Temporal Server
  ↓
study-runs task queue
  ↓
Temporal Worker
  ↓
StudyRunWorkflow
  ↓
Completed
  ↓
API updates run to FINALIZED
```
