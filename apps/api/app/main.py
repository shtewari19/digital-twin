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

app = FastAPI(title="Core API", version="0.1.0")
app.include_router(v1_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness probe. Deliberately doesn't touch the database."""
    return {"status": "ok"}
