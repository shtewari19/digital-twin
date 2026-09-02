# Engine (`apps/engine`)

**Status: not implemented yet.** This is a placeholder — `engine/__init__.py`
is the only file. Nothing here runs today.

## What this app will own

Per the architecture doc, the engine workers execute the SSR pipeline:

```
reaction -> embed -> cosine -> shift -> normalize -> expected value -> penalty
```

...then compute the Bradley-Terry ranking across a study's messages and drive
the report-synthesis call. Workers are stateless and horizontally scalable;
Temporal owns sequencing, retries, and the results-gate pause — the workers
just execute one step when signaled.

## Why it's a separate app from `apps/api`

The API is a request/response FastAPI service; the engine is a pool of
workers reacting to a workflow orchestrator (Temporal), with a completely
different runtime shape (no HTTP server, long-running processes, its own
scaling story). They'll likely share some Pydantic schemas with `apps/api`
(`Run`, `AvatarReaction`, `RankingEntry`, ...) — that's an open question:
either a small shared package once the duplication actually hurts, or the
engine imports `apps/api`'s schemas directly if they end up in the same
Python dependency graph. Not decided yet; don't build either until the first
real pipeline step needs it.

## Local setup (once there's real code)

```bash
cd apps/engine
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`pyproject.toml` has no runtime dependencies declared yet — add them as the
first real pipeline step gets built (embeddings client, pgvector access via
the same Postgres `docker-compose.yml` runs at the repo root, etc.).

See the [repo-root README](../../README.md) for how this app fits into the
rest of the monorepo, branch naming, and CI.
