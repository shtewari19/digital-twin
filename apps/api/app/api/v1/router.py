"""Aggregates every `/api/v1` resource router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import domains, me

router = APIRouter(prefix="/api/v1")
router.include_router(domains.router)
router.include_router(me.router)
