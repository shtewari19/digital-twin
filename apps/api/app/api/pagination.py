"""Cursor-pagination helpers shared by every list endpoint.

Matches the contract's convention: callers pass `limit` + `cursor`,
responses return `next_cursor` + `has_more` (see
`app.schemas.common.Page`). Every list endpoint orders its query by
`created_at DESC, id DESC` and encodes that pair as an opaque, base64
cursor — reuse `encode_cursor`/`decode_cursor` rather than re-deriving a
scheme per endpoint.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class InvalidCursorError(ValueError):
    """Raised when a client-supplied cursor can't be decoded."""


@dataclass(frozen=True)
class Cursor:
    """The `(created_at, id)` keyset a page boundary resumes from."""

    created_at: datetime
    id: UUID


def encode_cursor(created_at: datetime, id_: UUID) -> str:
    """Opaque-encode a `(created_at, id)` keyset as a pagination cursor."""
    raw = f"{created_at.isoformat()}|{id_}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> Cursor:
    """Decode a cursor produced by `encode_cursor`.

    Raises:
        InvalidCursorError: if `cursor` isn't well-formed.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        created_at_str, id_str = raw.split("|", 1)
        return Cursor(created_at=datetime.fromisoformat(created_at_str), id=UUID(id_str))
    except (ValueError, binascii.Error) as exc:
        raise InvalidCursorError("Invalid pagination cursor.") from exc
