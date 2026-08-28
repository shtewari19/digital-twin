"""The shared SQLAlchemy declarative base every ORM model inherits.

Every table in setup.sql has a matching model here now except
`platform.*` (provider keys, jobs, credit ledger, usage events, audit
log) — none of that vertical is built yet.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every ORM model in `app.db.models`."""
