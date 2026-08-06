"""ORM models, one module per table.

Import every model here so `Base.metadata` is fully populated for anyone
who imports `app.db.models`.
"""

from app.db.models.domain import Domain
from app.db.models.user import User

__all__ = ["Domain", "User"]
