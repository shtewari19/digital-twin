"""Apply setup.sql directly to the database.

Schema versioning (Alembic or similar) can come back later once there's
an actual second version of the schema to justify it; for now this is
the whole migration story. Run once against a fresh database, before
scripts/seed_dev_data.py:

    python scripts/apply_schema.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg

from app.core.config import settings

_SETUP_SQL_PATH = Path(__file__).resolve().parent / "setup.sql"

# Persists the search_path for anyone connecting directly (psql, admin
# tools); the app itself doesn't need it since every ORM model is
# schema-qualified. Uses current_database() so it isn't tied to one
# hardcoded database name. setup.sql leaves this as a commented-out
# suggestion; applied here instead of left as a manual step.
_PERSIST_SEARCH_PATH_SQL = """
DO $$
BEGIN
    EXECUTE format(
        'ALTER DATABASE %I SET search_path = core, runs, platform, public',
        current_database()
    );
END $$;
"""


async def main() -> None:
    sql = _SETUP_SQL_PATH.read_text(encoding="utf-8")
    conn = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )
    try:
        # No arguments -> asyncpg runs this over the simple query protocol,
        # which (unlike its usual prepared-statement path) allows multiple
        # semicolon-separated statements in one call.
        await conn.execute(sql)
        await conn.execute(_PERSIST_SEARCH_PATH_SQL)
    finally:
        await conn.close()
    print(f"Applied {_SETUP_SQL_PATH.name}.")


if __name__ == "__main__":
    asyncio.run(main())
