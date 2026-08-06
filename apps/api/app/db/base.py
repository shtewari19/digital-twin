"""The shared SQLAlchemy declarative base every ORM model inherits.

Only `core.users` and `core.domains` are modeled so far (see
`app/db/models/`) — enough for the walking skeleton's dev-user seed and
`GET /api/v1/domains`. The rest of `setup.sql`'s tables get a model each
as later verticals (studies, messages, avatars, ...) are built out.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every ORM model in `app.db.models`."""
