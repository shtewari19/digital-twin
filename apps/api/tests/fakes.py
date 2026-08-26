"""In-memory stand-ins for the external systems (DB, Temporal).

Deliberately minimal: each fake implements exactly the surface the
application touches, so a change in the app's usage shows up as a test
failure instead of being silently absorbed.
"""

from __future__ import annotations

import uuid
from typing import Any


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
    ) -> None:
        self.execute_rows = execute_rows or []
        self.get_result = get_result
        self.added: list[Any] = []
        self.commit_count = 0
        self.refreshed: list[Any] = []
        self.last_stmt: Any = None
        self.last_get: tuple[Any, Any] | None = None

    async def execute(self, stmt: Any) -> FakeResult:
        self.last_stmt = stmt
        return FakeResult(self.execute_rows)

    async def get(self, model: Any, pk: Any) -> Any:
        self.last_get = (model, pk)
        return self.get_result

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commit_count += 1

    async def refresh(self, obj: Any) -> None:
        # Emulate the DB round-trip: columns with server defaults
        # (e.g. users.id via gen_random_uuid()) come back populated.
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
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
        self, name: str, arg: Any, *, id: str, task_queue: str, **kwargs: Any
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
