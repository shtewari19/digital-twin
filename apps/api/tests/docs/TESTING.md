# Core API — Pytest Suite Documentation

> **Location:** `apps/api/tests/`
> **Suite size:** 122 tests across 16 test files · **Line coverage:** 100% · **Runtime:** < 10 s · **External services needed:** none

This document describes every application module covered by the pytest suite under
`apps/api/tests/`, what each test verifies, how the hermetic test infrastructure works,
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

The suite tests the FastAPI backend (`apps/api/app/`) at two levels:

| Level | What | How |
|---|---|---|
| **Unit** | Pure logic modules (config, pagination, JWT auth, Temporal holder, LLM client, DB models, schemas) | Direct function calls |
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
    ├── test_api_runs.py        #      ( 7 tests)
    ├── test_llm_client.py      #      (18 tests)
    ├── test_schemas.py         #      (28 tests)
    ├── test_db_models.py       #      (14 tests)
    ├── test_db_session.py      #      ( 2 tests)
    └── test_logging.py         #      ( 2 tests)
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
| `app/llm/llm_client.py` | `test_llm_client.py` | 18 |
| `app/schemas/*` | `test_schemas.py` | 28 |
| `app/db/models/*` | `test_db_models.py` | 14 |
| `app/db/session.py` | `test_db_session.py` | 2 |
| `app/core/logging.py` | `test_logging.py` | 2 |
| **Total** | | **122** |

Not covered by design: `scripts/` (seed/dev helper scripts), `app/db/base.py`
(trivial declarative base), and `app/llm/llm_call.py` (CLI harness script).

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

### 3.10 `app/llm/llm_client.py` — tested by `test_llm_client.py`

Covers the LiteLLM wrapper, provider key resolution, and error-handling branches.
All tests monkeypatch `litellm.completion` and `os.getenv` so no real LLM calls are made.

*API key resolution (`_get_default_api_key`)*

| Test | Verifies |
|---|---|
| `test_anthropic` | Returns `ANTHROPIC_API_KEY` for `anthropic/*` models. |
| `test_openai` | Returns `OPENAI_API_KEY` for `openai/*` models. |
| `test_azure` | Returns `AZURE_OPENAI_API_KEY` for `azure/*` models. |
| `test_unknown_provider_returns_none` | `local/llama` → `None` (no key configured). |
| `test_env_not_set_returns_none` | Missing env var → `None` (no crash). |

*Happy path (`call_llm`)*

| Test | Verifies |
|---|---|
| `test_returns_content` | Mocked completion returns the LLM response string. |
| `test_uses_explicit_api_key_over_env` | Explicit `api_key` param wins over environment variable. |
| `test_azure_sets_api_base_and_version` | Azure models pass `api_base` and `api_version` to `litellm.completion`. |

*Error paths (`call_llm`)*

| Test | Verifies |
|---|---|
| `test_rejects_empty_prompt` | Empty string → `ValueError("Prompt cannot be empty.")`. |
| `test_rejects_whitespace_prompt` | Whitespace-only string → same `ValueError`. |
| `test_raises_when_no_api_key` | No env var + no explicit key → `LLMProviderError("No API key configured")`. |
| `test_raises_rate_limit_error` | `litellm.RateLimitError` → `LLMRateLimitError`. |
| `test_raises_timeout_error` | `litellm.Timeout` → `LLMTimeoutError`. |
| `test_raises_provider_error_on_generic_exception` | Any `Exception` → `LLMProviderError`. |
| `test_raises_on_empty_response_content` | Response with `content=None` → `LLMProviderError("Empty response")`. |

*Exception hierarchy*

| Test | Verifies |
|---|---|
| `test_rate_limit_is_llm_error` | `LLMRateLimitError` subclasses `LLMError`. |
| `test_timeout_is_llm_error` | `LLMTimeoutError` subclasses `LLMError`. |
| `test_provider_is_llm_error` | `LLMProviderError` subclasses `LLMError`. |

### 3.11 `app/schemas/*` — tested by `test_schemas.py`

Validates Pydantic schema defaults, enum values, alias handling, and `from_attributes`
round-trips across all four schema modules.

*common.py*

| Test | Verifies |
|---|---|
| `test_defaults` | `Problem()` has `type="about:blank"`, all optional fields `None`. |
| `test_with_values` | `Problem(status=404, detail="No study")` stores values. |
| `test_generic_structure` | `Page[Domain]` has `data`, `next_cursor`, `has_more`. |
| `test_has_data_and_meta` | `Page` with data and cursor round-trips correctly. |
| `test_from_attributes` | `Timestamps.model_validate(mock_row)` reads `created_at`/`updated_at`. |
| `test_enum_values` | `Priority.HIGH == "high"`, `MEDIUM == "medium"`, `LOW == "low"`. |

*core.py enums*

| Test | Verifies |
|---|---|
| `test_values` (DomainType) | `PREDEFINED == "predefined"`, `CUSTOM == "custom"`. |
| `test_values` (StudyStatus) | `DRAFT == "draft"`, `READY == "ready"`, `ARCHIVED == "archived"`. |
| `test_values` (IngestStatus) | All 4 values: `pending`, `processing`, `ready`, `failed`. |
| `test_values` (AvatarScope) | `LIBRARY == "library"`, `STUDY == "study"`. |
| `test_values` (AvatarSource) | `PREBUILT`, `CUSTOM`, `LLM_ASSISTED`. |

*core.py schemas*

| Test | Verifies |
|---|---|
| `test_from_attributes` (Domain) | `Domain.model_validate(mock_row)` round-trips name, type. |
| `test_domain_list_is_page` | `DomainList` is a `Page[Domain]` subclass. |

*platform.py*

| Test | Verifies |
|---|---|
| `test_values` (Role) | `OPERATOR == "operator"`, `ADMIN == "admin"`. |
| `test_values` (JobKind) | `EXPORT`, `REINDEX`. |
| `test_values` (JobStatus) | `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`. |
| `test_values` (ModelType) | `CHAT`, `EMBEDDING`. |
| `test_rejects_invalid_email` | `User(email="not-an-email")` → `ValidationError`. |
| `test_accepts_valid_email` | Valid email accepted, role stored. |

*runs.py*

| Test | Verifies |
|---|---|
| `test_all_values` (RunStatus) | All 11 status values match DB enum. |
| `test_model_config_alias` | `RunCreate(model_config={...})` deserializes via alias to `model_settings`. |
| `test_default_values` (RunCreate) | `model_settings=None`, `repetitions=1`. |
| `test_schema_matches_db` | Schema `RunStatus` values equal `db.models.run.RunStatus` values. |
| `test_values` (ExportFormat) | `markdown`, `pdf`, `docx`, `pptx`. |
| `test_values` (RunEventType) | All 5 event types. |
| `test_values` (RecommendationTier) | `recommended`, `runner_up`, `drop`. |
| `test_all_none_by_default` (ModelConfig) | All model fields default to `None`. |
| `test_minimal` (RunResults) | Empty ranking, null `baseline_lift_pct`. |

### 3.12 `app/db/models/*` — tested by `test_db_models.py`

Verifies ORM model definitions (table names, schemas, defaults) without a live database.

| Test | Verifies |
|---|---|
| `test_domain_inherits_base` | `Domain` subclasses `Base`. |
| `test_run_inherits_base` | `Run` subclasses `Base`. |
| `test_user_inherits_base` | `User` subclasses `Base`. |
| `test_tablename` (Domain) | `Domain.__tablename__ == "domains"`. |
| `test_schema` (Domain) | Table args include `"schema": "core"`. |
| `test_tablename` (Run) | `Run.__tablename__ == "runs"`. |
| `test_schema` (Run) | Table args include `"schema": "runs"`. |
| `test_status_column_default` | `Run.status` column default is `"draft"`. |
| `test_workflow_id_defaults_none` | `Run(study_id=...).workflow_id` is `None`. |
| `test_all_members` (RunStatus) | 11 values match the DB CHECK constraint. |
| `test_is_str_enum` (RunStatus) | `RunStatus.DRAFT == "draft"` (str enum for JSON). |
| `test_member_count` (RunStatus) | Exactly 11 enum members. |
| `test_tablename` (User) | `User.__tablename__ == "users"`. |
| `test_schema` (User) | Table args include `"schema": "core"`. |

### 3.13 `app/db/session.py` — tested by `test_db_session.py`

| Test | Verifies |
|---|---|
| `test_uses_asyncpg_driver` | `engine.url.drivername == "postgresql+asyncpg"`. |
| `test_yields_session` | `get_db()` async generator yields an `AsyncSession`. |

### 3.14 `app/core/logging.py` — tested by `test_logging.py`

| Test | Verifies |
|---|---|
| `test_is_importable` | `configure_logging` can be imported and is callable. |
| `test_repo_root_on_sys_path` | After import, the monorepo root is in `sys.path` for the `utility.logging` import. |

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

## 5. Mocking Techniques & What Is Mocked Where

The suite **does not use `unittest.mock`**, `MagicMock`, `patch`, or any third-party
mocking library. All test doubles are either pytest's built-in `monkeypatch` fixture,
FastAPI's `dependency_overrides`, or hand-written fake classes in `fakes.py`.

### 5.1 Technique Summary

| Technique | Mechanism | Purpose |
|---|---|---|
| **`monkeypatch.setattr`** | Replaces a module-level attribute (function, class, or variable) with a test double for the duration of the test. | Swap out real DB sessions, Temporal clients, LLM backends, and auth validators with controllable fakes. |
| **`monkeypatch.setenv` / `delenv`** | Sets or removes environment variables for the test scope. | Control API key resolution per LLM provider without touching the real environment. |
| **FastAPI `dependency_overrides`** | Registers a replacement callable for a FastAPI `Depends()` dependency; cleared automatically by `conftest.py` after each test. | Override `get_db` (database session) and `get_current_user` (auth) at the HTTP layer without touching internal code. |
| **Hand-written fake classes** (`fakes.py`) | Lightweight in-memory stand-ins that record calls and replay canned data. | Replace DB sessions, Temporal clients, workflow handles, and async task scheduling with deterministic objects. |
| **Real RSA keypair + fake JWKS** | Tests generate a real RSA key pair, sign tokens with the private key, and feed the public key via a fake JWKS client. | Exercise genuine RS256 signature verification offline without Azure Entra. |
| **`SimpleNamespace`** | Lightweight `types.SimpleNamespace` objects used as stand-in data containers. | Simulate ORM rows (`from_attributes`) and litellm response objects without importing real models. |
| **`pytest.mark.parametrize`** | A single test function runs against multiple input variants defined in the `@parametrize` decorator. | Cover malformed cursors, invalid JWT claims, and non-bearer headers in a single definition. |

### 5.2 What Is Mocked in Each Test File

| Test file | What is mocked | How | Why |
|---|---|---|---|
| `test_api_health.py` | `app.main.init_temporal_client` | `monkeypatch.setattr` (via autouse `_never_touch_real_services` fixture) → replaced with a no-op async function. | Prevent real Temporal connection during app startup. |
| `test_api_runs.py` | `app.core.temporal._client` | `monkeypatch.setattr` → `FakeTemporalClient` | Record `start_workflow` calls without a Temporal server. |
| | `app.api.v1.runs.asyncio` | `monkeypatch.setattr` → `RecordingTaskFactory` | Capture background `create_task` calls for deterministic assertion. |
| | `app.api.v1.runs.SessionLocal` | `monkeypatch.setattr` → lambda returning `FakeSessionContext` | Replace the real DB session factory with a fake. |
| | `app.api.deps.get_db` | FastAPI `dependency_overrides` → `FakeAsyncSession` | Inject a fake DB session at the HTTP dependency level. |
| `test_api_me.py` | `app.api.deps.get_current_user` | FastAPI `dependency_overrides` (via `current_user` fixture) → fake `User` row. | Provide an authenticated user without real JWT validation. |
| | `app.api.deps.get_db` | FastAPI `dependency_overrides` → `FakeAsyncSession` | Avoid real DB queries. |
| `test_api_domains.py` | `app.api.deps.get_db` | FastAPI `dependency_overrides` → `FakeAsyncSession(execute_rows=[...])` | Replay a fixed set of `Domain` rows for pagination testing. |
| | `app.api.deps.get_current_user` | FastAPI `dependency_overrides` (via `current_user` fixture). | Satisfy auth dependency. |
| `test_auth.py` | `app.core.auth._get_jwks_client` | `monkeypatch.setattr` → `_FakeJWKClient` (holds a real RSA **public** key). | Replace the Azure Entra JWKS endpoint with an offline fake that returns a known key. |
| | `app.core.auth._jwks_client` | `monkeypatch.setattr(auth, "_jwks_client", None)` | Reset the singleton cache for the JWKS client factory test. |
| `test_deps.py` | `app.api.deps.validate_token` | `monkeypatch.setattr(deps, "validate_token", stub)` | Return controlled claims dicts or raise `TokenError` without real JWT processing. |
| `test_temporal.py` | `app.core.temporal._client` | `monkeypatch.setattr` → `_FakeClient` instances. | Test singleton guard and initialization without `temporalio.Client.connect`. |
| | `app.core.temporal.Client` | `monkeypatch.setattr` → `_ConnectableClient` (captures `connect()` args). | Verify correct host/namespace are passed to Temporal. |
| `test_llm_client.py` | `app.llm.llm_client.litellm.completion` | `monkeypatch.setattr` → fake functions returning `SimpleNamespace` objects or raising `litellm.RateLimitError` / `litellm.Timeout`. | Test all happy-path and error branches without real LLM API calls. |
| | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION` | `monkeypatch.setenv` / `monkeypatch.delenv` | Control which API keys are "set" to test provider resolution logic. |
| `test_schemas.py` | *(no mocking)* | `SimpleNamespace` used as lightweight stand-in objects for `from_attributes` round-trips. | Not a true mock — just a minimal data container to test Pydantic `model_validate()`. |
| `test_config.py` | *(no mocking)* | `Settings(_env_file=None, **base)` constructed directly. | Bypass `.env` file loading entirely; test derived properties with controlled inputs. |
| `test_db_models.py` | *(no mocking)* | Direct inspection of class attributes, `__table_args__`, enum members. | Pure structural tests — no runtime behavior to stub. |
| `test_db_session.py` | *(no mocking)* | Tests the module-level `engine` object and `get_db` generator shape directly. | Verify static configuration; no DB connection is opened. |
| `test_pagination.py` | *(no mocking)* | Pure function calls to `encode_cursor` / `decode_cursor`. | Deterministic encode/decode logic needs no fakes. |
| `test_logging.py` | *(no mocking)* | Import-and-inspect only. | Smoke test — checks importability, not behavior. |

### 5.3 The Fake Classes (from `fakes.py`) — Detailed

These hand-written fakes replace `unittest.mock` objects. They are explicit, typed, and
record calls for assertion rather than using `assert_called_with` patterns.

#### `FakeAsyncSession` — replaces SQLAlchemy `AsyncSession`

```python
# Replays canned rows for any SELECT, records mutations
session = FakeAsyncSession(execute_rows=[row1, row2, row3])
result = await session.execute(select(Domain))
# result.scalars().all() returns [row1, row2, row3]

# Inspect what happened
assert session.commit_count == 1
assert len(session.added) == 1          # one INSERT
assert session.refreshed == [inserted_row]
```

Key behaviors:
- `execute_rows` → returned by `scalars().all()`
- `get_result` → returned by `get(model, pk)`
- Records `added`, `commit_count`, `refreshed`, `last_stmt`, `last_get`
- `refresh()` fills in a UUID `id` if missing (mirrors `gen_random_uuid()`)

#### `FakeTemporalClient` — replaces `temporalio.Client`

```python
client = FakeTemporalClient(handle=FakeWorkflowHandle(result_value="done"))
handle = await client.start_workflow(
    "study_run_workflow", arg, id="run-123", task_queue="default"
)
# client.started_records == [("study_run_workflow", arg, "run-123", "default")]
# await handle.result() == "done"
```

Key behaviors:
- Records every `start_workflow` call in `started_records`
- Returns a configurable `FakeWorkflowHandle`
- `FakeWorkflowHandle.result()` either returns a value or raises an exception

#### `RecordingTaskFactory` — replaces `asyncio` module inside `runs.py`

```python
factory = RecordingTaskFactory()
# patched in as `runs.asyncio`
task = factory.create_task(some_coroutine())
# factory.tasks == [coroutine]  — captured, not scheduled
factory.close_all()  # prevents "coroutine never awaited" warnings
```

Key behaviors:
- Captures coroutines passed to `create_task` without actually scheduling them
- Enables deterministic assertion of background task scheduling

---

## 6. How to Run the Tests Manually

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
# Everything (122 tests, < 10 s)
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

# Only LLM client tests
uv run pytest tests/test_llm_client.py

# Only schema validation tests
uv run pytest tests/test_schemas.py
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

**Current result: 100% line coverage (749/749 statements), 122 passed.**

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
- Line coverage says nothing about the real SQL filtering (see §8).

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

## 8. Known Limitations

1. **SQL-side keyset filtering is not exercised.** `FakeAsyncSession` replays a fixed
   row set, so the `tuple_(created_at, id) < (...)` predicate in `list_domains` never
   actually filters. Page-2 correctness of that SQL needs an integration test against
   real Postgres (e.g., docker-compose + a dedicated test database).
2. **`scripts/` are untested** (`seed_dev_data.py`, `get_dev_token.py`,
   `apply_schema.py`) — they are operational helpers, not application code.
3. **`app/llm/llm_call.py` is untested** — it is a CLI harness script, not application
   logic; it calls `call_llm` directly and would require a live LLM API.
4. **Pre-existing deprecation warnings surface during runs** (6 warnings): FastAPI's
   deprecated `@app.on_event("startup")` in `app/main.py` and Starlette's deprecated
   `HTTP_422_UNPROCESSABLE_ENTITY` constant in `app/api/v1/domains.py`. They originate
   in application code, not the tests; migrating to a lifespan handler and the
   `..._CONTENT` constant would silence them.

