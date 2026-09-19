"""FastAPI application entrypoint.

Run locally with:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1.router import router as v1_router
from app.core.logging import configure_logging

# Initialise logging before any module-level logger calls fire.
configure_logging()
from app.core.problem import install_problem_handlers
from app.core.temporal import init_temporal_client

app = FastAPI(title="Core API", version="0.1.0")

# Must run before the router is included so our handlers, not FastAPI's
# defaults, render every non-2xx as RFC 7807 application/problem+json.
install_problem_handlers(app)
app.include_router(v1_router)


@app.on_event("startup")
async def startup() -> None:
    await init_temporal_client()


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness probe. Deliberately doesn't touch the database."""
    return {"status": "ok"}