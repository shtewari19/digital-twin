"""Contract tests for GET /api/v1/domains (keyset-paginated listing).

The FakeAsyncSession replays a fixed row set, so the SQL-side keyset
filtering itself is out of scope here; what's covered is the HTTP
contract: response shape, the fetch-limit+1 has_more trick, cursor
round-tripping, and input validation.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fakes import FakeAsyncSession

from app.api.deps import get_db
from app.api.pagination import decode_cursor
from app.db.models.domain import Domain as DomainRow


def _domain_row(name: str, minutes_ago: int) -> DomainRow:
    created_at = datetime.now(tz=UTC) - timedelta(minutes=minutes_ago)
    return DomainRow(
        id=uuid4(),
        name=name,
        type="custom",
        description=None,
        compliance_profile="standard",
        created_at=created_at,
        updated_at=created_at,
    )


def _install_rows(app, rows: list[DomainRow]) -> list[DomainRow]:
    app.dependency_overrides[get_db] = lambda: FakeAsyncSession(execute_rows=list(rows))
    return rows


def test_list_domains_returns_rows_in_query_order(client, app, current_user):
    rows = _install_rows(
        app, [_domain_row("oldest", 30), _domain_row("middle", 20), _domain_row("newest", 10)]
    )

    response = client.get("/api/v1/domains")

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body["data"]] == [row.name for row in rows]
    assert body["has_more"] is False
    assert body["next_cursor"] is None


def test_list_domains_empty_table_yields_empty_page(client, app, current_user):
    _install_rows(app, [])

    body = client.get("/api/v1/domains").json()

    assert body == {"data": [], "next_cursor": None, "has_more": False}


def test_list_domains_signals_more_pages_with_next_cursor(client, app, current_user):
    rows = _install_rows(app, [_domain_row(f"domain-{i}", i) for i in range(3)])

    response = client.get("/api/v1/domains", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 2
    assert body["has_more"] is True

    cursor = decode_cursor(body["next_cursor"])
    assert cursor.id == rows[1].id
    assert cursor.created_at == rows[1].created_at


def test_list_domains_accepts_next_cursor_for_follow_up_page(client, app, current_user):
    _install_rows(app, [_domain_row(f"domain-{i}", i) for i in range(3)])

    first = client.get("/api/v1/domains", params={"limit": 2})
    cursor = first.json()["next_cursor"]

    second = client.get("/api/v1/domains", params={"limit": 2, "cursor": cursor})

    # The fake session replays the same row set (no real SQL filtering),
    # so only the HTTP contract of a well-formed follow-up page is asserted.
    assert second.status_code == 200
    body = second.json()
    assert len(body["data"]) == 2
    assert body["has_more"] is True


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor-at-all",
        base64.urlsafe_b64encode(b"no-separator").decode(),
        base64.urlsafe_b64encode(b"2026-01-01T00:00:00+00:00|not-a-uuid").decode(),
    ],
)
def test_list_domains_rejects_invalid_cursor_with_422(client, app, current_user, cursor):
    _install_rows(app, [])

    response = client.get("/api/v1/domains", params={"cursor": cursor})

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid pagination cursor."


def test_list_domains_validates_limit_bounds(client, app, current_user):
    _install_rows(app, [])

    too_small = client.get("/api/v1/domains", params={"limit": 0})
    too_large = client.get("/api/v1/domains", params={"limit": 101})

    assert too_small.status_code == 422
    assert too_large.status_code == 422
