"""Aggregates every `/api/v1` resource router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import avatars, domains, knowledgebase, me, messages, runs, sources, studies

router = APIRouter(prefix="/api/v1")
router.include_router(domains.router, tags=["domains"])
router.include_router(studies.router, tags=["studies"])
router.include_router(messages.router, tags=["messages"])
router.include_router(avatars.router, tags=["avatars"])
router.include_router(sources.router, tags=["sources"])
router.include_router(knowledgebase.router, tags=["knowledgebase"])
router.include_router(runs.router, tags=["runs"])
router.include_router(me.router, tags=["me"])
