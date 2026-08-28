"""The async SQLAlchemy engine and per-request session dependency.

All queries go over asyncpg (`Settings.async_database_url`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from pgvector.asyncpg import register_vector
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.async_database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _register_pgvector_codec(dbapi_connection, connection_record) -> None:
    """Teach each new asyncpg connection how to (de)serialize `vector`.

    Required for `core.source_chunks.embedding` (pgvector's SQLAlchemy
    `Vector` type) — without this, asyncpg doesn't know the wire format
    for Postgres's custom `vector` type and raises on any read/write of
    that column.
    """
    dbapi_connection.run_async(register_vector)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped `AsyncSession`."""
    async with SessionLocal() as session:
        yield session
