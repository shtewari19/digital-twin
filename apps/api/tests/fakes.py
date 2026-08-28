"""In-memory stand-ins for the external systems (DB, Temporal).

Deliberately minimal: each fake implements exactly the surface the
application touches, so a change in the app's usage shows up as a test
failure instead of being silently absorbed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any


def _server_default_value(column: Any) -> Any:
    """Best-effort Python value for a column's SQLAlchemy `server_default`.

    Only understands the literal forms this app's models actually use
    (`gen_random_uuid()`, `now()`, and bare string/int/bool literals) —
    good enough to emulate what a real `INSERT ... RETURNING` would hand
    back, not a general SQL-expression evaluator.
    """
    raw = column.server_default.arg
    text = str(getattr(raw, "text", raw)).strip("'\"")
    if text == "gen_random_uuid()":
        return uuid.uuid4()
    if "now()" in text:
        return datetime.now(tz=UTC)
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return text
    if python_type is bool:
        return text.lower() == "true"
    if python_type is int:
        return int(text)
    return text


def _python_default_value(column: Any) -> Any:
    """Best-effort Python value for a column's SQLAlchemy-side `default=`.

    Covers this app's two shapes: a scalar (`default=RunStatus.DRAFT`) and
    a zero-argument callable (`default=uuid.uuid4`).
    """
    default = column.default
    if default.is_scalar:
        return default.arg
    if default.is_callable:
        return default.arg({})
    return None


class FakeResult:
    """Stand-in for SQLAlchemy's `Result` (scalars().all(), scalar_one_or_none())."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any | None:
        return self._rows[0] if self._rows else None


class FakeAsyncSession:
    """Records every call and replays canned results.

    - `execute_rows`: returned by `execute(...).scalars().all()`
    - `get_result`:   returned by `get(model, pk)`
    """

    def __init__(
        self,
        execute_rows: list[Any] | None = None,
        get_result: Any | None = None,
        get_results: list[Any] | None = None,
        scalar_results: list[Any] | None = None,
        commit_error: Exception | None = None,
    ) -> None:
        self.execute_rows = execute_rows or []
        self.get_result = get_result
        # For routes that call `session.get(...)` more than once per request
        # with different expected results (e.g. "does the parent exist" then
        # "does the child exist"), pop these in call order; falls back to
        # `get_result` once exhausted (or if never set).
        self._get_results = list(get_results) if get_results is not None else None
        # Same idea for `session.scalar(...)` — e.g. a route issuing three
        # separate aggregate-count queries in sequence.
        self._scalar_results = list(scalar_results) if scalar_results is not None else None
        self.commit_error = commit_error
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.refreshed: list[Any] = []
        self.last_stmt: Any = None
        self.last_get: tuple[Any, Any] | None = None
        self.execute_calls: list[Any] = []

    async def execute(self, stmt: Any) -> FakeResult:
        self.last_stmt = stmt
        self.execute_calls.append(stmt)
        return FakeResult(self.execute_rows)

    async def scalar(self, stmt: Any) -> Any:
        self.last_stmt = stmt
        self.execute_calls.append(stmt)
        if self._scalar_results:
            return self._scalar_results.pop(0)
        return None

    async def get(self, model: Any, pk: Any) -> Any:
        self.last_get = (model, pk)
        if self._get_results is not None:
            if self._get_results:
                return self._get_results.pop(0)
            return self.get_result
        return self.get_result

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def commit(self) -> None:
        if self.commit_error is not None:
            error, self.commit_error = self.commit_error, None
            raise error
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def refresh(self, obj: Any) -> None:
        """Emulate the DB round-trip: any column whose value is still unset
        on `obj` gets filled from its `server_default` (id via
        gen_random_uuid(), created_at/updated_at via now(),
        status/priority/... literals) or, failing that, its Python-side
        `default=` (e.g. `Run.id`'s `default=uuid.uuid4`) — same as what a
        real `INSERT ... RETURNING` (or SQLAlchemy's pre-flush default
        application) would hand back.
        """
        table = getattr(type(obj), "__table__", None)
        if table is not None:
            for column in table.columns:
                if getattr(obj, column.name, None) is not None:
                    continue
                if column.server_default is not None:
                    setattr(obj, column.name, _server_default_value(column))
                elif column.default is not None:
                    setattr(obj, column.name, _python_default_value(column))
        self.refreshed.append(obj)


class FakeWorkflowHandle:
    """`result()` either returns or raises — all `_finalize_when_done` needs."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def result(self) -> None:
        if self._error is not None:
            raise self._error


class FakeTemporalClient:
    """Captures `start_workflow` calls; hands out configurable handles."""

    def __init__(self, handle_error: Exception | None = None) -> None:
        self.handle_error = handle_error
        self.started: list[dict[str, Any]] = []

    async def start_workflow(
        self,
        name: str,
        arg: Any,
        *,
        id: str,
        task_queue: str,
        **kwargs: Any,
    ) -> None:
        self.started.append({"name": name, "arg": arg, "id": id, "task_queue": task_queue})

    def get_workflow_handle(self, workflow_id: str) -> FakeWorkflowHandle:
        return FakeWorkflowHandle(self.handle_error)


class FakeSessionContext:
    """Replacement for `async with SessionLocal() as session:`."""

    def __init__(self, session: FakeAsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeAsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class RecordingTaskFactory:
    """Stands in for the `asyncio` module inside a module under test.

    `create_task` records (and closes) the coroutine instead of
    scheduling it, so tests stay deterministic and no "task was
    destroyed while pending" noise leaks from the TestClient loop.
    """

    def __init__(self) -> None:
        self.coroutines: list[Any] = []

    def create_task(self, coro: Any) -> None:
        coro.close()  # never awaited — close to avoid RuntimeWarning
        self.coroutines.append(coro)
