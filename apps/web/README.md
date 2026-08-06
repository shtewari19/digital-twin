# Web (`apps/web`)

**Status: not scaffolded yet.** `package.json` is a placeholder — there's no
Next.js app here yet.

## What this app will own

Per the architecture doc: the operator-facing UI (domain, study, docs,
messages, personas, estimate, review gates, results, export screens) and a
thin BFF for auth-aware calls to `apps/api`. Also hosts the LLM-assisted
interactive UX (study naming, doc summaries, persona elaboration, estimate
advice) via streaming.

Recommended stack: Next.js (App Router) + TypeScript + Tailwind + shadcn/ui,
Vercel AI SDK for the streaming AI UX.

## Getting started

Nobody has run `create-next-app` here yet — do that first:

```bash
cd apps/web
npx create-next-app@latest .
```

Point it at the API during local dev — `apps/api` runs at
`http://localhost:8000`, with the actual API under `/api/v1` (see
[`apps/api/README.md`](../api/README.md) to get that running first).

See the [repo-root README](../../README.md) for how this app fits into the
rest of the monorepo, branch naming, and CI.
