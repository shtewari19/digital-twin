"""Seed the fixed dev user the auth stub depends on, plus a few sample
domains so `GET /api/v1/domains` has something to return.

Run once against a fresh, migrated database:
    python scripts/seed_dev_data.py

Idempotent: every row uses a fixed UUID and `ON CONFLICT DO NOTHING`, so
re-running this script is a no-op after the first time.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.db.models.domain import Domain
from app.db.models.user import User
from app.db.session import SessionLocal

# The four predefined domains setup.sql leaves as a commented-out seed
# suggestion; kept here with fixed ids instead so re-running is a no-op.
_SEED_DOMAINS = [
    {
        "id": UUID("00000000-0000-0000-0000-0000000000d1"),
        "name": "Pharmaceutical Marketing",
        "type": "predefined",
        "description": "HCP & payer messaging",
    },
    {
        "id": UUID("00000000-0000-0000-0000-0000000000d2"),
        "name": "IT & Enterprise Software",
        "type": "predefined",
        "description": "IT buyer messaging",
    },
    {
        "id": UUID("00000000-0000-0000-0000-0000000000d3"),
        "name": "Financial Services",
        "type": "predefined",
        "description": "Consumer & SMB messaging",
    },
    {
        "id": UUID("00000000-0000-0000-0000-0000000000d4"),
        "name": "Consumer / CPG & Retail",
        "type": "predefined",
        "description": "Shopper messaging",
    },
]


async def main() -> None:
    async with SessionLocal() as session:
        await session.execute(
            pg_insert(User)
            .values(
                id=settings.dev_user_id,
                email="dev@example.com",
                name="Dev User",
                role="admin",
            )
            .on_conflict_do_nothing(index_elements=[User.id])
        )
        await session.execute(
            pg_insert(Domain).on_conflict_do_nothing(index_elements=[Domain.id]),
            _SEED_DOMAINS,
        )
        await session.commit()
    print(f"Seeded dev user {settings.dev_user_id} and {len(_SEED_DOMAINS)} domains.")


if __name__ == "__main__":
    asyncio.run(main())
