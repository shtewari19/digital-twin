"""Shared asyncpg pool for the engine's DB-bound activities.

Raw asyncpg, not SQLAlchemy — activities only run a handful of hand-written
queries, so pulling the ORM in here would mean keeping model definitions in
sync across two codebases for no real benefit.
"""

import asyncpg

from app.config import settings

_pool: asyncpg.Pool | None = None


async def create_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(settings.asyncpg_dsn, min_size=2, max_size=10)
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call create_pool() first")
    return _pool