"""ORM models, one module per table.

Import every model here so `Base.metadata` is fully populated for anyone
who imports `app.db.models`.
"""

from app.db.models.anchor import Anchor
from app.db.models.avatar import Avatar, StudyAvatar
from app.db.models.chunk import SourceChunk
from app.db.models.domain import Domain
from app.db.models.message import Message
from app.db.models.run import Run, RunStatus
from app.db.models.source import Source
from app.db.models.study import Study
from app.db.models.user import User

__all__ = [
    "Anchor",
    "Avatar",
    "Domain",
    "Message",
    "Run",
    "RunStatus",
    "Source",
    "SourceChunk",
    "Study",
    "StudyAvatar",
    "User",
]
