# API (`apps/api`)

A FastAPI backend for a domain-agnostic message-testing platform: operators
define a **domain** (market/audience context), spin up a **study** with
candidate **messages** and an **avatar** panel (AI personas — digital twins
of the target audience), run the study through an SSR pipeline, and get
back a ranked recommendation plus a qualitative report.

## Stack

- **API:** FastAPI + Pydantic v2 (async)
- **Auth:** Microsoft Entra ID (JWT via JWKS — signature, `iss`, `aud`, `exp`)
- **Database:** PostgreSQL 16 + pgvector, via SQLAlchemy 2.0 (asyncpg)
- **Cache / rate limiting:** Redis

## Requirements

- Python 3.14+
- Docker Desktop (for Postgres + Redis — see the repo-root
  [`docker-compose.yml`](../../docker-compose.yml))
- An Entra ID app registration (tenant id + client id)

## Setup

Run from `apps/api/` (this directory):

```bash
# 1. Configure environment
cp .env.example .env
# Fill APP_ENTRA_TENANT_ID and APP_ENTRA_CLIENT_ID (see Entra section below)

# 2. Start Postgres (pgvector) + Redis — from the repo root
(cd ../.. && docker compose up -d)

# 3. Install dependencies
uv sync

# 4. Create the schema and seed dev data
python scripts/apply_schema.py
python scripts/seed_dev_data.py

# 5. Run the API
uvicorn app.main:app --reload
```

## Verify it's working

```bash
curl http://localhost:8000/health
```

Protected routes need a Bearer token (see below):

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/me
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/domains
```

Interactive API docs (Authorize with Bearer JWT): `http://localhost:8000/docs`.

## Project layout

```
app/
  main.py              FastAPI entrypoint, /health, logging init
  core/
    config.py          Settings (env-driven), including Entra IDs
    auth.py            JWKS fetch + JWT validation (iss/aud/exp/signature)
    logging.py         Re-export of utility.logging for apps/api
  db/
    models/            ORM models (users, domains so far)
  api/
    deps.py            DB session + get_current_user (JWT + JIT provision)
    v1/
      router.py
      me.py            GET /me
      domains.py       GET /domains
  schemas/             Pydantic request/response models
scripts/
  apply_schema.py
  seed_dev_data.py
  setup.sql
  get_dev_token.py     Device-code helper for local /me testing
pyproject.toml
```

## Setup Prism
Requires **Node 22**. Run the commands below to set up, launch, and test the mock server:
```bash
# Set Node version
nvm install 22 && nvm use 22

# Install Prism CLI
npm install --save-dev @stoplight/prism-cli@latest

# Start mock API & Prism server
npm run mock:api
npx prism mock openapi-test.yaml -p 4010

# Test endpoint
curl http://localhost:4010/api/v1/domains
```

Shared monorepo code lives in [`../../utility`](../../utility) (logging today).

## Microsoft Entra ID (Azure AD) — local setup

Full Azure Portal + SSO walkthrough (Digital Twin only):

**→ [`docs/ENTRA_SSO_SETUP.md`](docs/ENTRA_SSO_SETUP.md)**

Short version:

1. Create Entra app registration (SPA redirect for the frontend).
2. Put `APP_ENTRA_TENANT_ID` and `APP_ENTRA_CLIENT_ID` in `.env`.
3. Expose an API scope for FE access tokens (`api://<client-id>/access_as_user`).
4. Run the API; get a user token with `python scripts/get_dev_token.py`.
5. `GET /api/v1/me` with `Authorization: Bearer <token>` — first call JIT-creates `core.users`.

Never commit secrets. Client **secret is not required** for API JWT validation
(JWKS). Client-credentials Graph tokens are **not** valid for `/me`.

### `GET /api/v1/me`

| | |
|---|---|
| Method | `GET` |
| Path | `/api/v1/me` |
| Headers | `Authorization: Bearer <jwt>` |
| Body | none |

**200**

```json
{"id": "...", "name": "Your Name", "email": "you@example.com", "role": "operator"}
```

**401** — missing or invalid credentials.

### How JWT validation works

- **`app/core/auth.py`** — JWKS from Entra, verify RS256 + `iss` / `aud` / `exp`
- **`app/api/deps.py`** — resolve `CurrentUser`; create/update `core.users`

## Notes on current design decisions

**Auth is Entra ID JWT.** The old `APP_DEV_USER_ID` stub is unused by the live
auth path; it remains only for seed scripts.

**Schema management has no migration tool yet.** `scripts/apply_schema.py`
applies `setup.sql` directly via `asyncpg`. Re-running against an already-
schema'd database will fail — drop/recreate to reset until Alembic lands.

**`/api/v1` prefix.** Matches the API spec; leaves room for a `v2` later.

**Only `User` and `Domain` are modeled as SQLAlchemy ORM classes** so far.

See the [repo-root README](../../README.md) for monorepo layout, branch naming,
and CI.
