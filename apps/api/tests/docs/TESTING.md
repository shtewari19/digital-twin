# Core API — Pytest Suite Documentation

> **Location:** `apps/api/tests/`
> **Suite size:** 243 tests across 23 test files · **Line coverage:** 98% (100% outside `domains.py` — see §8) · **Runtime:** < 10 s · **External services needed:** none

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
    ├── test_api_studies.py     #      (28 tests)
    ├── test_api_messages.py    #      (11 tests)
    ├── test_api_avatars.py     #      (27 tests)
    ├── test_api_sources.py     #      (20 tests)
    ├── test_api_knowledgebase.py #    (12 tests)
    ├── test_api_llm_assist.py  #      (14 tests)
    ├── test_llm_client.py      #      (18 tests)
    ├── test_llm_gateway.py     #      ( 9 tests)
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
| `app/api/v1/studies.py` | `test_api_studies.py` | 28 |
| `app/api/v1/messages.py` | `test_api_messages.py` | 11 |
| `app/api/v1/avatars.py` | `test_api_avatars.py` | 27 |
| `app/api/v1/sources.py` | `test_api_sources.py` | 20 |
| `app/api/v1/knowledgebase.py` | `test_api_knowledgebase.py` | 12 |
| `app/api/v1/llm_assist.py` | `test_api_llm_assist.py` | 14 |
| `app/llm/llm_client.py` | `test_llm_client.py` | 18 |
| `app/core/llm_gateway.py` | `test_llm_gateway.py` | 9 |
| `app/schemas/*` | `test_schemas.py` | 28 |
| `app/db/models/*` | `test_db_models.py` | 14 |
| `app/db/session.py` | `test_db_session.py` | 2 |
| `app/core/logging.py` | `test_logging.py` | 2 |
| **Total** | | **243** |

Not covered by design: `scripts/` (seed/dev helper scripts), `app/db/base.py`
(trivial declarative base), and `app/llm/llm_call.py` (CLI harness script).
`app/api/v1/domains.py` is only partially covered (`list_domains` alone,
55% line coverage) — see §8; it predates the studies/messages/avatars/
sources/knowledgebase/llm_assist vertical and was never brought up to the
same bar.

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

### 3.10 `app/api/v1/studies.py` — tested by `test_api_studies.py`

Studies, plus the `outcome`/`anchors` sub-resources that live as columns/rows
scoped to a study.

| Group | Verifies |
|---|---|
| `GET /studies` | Query order, domain/status filters accepted, cursor round-trip (valid + invalid → 422), limit bounds. |
| `POST /studies` | Unknown `domain_id` → 422; required `description`; `intent` persisted and round-tripped; owner set from the authenticated user. |
| `POST /studies` (known gap) | `test_create_study_omitting_name_currently_raises_instead_of_422` — pins that omitting `name` crashes unhandled rather than 422 (see §8). |
| `GET/PATCH/DELETE /studies/{id}` | 404s; `PATCH` only touches provided fields (`exclude_unset`); `status` transitions serialize correctly and reject invalid values; `DELETE` deletes scoped anchors before the study, and a DB-level conflict (`IntegrityError`) → 409 with rollback. |
| `GET/PUT /studies/{id}/outcome` | 404; defaults (`dimension=""`, `scale=1..5`) when never set; `PUT` persists both fields; missing `scale` → 422. |
| `GET/PUT /studies/{id}/anchors` | 404; empty vs. populated sets; `PUT` fully replaces (one DELETE + N adds); malformed body → 422. |

### 3.11 `app/api/v1/messages.py` — tested by `test_api_messages.py`

Candidate messages nested under a study — not paginated (see the module's
own docstring).

| Group | Verifies |
|---|---|
| `GET /studies/{id}/messages` | Study 404; ordering (position, then created_at). |
| `POST /studies/{id}/messages` | Study 404; `text` required; happy path persists `text`/`group`, `version` starts at 1. |
| `PATCH .../messages/{id}` | 404 when missing *or* when the message belongs to a different study; `version` increments only when a field actually changes (an empty body does not bump it). |
| `DELETE .../messages/{id}` | 404; happy path deletes and commits. |

### 3.12 `app/api/v1/avatars.py` — tested by `test_api_avatars.py`

Avatar CRUD plus the per-study `panel` sub-resource.

| Group | Verifies |
|---|---|
| `GET /avatars` | Query order; `scope`/`domain_id`/`study_id` filters (individually and combined); invalid `scope` → 422; cursor round-trip (valid + invalid → 422); limit bounds. |
| `POST /avatars` — scope validation | `library` scope requires (and validates) `domain_id`; `study` scope requires (and validates) `study_id`; defaults to `library`; the non-selected scope's id is dropped on write; `source` defaults to `custom`. |
| `GET/PATCH/DELETE /avatars/{id}` | Happy path + 404s; `PATCH` is restricted to `name`/`profile` per the API spec; `DELETE` referenced by a panel → 409 via `IntegrityError`. |
| `GET/PUT /studies/{id}/panel` | Study 404 on both; happy path round-trips each member's `replica_count` (`GET` reads the join table, `PUT` replaces it); `avatars` requires ≥1 entry; `replica_count` must be ≥1; defaults to 1 when omitted; unknown avatar id → 422. |

### 3.13 `app/api/v1/sources.py` — tested by `test_api_sources.py`

Multipart uploads (files stored in-DB, see the module docstring) plus the
per-study sufficiency check.

| Group | Verifies |
|---|---|
| `GET /studies/{id}/sources` | Study 404; not paginated. |
| `POST /studies/{id}/sources` | Study 404; a `file` part is required; 413 over the 20 MB cap; `priority` defaults to `medium`, honors an explicit value, rejects an invalid one; missing `content_type` falls back to `application/octet-stream`. |
| `GET /studies/{id}/sufficiency` | Study 404; `LLMProviderError`/`LLMResponseParseError` from `assess_sufficiency` → 502; happy path passes `{filename, summary}` pairs through and returns the LLM's JSON verbatim. |
| `GET/PATCH/DELETE /sources/{id}` | 404s; `PATCH` re-prioritizes only, other fields untouched; `DELETE` happy path. |
| `GET /sources/{id}/analysis` | 404; pending-with-no-tags-yet shape. |

### 3.14 `app/api/v1/knowledgebase.py` — tested by `test_api_knowledgebase.py`

Chunk listing, per-study index status, and semantic search. `POST
.../reindex` isn't implemented in the app (see the module's own docstring),
so there's nothing to test for it.

| Group | Verifies |
|---|---|
| `GET /sources/{id}/chunks` | Source 404; page shape; cursor round-trip (valid + invalid → 422). |
| `GET /studies/{id}/knowledgebase` | Study 404; status math across all three states — `pending` (no chunks), `processing` (partially embedded), `ready` (fully embedded) — and `coverage_pct` computed from the embedded/chunk ratio. |
| `POST .../knowledgebase/search` | Study 404; required `query`; 501 while `embed_texts` is unconfigured (the current, real state); happy-path branch (`embed_texts` mocked) ranks results by cosine distance → `score = 1 - distance`. |

### 3.15 `app/api/v1/llm_assist.py` — tested by `test_api_llm_assist.py`

All four LLM-assisted drafting endpoints. `call_llm_json` (or the route's
own `_run_assist` wrapper) is monkeypatched at the boundary, so these are
prompt-wiring/response-shaping/error-translation tests, not LLM integration
tests — see `test_llm_client.py` / `test_llm_gateway.py` for that layer.

| Group | Verifies |
|---|---|
| `POST /llm/assist/study-name` | `description` required; happy path maps the LLM's JSON into `StudyNameSuggestion`; missing `intent` in the response tolerated as `None`; `LLMProviderError`/`LLMResponseParseError` → 502. |
| `POST /llm/assist/persona` | Unknown `domain_id` → 422; `domain_id` omitted skips the DB lookup entirely; happy path with and without a domain. |
| `POST /llm/assist/messages` | Study 404; `count` defaults to 5 (asserted in the generated prompt text); a non-list `messages` value in the LLM's response degrades to `[]` rather than crashing. |
| `POST /llm/assist/anchors` | `scale` required; happy path; a non-list `anchors` value degrades to `[]`. |

### 3.16 `app/core/llm_gateway.py` — tested by `test_llm_gateway.py`

The app's one seam onto LLM calls (see the module's own docstring). Only
this module's own logic is exercised here — `call_llm` itself (LiteLLM
plumbing) is `test_llm_client.py`'s job.

| Group | Verifies |
|---|---|
| `call_llm_json` | Valid JSON object parses; non-JSON text, a JSON array, and a bare JSON scalar are all rejected as `LLMResponseParseError` (only an object is acceptable); a provider error from `call_llm` propagates unchanged. |
| `embed_texts` | Still raises `LLMGatewayNotConfiguredError` — pins the current, real "not wired up yet" state. |
| `assess_sufficiency` | Builds its listing prompt from `{filename, summary}` pairs; handles zero sources and a source with no summary yet (`"(no summary yet)"` / `"(no sources uploaded yet)"` placeholders). |

### 3.17 `app/llm/llm_client.py` — tested by `test_llm_client.py`

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

### 3.18 `app/schemas/*` — tested by `test_schemas.py`

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

### 3.19 `app/db/models/*` — tested by `test_db_models.py`

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

### 3.20 `app/db/session.py` — tested by `test_db_session.py`

| Test | Verifies |
|---|---|
| `test_uses_asyncpg_driver` | `engine.url.drivername == "postgresql+asyncpg"`. |
| `test_yields_session` | `get_db()` async generator yields an `AsyncSession`. |

### 3.21 `app/core/logging.py` — tested by `test_logging.py`

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
| `FakeAsyncSession` | `AsyncSession` | Canned results — `execute_rows`, `get_result`/`get_results` (sequential, for routes doing more than one `session.get(...)` per request), `scalar_results` (sequential, for routes issuing several `session.scalar(...)` aggregate queries) — plus call recording (`added`, `deleted`, `commit_count`, `rollback_count`, `refreshed`, `last_stmt`, `last_get`, `execute_calls`). `commit_error` makes `commit()` raise once (e.g. `IntegrityError`, for 409-on-delete tests). Its `refresh()` generically emulates a real `INSERT ... RETURNING`: any column still unset on the row is filled from its SQLAlchemy `server_default` (`gen_random_uuid()`, `now()`, string/int/bool literals) or, failing that, its Python-side `default=` (scalar or zero-arg callable) — not just `id`. |
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
| `test_api_studies.py`, `test_api_messages.py`, `test_api_avatars.py`, `test_api_sources.py`, `test_api_knowledgebase.py` | `app.api.deps.get_db` | FastAPI `dependency_overrides` → `FakeAsyncSession` (various `get_result`/`get_results`/`execute_rows`/`scalar_results`/`commit_error` configs per test). | Avoid real DB queries; simulate 404/409/multi-lookup routes without a schema. |
| `test_api_sources.py`, `test_api_llm_assist.py` | `app.api.v1.sources.assess_sufficiency` / `app.api.v1.llm_assist.call_llm_json` (or `_run_assist`) | `monkeypatch.setattr` → async fakes returning canned dicts or raising `LLMProviderError`/`LLMResponseParseError`. | Exercise the 502-translation and response-shaping logic without a real LLM call. |
| `test_api_knowledgebase.py` | `app.api.v1.knowledgebase.embed_texts` | `monkeypatch.setattr` → async fake returning a canned vector. | Exercise the search happy path despite `embed_texts` being unconfigured in the real app. |
| `test_llm_gateway.py` | `app.core.llm_gateway.call_llm` | `monkeypatch.setattr` → fakes returning canned JSON strings or raising `LLMProviderError`. | Test `call_llm_json`'s JSON-mode parsing and `assess_sufficiency`'s prompt-building without real LLM calls. |

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
- `execute_rows` → returned by `scalars().all()` (or `.all()` directly, for
  routes selecting tuples like `(row, distance)`)
- `get_result` / `get_results` → returned by `get(model, pk)`; the plural
  form pops one value per call, for routes doing more than one lookup
- `scalar_results` → returned by `scalar(stmt)`, popped per call (e.g. a
  route issuing several `COUNT(*)` queries in sequence)
- `commit_error` → raised once by `commit()`, then cleared (simulates a
  DB-level failure like `IntegrityError` surfacing as a 409)
- Records `added`, `deleted`, `commit_count`, `rollback_count`, `refreshed`,
  `last_stmt`, `last_get`, `execute_calls`
- `refresh()` fills in *any* column still unset on the row from its
  `server_default` (`gen_random_uuid()`, `now()`, literals) or Python-side
  `default=`, not just `id` — so a freshly-`add()`ed row round-trips the
  same way it would against a real `INSERT ... RETURNING`

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
# Everything (243 tests, < 10 s)
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

**Current result: 98% line coverage (1387/1414 statements), 243 passed.**

| Module | Stmts | Miss | Cover |
|---|---:|---:|---:|
| `app/api/deps.py` | 49 | 0 | 100% |
| `app/api/pagination.py` | 21 | 0 | 100% |
| `app/api/v1/avatars.py` | 95 | 0 | 100% |
| `app/api/v1/domains.py` | 58 | 26 | 55% |
| `app/api/v1/knowledgebase.py` | 53 | 0 | 100% |
| `app/api/v1/llm_assist.py` | 46 | 0 | 100% |
| `app/api/v1/me.py` | 8 | 0 | 100% |
| `app/api/v1/messages.py` | 48 | 0 | 100% |
| `app/api/v1/router.py` | 13 | 0 | 100% |
| `app/api/v1/runs.py` | 55 | 0 | 100% |
| `app/api/v1/sources.py` | 70 | 0 | 100% |
| `app/api/v1/studies.py` | 93 | 0 | 100% |
| `app/core/auth.py` | 41 | 0 | 100% |
| `app/core/config.py` | 32 | 0 | 100% |
| `app/core/llm_gateway.py` | 22 | 0 | 100% |
| `app/core/logging.py` | 8 | 0 | 100% |
| `app/core/temporal.py` | 11 | 0 | 100% |
| `app/db/*` (base, models, session) | 240 | 1 | 99% |
| `app/llm/llm_client.py` | 46 | 0 | 100% |
| `app/main.py` | 14 | 0 | 100% |
| `app/schemas/*` (common, core, platform, run, runs) | 390 | 0 | 100% |
| **TOTAL** | **1414** | **27** | **98%** |

Notes on interpreting the number:

- Schemas and ORM model files are declarative; they hit 100% simply by being imported.
- The meaningful signal is the hand-written logic modules — every one of them is at
  100% except `domains.py` (see below) and one unreachable-without-a-real-connection
  line in `db/session.py` (the pgvector asyncpg codec registration callback).
- **`app/api/v1/domains.py` sits at 55%** — `test_api_domains.py` only exercises
  `list_domains`; `create_domain`/`get_domain`/`update_domain`/`delete_domain` have no
  tests at all. This predates the studies/messages/avatars/sources/knowledgebase/
  llm_assist vertical and was never brought up to par — worth a follow-up pass with
  the same coverage-driven approach used for the newer modules (see §8).
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

`.github/workflows/ci-api.yml` runs on every push/PR touching `apps/api/**`, as two
jobs — both installing via `uv sync --locked` (matching local dev; `uv` provisions its
own pinned Python 3.14 rather than relying on `actions/setup-python`):

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

No service containers are needed because the suite is fully hermetic.

Historical note: this job previously used `pip install -e ".[dev]"`. That broke
silently once dev dependencies were consolidated into `[dependency-groups]` (a plain
`pip install` doesn't read that table, per PEP 735) — `pip` installed zero dev
dependencies without erroring, so the very next `ruff check .` step failed with
"command not found." `apps/engine`'s CI had the same latent risk (fixed the same way,
alongside removing its now-redundant `[project.optional-dependencies]` block).

---

## 8. Known Limitations

1. **SQL-side keyset filtering is not exercised.** `FakeAsyncSession` replays a fixed
   row set, so the `tuple_(created_at, id) < (...)` predicate in every list endpoint
   (`list_domains`, `list_studies`, `list_avatars`, `list_chunks`) never actually
   filters. Page-2 correctness of that SQL needs an integration test against real
   Postgres (e.g., docker-compose + a dedicated test database).
2. **`app/api/v1/domains.py` is only 55% covered.** `test_api_domains.py` exercises
   `list_domains` alone — `create_domain`, `get_domain`, `update_domain`, and
   `delete_domain` have zero test coverage. This module predates the studies/messages/
   avatars/sources/knowledgebase/llm_assist vertical (which is otherwise 100% covered)
   and was never brought up to the same bar. Worth a follow-up pass mirroring
   `test_api_studies.py`'s / `test_api_avatars.py`'s structure.
3. **`embed_texts` is a stub**, so knowledgebase search's real embedding call and the
   pgvector cosine-distance query's behavior against a live index are both untested —
   `test_api_knowledgebase.py`'s happy-path test mocks `embed_texts` and exercises the
   ranking/shaping logic around it, not the vector math itself.
4. **`scripts/` are untested** (`seed_dev_data.py`, `get_dev_token.py`,
   `apply_schema.py`) — they are operational helpers, not application code.
5. **`app/llm/llm_call.py` is untested** — it is a CLI harness script, not application
   logic; it calls `call_llm` directly and would require a live LLM API.
6. **A known application bug is pinned, not fixed, by design:**
   `test_create_study_omitting_name_currently_raises_instead_of_422` in
   `test_api_studies.py` documents that `POST /studies` crashes unhandled (rather than
   a clean 422) when `name` is omitted — `StudyCreate.name` is optional on the wire but
   `core.studies.name` is `NOT NULL`, and nothing auto-generates one. Flip that test's
   expectation once `create_study` is fixed.
7. **Pre-existing deprecation warnings surface during runs** (17 warnings, up from 6 as
   the newer test files hit the same already-deprecated code paths more): FastAPI's
   deprecated `@app.on_event("startup")` in `app/main.py`, Starlette's deprecated
   `HTTP_422_UNPROCESSABLE_ENTITY`/`HTTP_413_REQUEST_ENTITY_TOO_LARGE` constants, and
   `starlette.testclient`'s deprecated reliance on `httpx`. They originate in
   application code (or the test/HTTP-client versions in use), not the tests
   themselves; migrating to a lifespan handler and the `..._CONTENT` constants would
   silence the first two.

