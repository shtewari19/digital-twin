"""Unit tests for the opaque cursor scheme (app/api/pagination.py)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.api.pagination import Cursor, InvalidCursorError, decode_cursor, encode_cursor


def test_roundtrip_preserves_keyset():
    created_at = datetime.now(tz=UTC)
    id_ = uuid4()

    decoded = decode_cursor(encode_cursor(created_at, id_))

    assert decoded == Cursor(created_at=created_at, id=id_)


def test_encode_is_deterministic_and_input_sensitive():
    created_at, id_ = datetime(2026, 1, 15, tzinfo=UTC), uuid4()

    assert encode_cursor(created_at, id_) == encode_cursor(created_at, id_)
    assert encode_cursor(created_at, id_) != encode_cursor(created_at + timedelta(days=1), id_)


def test_cursor_dataclass_is_frozen():
    cursor = Cursor(created_at=datetime.now(tz=UTC), id=uuid4())

    with pytest.raises(AttributeError):
        cursor.id = uuid4()


@pytest.mark.parametrize(
    "cursor",
    [
        "",  # empty string
        "not-base64!!!",  # binascii.Error
        base64.urlsafe_b64encode(b"no-separator").decode(),  # missing "|"
        base64.urlsafe_b64encode(b"2026-01-01T00:00:00+00:00|not-a-uuid").decode(),
        base64.urlsafe_b64encode(b"january|00000000-0000-0000-0000-000000000000").decode(),
    ],
)
def test_decode_rejects_malformed_cursors(cursor):
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor)


def test_invalid_cursor_error_is_a_value_error():
    # Callers in endpoints may catch either; keep the subclass contract.
    assert issubclass(InvalidCursorError, ValueError)
