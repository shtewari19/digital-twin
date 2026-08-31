# Engine — Pytest Suite Documentation

> **Location:** `apps/engine/tests/`
> **Suite size:** 10 tests across 2 test files · **Line coverage:** 92% (100% of executable workflow logic) · **Runtime:** < 1 s · **External services needed:** none

This document describes every application module covered by the pytest suite under
`apps/engine/tests/`, what each test verifies, how the hermetic test infrastructure works,
and how to run the suite manually.

---

## Table of Contents

1. [Overview & Testing Philosophy](#1-overview--testing-philosophy)
2. [Directory Layout](#2-directory-layout)
3. [Coverage Detail — What Is Tested Where](#3-coverage-detail--what-is-tested-where)
4. [Test Infrastructure](#4-test-infrastructure)
5. [Mocking Techniques & What Is Mocked Where](#5-mocking-techniques--what-is-mocked-where)
6. [How to Run the Tests Manually](#6-how-to-run-the-tests-manually)
7. [CI Integration](#7-ci-integration)
8. [Known Limitations](#8-known-limitations)

---

## 1. Overview & Testing Philosophy

The suite tests the Temporal worker application (`apps/engine/app/`) at unit level. There
are no HTTP endpoints here — the engine is a long-running worker process, so there is no
`TestClient`-style contract layer. The two live modules are `app/worker.py` (the Temporal
worker entrypoint) and `app/workflows/study_run.py` (the `StudyRunWorkflow` walking
skeleton).

Two deliberate design decisions make the suite **hermetic** (no Temporal server required):

1. **Environment bootstrapping before imports** — `app/worker` runs `load_dotenv` and
   reads `APP_TEMPORAL_HOST` / `APP_TEMPORAL_NAMESPACE` / `APP_TASK_QUEUE` at import and
   call time, so `conftest.py` sets all `APP_*` environment variables *before* any
   `app.*` module import.
2. **In-process Temporal test server** — workflow tests run against
   `temporalio.testing.WorkflowEnvironment.start_time_skipping()`, the SDK's bundled
   single-binary test server. It needs no network access and fast-forwards time, so the
   skeleton's real 30-second `asyncio.sleep(30)` completes in milliseconds.

---

## 2. Directory Layout

```
apps/engine/
├── pyproject.toml              # pytest + ruff config, dev dependencies
└── tests/
    ├── docs/
    │   └── TESTING.md          # ← this document
    ├── conftest.py             # env bootstrap + shared state isolation
    ├── fakes.py                # in-memory stand-ins for Temporal client / worker
    ├── test_worker.py          #      (6 tests)
    └── test_study_run.py       #      (4 tests)
```

**Coverage summary**

| Source module | Test file | Tests |
|---|---:|---:|
| `app/worker.py` | `test_worker.py` | 6 |
| `app/workflows/study_run.py` | `test_study_run.py` | 4 |
| **Total** | | **10** |

Not covered by design: the module-level `.env` discovery loop and the
`if __name__ == "__main__"` guard in `app/worker.py` — see §8.

---

## 3. Coverage Detail — What Is Tested Where

### 3.1 `app/worker.py` — tested by `test_worker.py`

The module's `main()` has three responsibilities: validate required environment variables,
connect to Temporal, and build/run a `Worker` registering `StudyRunWorkflow`. Async tests
run automatically (`asyncio_mode = "auto"`).

| Test | Verifies |
|---|---|
| `test_main_raises_when_host_missing` | Missing `APP_TEMPORAL_HOST` → `RuntimeError` before any connection attempt. |
| `test_main_raises_when_namespace_missing` | Missing `APP_TEMPORAL_NAMESPACE` → `RuntimeError`. |
| `test_main_raises_when_task_queue_missing` | Missing `APP_TASK_QUEUE` → `RuntimeError`. |
| `test_main_connects_to_temporal` | `Client.connect(host, namespace=...)` is called with the exact configured values, and the `Worker` is built for the configured task queue. |
| `test_main_registers_study_run_workflow` | The `Worker` receives `StudyRunWorkflow` in its `workflows=[...]` — guards the wiring contract between entrypoint and workflow. |
| `test_main_calls_worker_run` | `await worker.run()` is reached once everything is constructed. |

### 3.2 `app/workflows/study_run.py` — tested by `test_study_run.py`

Workflow tests run against `WorkflowEnvironment.start_time_skipping()`, so they exercise
the real decorated class end-to-end (registration, deserialization of `study_id`, the
logged start/completion) without a server or a real 30-second wait.

| Test | Verifies |
|---|---|
| `test_workflow_name_matches_api_contract` | The `@workflow.defn(name=...)` value is `"study_run_workflow"` — the exact string the API's `app/core/temporal.py` uses as `STUDY_RUN_WORKFLOW_NAME` when starting executions. |
| `test_workflow_completes_successfully` | Executing the workflow with a `study_id` returns `None` (success) and doesn't raise. |
| `test_workflow_accepts_any_study_id` | A non-trivial study id flows through execution without error. |
| `test_workflow_skip_time_from_thirty_second_sleep` | The 30-second `asyncio.sleep(30)` in the skeleton is fast-forwarded by the time-skipping environment, so the workflow finishes immediately — pins the placeholder behavior. |

---

## 4. Test Infrastructure

### 4.1 `tests/conftest.py`

| Fixture | Scope | Purpose |
|---|---|---|
| *(module top-level code)* | — | Sets every required `APP_*` env var via `os.environ.setdefault` **before** any `app.*` import — importing `app.worker` runs `load_dotenv` + reads env vars at call time. |
| `_isolate_test_state` (autouse) | function | Clears `FakeTemporalClient.connect_calls` before and after every test, so the shared class-level recorder never leaks between tests. |

### 4.2 `tests/fakes.py`

| Fake | Replaces | Notes |
|---|---|---|
| `FakeTemporalClient` | `temporalio.Client` | The real `Client.connect` is a classmethod reached as `Client.connect(...)`, so this fake records every `(host, namespace)` pair at the **class level** (`connect_calls`) and returns a fresh bare stub per call. |
| `FakeWorker` | `temporalio.worker.Worker` | Records construction args (`task_queue`, `workflows`); `run()` is a no-op that flips `_ran`, so tests can assert the worker was actually run. |

---

## 5. Mocking Techniques & What Is Mocked Where

The suite **does not use `unittest.mock`**, `MagicMock`, `patch`, or any third-party
mocking library. All test doubles are either pytest's built-in `monkeypatch` fixture or
hand-written fake classes in `fakes.py`.

| Technique | Mechanism | Purpose |
|---|---|---|
| **`monkeypatch.setattr`** | Replaces a module-level attribute (function, class, or variable) with a test double for the duration of the test. | Swap `worker.Client` for `FakeTemporalClient` and `worker.Worker` for a capturing factory, so no real Temporal connection is attempted. |
| **`monkeypatch.setenv` / `delenv`** | Sets or removes environment variables for the test scope. | Drive the env-var validation branches of `main()` (each missing-key test clears exactly one variable). |
| **`temporalio.testing.WorkflowEnvironment`** | In-process Temporal test server (time-skipping). | Execute `StudyRunWorkflow` for real — including its 30s sleep — with no external server. |
| **Hand-written fake classes** (`fakes.py`) | Lightweight in-memory stand-ins that record calls and replay canned data. | Replace the Temporal client and worker construction inside `main()`. |
| **Local `_FakeWorkerFactory`** | A test-local class standing in for `worker.Worker`. | Capture the `workflows=[...]` argument and the constructed worker instances for assertion. |

What is mocked in each test file:

| Test file | What is mocked | How | Why |
|---|---|---|---|
| `test_study_run.py` | *(nothing)* | The real `WorkflowEnvironment` + `Worker` classes are used. | The in-process test server *is* the hermetic seam; nothing else needs stubbing. |
| `test_worker.py` | `app.worker.Client` | `monkeypatch.setattr(worker, "Client", FakeTemporalClient)` | Record `Client.connect(host, namespace)` calls without touching a real Temporal server. |
| | `app.worker.Worker` | `monkeypatch.setattr` → test-local `_FakeWorkerFactory` or `FakeWorker` | Capture construction args and assert `run()` was awaited. |
| | `APP_TEMPORAL_HOST` / `APP_TEMPORAL_NAMESPACE` / `APP_TASK_QUEUE` | `monkeypatch.setenv` / `delenv` | Test each missing-variable error branch in isolation. |

---

## 6. How to Run the Tests Manually

### Prerequisites

- [uv](https://docs.astral.sh/uv/) installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- No Temporal server or other services required.

### One-time setup

```bash
cd apps/engine
uv sync            # creates .venv with Python >= 3.14 + dev deps (pytest, ruff, ...)
```

### Running the suite

All commands below run from `apps/engine/`.

```bash
# Everything (10 tests, < 1 s)
uv run pytest

# Verbose: one line per test
uv run pytest -v

# Stop at the first failure, show full tracebacks
uv run pytest -x --tb=long
```

### Selecting subsets

```bash
# A single file
uv run pytest tests/test_worker.py

# A single test function
uv run pytest tests/test_study_run.py::test_workflow_name_matches_api_contract

# All tests matching a keyword
uv run pytest -k "env"          # env-var validation branches
uv run pytest -k "workflow"     # workflow execution tests
```

### Useful flags

```bash
uv run pytest --collect-only -q     # list tests without running them
uv run pytest -q                    # quiet summary
uv run pytest --lf                  # re-run only last-failed tests
uv run pytest -p no:cacheprovider   # don't touch .pytest_cache
```

### Coverage report

Coverage tooling isn't part of the default install; run it ad hoc with:

```bash
uv run --with pytest-cov pytest --cov=app --cov-report=term-missing
```

**Current result: 92% line coverage (35/38 statements), 10 passed.**

| Module | Stmts | Miss | Cover |
|---|---:|---:|---:|
| `app/worker.py` | 29 | 3 | 90% |
| `app/workflows/study_run.py` | 9 | 0 | 100% |
| **TOTAL** | **38** | **3** | **92%** |

Notes on interpreting the number:

- The workflow module — the actual product logic — is at **100%**.
- The 3 missed lines in `app/worker.py` are the module-level `.env` discovery loop
  (no `.env` file exists at import time, so the loop body never runs) and the
  `if __name__ == "__main__"` guard — both only execute as side effects that can't be
  exercised through `main()` (see §8).

### Linting the suite (same as CI)

```bash
uv run ruff check .
```

### Configuration reference (`pyproject.toml`)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]          # where tests live
pythonpath = ["."]             # makes `app` and `fakes` importable
asyncio_mode = "auto"          # async test functions run without markers
asyncio_default_fixture_loop_scope = "function"
```

---

## 7. CI Integration

`.github/workflows/ci-engine.yml` runs on every push/PR touching `apps/engine/**`, as two
jobs — both installing via `uv sync --locked` (matching local dev; `uv` provisions its own
pinned Python 3.14 rather than relying on `actions/setup-python`):

```yaml
  lint:
    name: Lint (ruff)
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v10.0.1
        with:
          python-version: "3.14"
          enable-cache: true
      - run: uv sync --locked
      - run: uv run ruff check .

  test:
    name: Test (pytest)
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v10.0.1
        with:
          python-version: "3.14"
          enable-cache: true
      - run: uv sync --locked
      - run: uv run pytest -q
```

No service containers are needed because the suite is fully hermetic — the workflow tests
use Temporal's bundled in-process test server.

Historical note (mirroring `apps/api`): dev dependencies live in `[dependency-groups]`
(PEP 735), which a plain `pip install -e ".[dev]"` silently ignores — always install with
`uv sync` so `pytest`/`ruff` are actually present.

---

## 8. Known Limitations

1. **The module-level `.env` discovery loop in `app/worker.py` is untestable.** The
   `for env_file in Path(...).rglob(".env")` / `load_dotenv(env_file)` / `break` block
   runs at import time, before any test executes; with no `.env` present at import it
   finds nothing and its body never runs. This is intentionally out of scope — the
   relevant testable surface is `main()`'s env-var *validation*, which the tests cover.
2. **The `if __name__ == "__main__": asyncio.run(main())` guard is not executed.** It only
   fires when `app/worker.py` is run as a script; tests import the module instead. If a
   subprocess-level test is ever wanted, it would exec the file as `__main__`.
3. **`StudyRunWorkflow` is a placeholder.** The tests pin its current contract (name,
   signature, 30s sleep, `None` result) because the real SSR pipeline steps
   (reaction → embed → cosine → shift → normalize → expected value → penalty,
   Bradley-Terry ranking, report synthesis) are not implemented yet. When those land,
   this test file should grow coverage for each new activity/step, and the
   `test_workflow_skip_time_from_thirty_second_sleep` expectation will change.