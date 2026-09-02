"""Unit tests for the database session module (app/db/session.py)."""

from __future__ import annotations

import contextlib

from app.db.session import engine, get_db


class TestEngine:
    def test_uses_asyncpg_driver(self):
        assert engine.url.drivername == "postgresql+asyncpg"


class TestGetDb:
    async def test_yields_session(self):
        gen = get_db()
        session = await gen.__anext__()
        assert session is not None
        with contextlib.suppress(StopAsyncIteration):
            await gen.__anext__()
