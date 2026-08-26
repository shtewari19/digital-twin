# Core API — Pytest Suite Documentation

> **Location:** `apps/api/tests/`
> **Suite size:** 58 tests across 9 test files · **Line coverage:** 100% · **Runtime:** < 1 s · **External services needed:** none

This document describes every application module covered by the pytest suite under
`apps/api/tests/`, what each test verifies, how the hermetic test infrastructure works,
and how to run the suite manually.

---

## Table of Contents

1. [Overview & Testing Philosophy](#1-overview--testing-philosophy)
2. [Directory Layout](#2-directory-layout)
3. [Coverage Detail — What Is Tested Where](#3-coverage-detail--what-is-tested-where)
4. [Test Infrastructure](#4-test-infrastructure)
5. [How to Run the Tests Manually](#5-how-to-run-the-tests-manually)
6. [CI Integration](#6-ci-integration)
7. [Known Limitations](#7-known-limitations)

---

## 1. Overview & Testing Philosophy

The suite tests the FastAPI backend (`apps/api/app/`) at two levels:

| Level | What | How |
|---|---|---|
| **Unit** | Pure logic modules (config, pagination, JWT auth, Temporal holder) | Direct function calls |
| **Contract** | HTTP endpoints (`/health`, `/me`, `/domains`, `/runs`) | `fastapi.testclient.TestClient` with dependency overrides |

Three deliberate design decisions make the suite **hermetic** (no Postgres,
Temporal server, or Microsoft Entra ID required):

1. **Environment bootstrapping before imports** — `Settings()` is instantiated the
   moment `app.core.config` is imported, so `conftest.py` sets all `APP_*`
   environment variables *before* any `app.*` module import.
2. **Fake database sessions** — `FakeAsyncSession` replays canned query results and
   records every call, instead of talking to Postgres.
3. **Real RS256 cryptography, fake JWKS** — JWT validation tests sign tokens with a
   throwaway RSA key pair and hand the public key to `validate_token` through a fake
   JWKS client, so signature and claim verification are genuinely exercised offline.

---

## 2. Directory Layout

```
apps/api/
├── pyproject.toml              # pytest + ruff config, dev dependencies
└── tests/
    ├── docs/
    │   ├── TESTING.md          # ← this document
    │   └── TESTING.pdf         # PDF rendition of this document
    ├── conftest.py             # shared fixtures + env bootstrap
    ├── fakes.py                # in-memory stand-ins for DB / Temporal
    ├── test_config.py          #      ( 5 tests)
    ├── test_pagination.py      #      ( 9 tests)
    ├── test_auth.py            #      (15 tests)
    ├── test_temporal.py        #      ( 4 tests)
    ├── test_deps.py            #      ( 6 tests)
    ├── test_api_health.py      #      ( 2 tests)
    ├── test_api_me.py          #      ( 2 tests)
    ├── test_api_domains.py     #      ( 8 tests)
    └── test_api_runs.py        #      ( 7 tests)
```

**Coverage summary**

| Source module | Test file | Tests |
|---|---|---:|
| `app/core/config.py` | `test_config.py` | 5 |
| `app/api/pagination.py` | `test_pagination.py` | 9 |
| `app/core/auth.py` | `test_auth.py` | 15 |
| `app/core/temporal.py` | `test_temporal.py` | 4 |
| `app/api/deps.py` | `test_deps.py` | 6 |
| `app/main.py` | `test_api_health.py` | 2 |
| `app/api/v1/me.py` | `test_api_me.py` | 2 |
| `app/api/v1/domains.py` | `test_api_domains.py` | 8 |
| `app/api/v1/runs.py` | `test_api_runs.py` | 7 |
| **Total** | | **58** |

Not covered by design: `scripts/` (seed/dev helper scripts), `app/db/session.py`
(thin engine wiring), `app/db/base.py`, `app/core/logging.py`, and schema modules
(`app/schemas/*`) beyond their incidental exercise through endpoint responses.

---

## 3. Coverage Detail — What Is Tested Where

### 3.1 `app/core/config.py` — tested by `test_config.py`

Verifies the derived properties of the `Settings` class (constructed directly with
`_env_file=None`, so no local `.env` leaks into assertions):

| Test | Verifies |
|---|---|
| `test_async_database_url_builds_asyncpg_dsn` | `async_database_url` composes the correct `postgresql+asyncpg://user:pass@host:port/db` DSN from discrete fields. |
| `test_jwks_uri_points_at_configured_tenant` | JWKS URL embeds the configured Entra tenant ID. |
| `test_jwt_issuer_is_the_tenant_v2_endpoint` | Expected `iss` claim matches the tenant's v2 token endpoint. |
| `test_jwt_audience_defaults_to_client_id` | When `entra_audience` is unset, `jwt_audience` falls back to `entra_client_id`. |
| `test_jwt_audience_prefers_explicit_override` | An explicit `entra_audience` wins over the client-ID fallback. |

### 3.2 `app/api/pagination.py` — tested by `test_pagination.py`

Covers the opaque cursor scheme shared by all list endpoints:

| Test | Verifies |
|---|---|
| `test_roundtrip_preserves_keyset` | `decode_cursor(encode_cursor(...))` restores the exact `(created_at, id)` keyset. |
| `test_encode_is_deterministic_and_input_sensitive` | Same input → same cursor; changed input → different cursor. |
| `test_cursor_dataclass_is_frozen` | `Cursor` rejects attribute mutation (frozen dataclass contract). |
| `test_decode_rejects_malformed_cursors[...]` | 5 parametrized garbage cursors (empty string, invalid base64, missing pipe separator, bad UUID half, bad timestamp half) all raise `InvalidCursorError`. |
| `test_invalid_cursor_error_is_a_value_error` | `InvalidCursorError` subclasses `ValueError`, so callers catching either type keep working. |

### 3.3 `app/core/auth.py` — tested by `test_auth.py`

The most security-critical module. A fixture replaces `_get_jwks_client` with a fake
holding a real RSA **public** key; tokens are signed with the matching private key.

*JWT validation (`validate_token`)*

| Test | Verifies |
|---|---|
| `test_validate_token_accepts_valid_rs256_token` | A correctly signed token with valid `iss`/`aud`/`exp` passes and returns its claims. |
| `test_validate_token_rejects_bad_claims[expired]` | Expired token → `TokenError("Token has expired.")`. |
| `test_validate_token_rejects_bad_claims[wrong-audience]` | Foreign audience → audience mismatch error. |
| `test_validate_token_rejects_bad_claims[wrong-issuer]` | Untrusted issuer → issuer error. |
| `test_validate_token_requires_standard_claims` | Removing a required claim (`iat`) → generic validation failure. |
| `test_validate_token_rejects_tampered_signature` | Modifying the signature bytes after signing → rejection. |
| `test_validate_token_flags_malformed_tokens` | A non-JWT string → `"Token is malformed"`. |
| `test_validate_token_reports_unknown_signing_key` | JWKS lookup failure (`PyJWKClientError`) → `"Unable to locate the signing key"`. |
| `test_get_jwks_client_is_lazy_cached_singleton` | The JWKS client factory builds a `PyJWKClient` for the configured tenant URI and caches the instance across calls. |

*Header parsing (`extract_bearer`)*

| Test | Verifies |
|---|---|
| `test_extract_bearer_returns_raw_token` | `Bearer abc.def.ghi` → raw JWT string. |
| `test_extract_bearer_scheme_is_case_insensitive` | Lowercase `bearer` accepted per RFC 7235. |
| `test_extract_bearer_missing_header` | `None` header → error. |
| `test_extract_bearer_rejects_non_bearer_headers[...]` | 3 parametrized cases: Basic scheme, bare `Bearer` with no token, empty string. |

### 3.4 `app/core/temporal.py` — tested by `test_temporal.py`

| Test | Verifies |
|---|---|
| `test_get_temporal_client_raises_before_initialization` | Accessing the client before startup raises `RuntimeError`. |
| `test_get_temporal_client_returns_initialized_client` | After init, the exact singleton instance is returned. |
| `test_workflow_name_matches_engine_contract` | `STUDY_RUN_WORKFLOW_NAME == "study_run_workflow"` — guards the name contract with `apps/engine`'s `@workflow.defn`. |
| `test_init_temporal_client_stores_singleton` | `Client.connect` receives the configured host/namespace and the result is stored for `get_temporal_client`. |

### 3.5 `app/api/deps.py` — tested by `test_deps.py`

Exercises the full `get_current_user` dependency against a `FakeAsyncSession`, with
`validate_token` stubbed (it is a *sync* function — stubs must be sync too).

| Test | Verifies |
|---|---|
| `test_missing_credentials_yields_401` | No Authorization header → `HTTPException` 401 with `WWW-Authenticate: Bearer`. |
| `test_invalid_token_yields_401` | `validate_token` raising `TokenError` → same 401 envelope. |
| `test_claims_without_identity_yield_401` | Valid token lacking identity claims → 401 (never touches the DB). |
| `test_first_login_jit_provisions_operator` | Unknown `oid` → new `User` row added + committed: role `operator`, tenant UUID parsed from `tid`, `last_login_at` stamped. |
| `test_existing_user_login_refreshes_last_login` | Known user returned unchanged, `last_login_at` updated, no new row inserted. |
| `test_sub_claim_used_when_oid_absent_and_upn_for_email` | Claim fallback chain: `sub`→identity, `upn`→email, missing name → `"Unknown"`. |

### 3.6 `app/main.py` — tested by `test_api_health.py`

| Test | Verifies |
|---|---|
| `test_health_liveness_probe` | `GET /health` returns `200 {"status": "ok"}`. |
| `test_startup_hook_is_safe_without_temporal` | Entering the lifespan fires the startup hook without a real Temporal server (conftest stub), and the app still serves afterwards. |

### 3.7 `app/api/v1/me.py` — tested by `test_api_me.py`

| Test | Verifies |
|---|---|
| `test_me_returns_profile_of_current_user` | `GET /api/v1/me` serializes the stubbed user: `id`, `name`, `email`, `role`. |
| `test_me_requires_authentication` | No credentials → 401, `WWW-Authenticate: Bearer` header, problem detail text. |

### 3.8 `app/api/v1/domains.py` — tested by `test_api_domains.py`

Contract-level coverage of the keyset-paginated listing endpoint. The SQL-side
keyset filter itself is out of scope (see §7); what is verified:

| Test | Verifies |
|---|---|
| `test_list_domains_returns_rows_in_query_order` | Response `data[]` mirrors query order; single page → `has_more=false`, `next_cursor=null`. |
| `test_list_domains_empty_table_yields_empty_page` | Empty result set → `{"data": [], "next_cursor": null, "has_more": false}`. |
| `test_list_domains_signals_more_pages_with_next_cursor` | With `limit=2` and 3 rows: page truncated, `has_more=true`, and `next_cursor` decodes to the *last emitted row's* `(created_at, id)`. |
| `test_list_domains_accepts_next_cursor_for_follow_up_page` | A well-formed `next_cursor` passed back as `?cursor=` reaches the keyset `WHERE` branch and yields a valid follow-up page. |
| `test_list_domains_rejects_invalid_cursor_with_422[...]` | 3 parametrized malformed cursors → HTTP 422 with detail `"Invalid pagination cursor."`. |
| `test_list_domains_validates_limit_bounds` | `limit=0` and `limit=101` → HTTP 422 (Query constraint `ge=1, le=100`). |

### 3.9 `app/api/v1/runs.py` — tested by `test_api_runs.py`

*Creation*

| Test | Verifies |
|---|---|
| `test_create_run_persists_draft_run` | `POST /studies/{id}/runs` → 201; row persisted as `draft`; response carries `study_id`, null `workflow_id`; exactly one commit. |
| `test_create_run_rejects_non_uuid_study_id` | Non-UUID path segment → 422. |

*Starting a run*

| Test | Verifies |
|---|---|
| `test_start_unknown_run_returns_404` | Missing run → 404 `"run not found"`. |
| `test_start_non_draft_run_conflicts` | Starting a `queued` run → 409 with current status in the detail. |
| `test_start_run_starts_workflow_and_marks_running` | Happy path: workflow started on Temporal with the right name/argument/workflow-id/task-queue, run ends `running` with `workflow_id=study-run-{id}`, background finalizer scheduled exactly once. |

*Background finalizer (`_finalize_when_done`)*

| Test | Verifies |
|---|---|
| `test_finalize_marks_success_when_workflow_succeeds` | Workflow completes → run status `finalized`, one commit. |
| `test_finalize_marks_failure_when_workflow_raises` | Workflow raises → run status `failed`, one commit. |

---

## 4. Test Infrastructure

### 4.1 `tests/conftest.py`

| Fixture | Scope | Purpose |
|---|---|---|
| *(module top-level code)* | — | Sets every required `APP_*` env var via `os.environ.setdefault` **before** any `app.*` import, because `Settings()` runs at import time. |
| `app` | function | Imports and yields the FastAPI application singleton. |
| `client` | function | Synchronous `TestClient`. Deliberately **not** used as a context manager — entering the lifespan would fire the startup hook that connects to Temporal. |
| `_never_touch_real_services` (autouse) | function | Safety net: monkeypatches `app.main.init_temporal_client` to a no-op and clears all `dependency_overrides` after every test, so overrides never leak between tests. |
| `current_user` | function | Registers a `get_current_user` override returning a ready-made JIT-style `User` row; protected routes see this identity. |

### 4.2 `tests/fakes.py`

| Fake | Replaces | Notes |
|---|---|---|
| `FakeResult` | SQLAlchemy `Result` | Implements `scalars().all()` and `scalar_one_or_none()`. |
| `FakeAsyncSession` | `AsyncSession` | Canned results (`execute_rows`, `get_result`) plus call recording (`added`, `commit_count`, `refreshed`, `last_stmt`, `last_get`). Its `refresh()` emulates DB server defaults filling in a missing `id` (mirrors `gen_random_uuid()`). |
| `FakeTemporalClient` | `temporalio.Client` | Records `start_workflow(name, arg, id=..., task_queue=...)` calls; hands out handles. |
| `FakeWorkflowHandle` | `WorkflowHandle` | `result()` either returns normally or raises the configured error — all `_finalize_when_done` needs. |
| `FakeSessionContext` | `async with SessionLocal()` | Async context manager yielding the fake session. |
| `RecordingTaskFactory` | the `asyncio` module inside `runs.py` | Captures-and-closes coroutines passed to `create_task`, so background scheduling is asserted deterministically without event-loop noise. |

---

## 5. How to Run the Tests Manually

### Prerequisites

- [uv](https://docs.astral.sh/uv/) installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- No databases or other services required.

### One-time setup

```bash
cd apps/api
uv sync            # creates .venv with Python >= 3.14 + dev deps (pytest, ruff, ...)
```

> If you use plain pip instead of uv:
> `python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`

### Running the suite

All commands below run from `apps/api/`.

```bash
# Everything (55 tests, < 1 s)
uv run pytest

# Verbose: one line per test
uv run pytest -v

# Stop at the first failure, show full tracebacks
uv run pytest -x --tb=long
```

### Selecting subsets

```bash
# A single file
uv run pytest tests/test_auth.py

# A single test function
uv run pytest tests/test_api_runs.py::test_start_unknown_run_returns_404

# All tests matching a keyword (name, class, or parametrize id)
uv run pytest -k "cursor"
uv run pytest -k "jwt or pagination"

# Only the HTTP contract tests / only pure unit tests
uv run pytest tests/test_api_*.py
uv run pytest --ignore=tests/test_api_health.py -k "not api"
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

**Current result: 100% line coverage (749/749 statements), 58 passed.**

| Module | Stmts | Miss | Cover |
|---|---:|---:|---:|
| `app/api/deps.py` | 49 | 0 | 100% |
| `app/api/pagination.py` | 21 | 0 | 100% |
| `app/api/v1/domains.py` | 22 | 0 | 100% |
| `app/api/v1/me.py` | 8 | 0 | 100% |
| `app/api/v1/router.py` | 8 | 0 | 100% |
| `app/api/v1/runs.py` | 55 | 0 | 100% |
| `app/core/auth.py` | 41 | 0 | 100% |
| `app/core/config.py` | 31 | 0 | 100% |
| `app/core/logging.py` | 8 | 0 | 100% |
| `app/core/temporal.py` | 11 | 0 | 100% |
| `app/db/*` (base, models, session) | 91 | 0 | 100% |
| `app/main.py` | 14 | 0 | 100% |
| `app/schemas/*` (common, core, platform, run, runs) | 390 | 0 | 100% |
| **TOTAL** | **749** | **0** | **100%** |

Notes on interpreting the number:

- Schemas and ORM model files are declarative; they hit 100% simply by being imported.
- The meaningful signal is the hand-written logic modules (`auth`, `deps`, `pagination`,
  `runs`, `domains`, `config`, `temporal`) — each is fully exercised branch-by-branch.
- Line coverage says nothing about the real SQL filtering (see §7).

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

## 6. CI Integration

`.github/workflows/ci-api.yml` lints the app on every push/PR touching `apps/api/**`.
The pytest suite is designed to slot straight in — append a job like:

```yaml
  test:
    name: Test (pytest)
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: apps/api
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"
      - name: Install
        run: pip install -e ".[dev]"
      - name: Run tests
        run: pytest -q
```

No service containers are needed because the suite is fully hermetic.

---

## 7. Known Limitations

1. **SQL-side keyset filtering is not exercised.** `FakeAsyncSession` replays a fixed
   row set, so the `tuple_(created_at, id) < (...)` predicate in `list_domains` never
   actually filters. Page-2 correctness of that SQL needs an integration test against
   real Postgres (e.g., docker-compose + a dedicated test database).
2. **`scripts/` are untested** (`seed_dev_data.py`, `get_dev_token.py`,
   `apply_schema.py`) — they are operational helpers, not application code.
3. **Pre-existing deprecation warnings surface during runs** (6 warnings): FastAPI's
   deprecated `@app.on_event("startup")` in `app/main.py` and Starlette's deprecated
   `HTTP_422_UNPROCESSABLE_ENTITY` constant in `app/api/v1/domains.py`. They originate
   in application code, not the tests; migrating to a lifespan handler and the
   `..._CONTENT` constant would silence them.

