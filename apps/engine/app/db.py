"""Shared asyncpg pool for the engine's DB-bound activities.

Raw asyncpg, not SQLAlchemy — activities only run a handful of hand-written
queries, so pulling the ORM in here would mean keeping model definitions in
sync across two codebases for no real benefit.
"""

import asyncpg

from app.config import settings


async def create_pool() -> asyncpg.Pool:
    """Create the pool. Called once, from worker.py, which then hands it to
    StudyDataActivities — there is no module-level accessor on purpose, so the
    pool's lifetime is owned by the process that created it."""
    return await asyncpg.create_pool(settings.asyncpg_dsn, min_size=2, max_size=10)