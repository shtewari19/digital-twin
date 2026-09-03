# Digital Twin

A domain-agnostic message-testing platform: operators define a **domain**
(market/audience context), spin up a **study** with candidate **messages**
and an **avatar** panel (AI personas — digital twins of the target
audience), run the study through an SSR pipeline, and get back a ranked
recommendation plus a qualitative report.

This is a **monorepo**: the frontend, backend API, and pipeline workers live
in one repo under `apps/`, rather than three separate repos. Why: the three
services are tightly coupled through one contract (`apps/web` and
`apps/engine` both depend on `apps/api`'s schemas and the database schema),
and a single schema/contract change often needs coordinated updates across
more than one of them. Splitting repos would turn each of those into
multiple PRs that have to land in the right order, plus duplicated CI/tooling
per repo. Folder boundaries + CODEOWNERS + path-scoped CI (below) get the
"don't collide" property without that coordination tax. Revisit this if
these ever become independent products with separate release cadences.

## Where each team works

| App | Path | Stack | Status | Docs |
|---|---|---|---|---|
| Web | [`apps/web`](apps/web) | Next.js (App Router) + TypeScript | Not scaffolded yet | [apps/web/README.md](apps/web/README.md) |
| API | [`apps/api`](apps/api) | FastAPI + SQLAlchemy (async) + Postgres/pgvector | Walking skeleton — one real endpoint | [apps/api/README.md](apps/api/README.md) |
| Engine | [`apps/engine`](apps/engine) | Python workers (Temporal-orchestrated) | Not implemented yet | [apps/engine/README.md](apps/engine/README.md) |

Each app is self-contained: its own dependency manifest
(`apps/api/pyproject.toml`, `apps/engine/pyproject.toml`,
`apps/web/package.json`), its own README with local setup instructions, and
its own CI job scoped to its own path (see CI below). Don't reach into
another app's folder to fix something in it — that's what its README and
CODEOWNERS entry are for.

## Shared, root-level things

- [`docker-compose.yml`](docker-compose.yml) — Postgres (pgvector) + Redis,
  shared local dev infrastructure both `apps/api` and (eventually)
  `apps/engine` connect to. Run `docker compose up -d` from the repo root.
- `.github/` — CI, PR template, CODEOWNERS, Dependabot (all below).

## Contributing

**Branch naming.** GitHub doesn't natively enforce branch-name prefixes, so
this is convention rather than a hard gate — branch off `main` using one of:

- `feature/short-description` — new features
- `fix/short-description` — bug fixes
- `chore/short-description` — maintenance, dependencies, refactoring

**Pull requests** auto-populate from
[`.github/pull_request_template.md`](.github/pull_request_template.md).

**CI** is split per app so editing one app doesn't trigger another's checks:
[`.github/workflows/ci-api.yml`](.github/workflows/ci-api.yml) and
[`.github/workflows/ci-engine.yml`](.github/workflows/ci-engine.yml) each run
`ruff check` scoped to their own `apps/*` path. There's no `ci-web.yml` yet —
add one once `apps/web` has a real Next.js app to lint/build.

**Dependency updates** ([`.github/dependabot.yml`](.github/dependabot.yml))
opens weekly PRs per app (`apps/api`, `apps/engine` on uv; `apps/web` on
npm) plus the GitHub Actions used in CI.

**Code ownership** ([`.github/CODEOWNERS`](.github/CODEOWNERS)) currently
assigns `@shtewari19` as the global fallback reviewer — split it per
`apps/*` path once there are GitHub handles for each team to assign.

## Temporal

Temporal is used to orchestrate study runs asynchronously. The API submits a study run to Temporal, and the Python worker listens on the `study-runs` task queue and executes the `study_run_workflow`. For local development, Temporal Server and the Temporal UI are started through Docker Compose, with the server available at `localhost:7233` and the UI at `http://localhost:8080`. The Temporal host, namespace, and task queue are configured through the `.env` file using `APP_TEMPORAL_HOST`, `APP_TEMPORAL_NAMESPACE`, and `APP_TASK_QUEUE`. The worker can be started with `python -m app.worker` from `apps/engine`. The current workflow is a no-op workflow used to verify the complete API → Temporal → Worker flow.