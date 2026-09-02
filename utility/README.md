# Shared utilities (`utility/`)

Code shared by `apps/api` and (eventually) `apps/engine`.

## Modules

| Module | Purpose |
|--------|---------|
| `utility.logging` | Process-wide logging setup (`configure_logging`) |

## Usage

From `apps/api` (uvicorn cwd), the API adds the monorepo root to `sys.path`
via `app.core.logging` and re-exports:

```python
from app.core.logging import configure_logging

configure_logging()  # called once in app.main
```

Environment:

- `LOG_LEVEL` — default `INFO`
- `LOG_JSON` — set to `true` for JSON lines (prod aggregators)
