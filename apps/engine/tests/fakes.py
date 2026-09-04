"""In-memory stand-ins for the Temporal client and worker.

Deliberately minimal: each fake implements exactly the surface the
application touches, so a change in the app's usage shows up as a test
failure instead of being silently absorbed.
"""

from __future__ import annotations

from typing import Any, ClassVar


class FakeTemporalClient:
    """Stands in for `temporalio.client.Client`.

    The real `Client.connect` is a classmethod; `app/worker.main()` reaches it
    as `Client.connect(...)`, so the fake records every call at the class level
    (fresh instances are created per call) and answers with a bare stub.
    """

    connect_calls: ClassVar[list[dict[str, Any]]] = []

    @classmethod
    async def connect(cls, host: str, namespace: str | None = None) -> FakeTemporalClient:
        cls.connect_calls.append({"host": host, "namespace": namespace})
        return cls()


class FakeWorker:
    """Records construction args; `run()` is a no-op."""

    def __init__(
        self,
        client: Any,
        *,
        task_queue: str,
        workflows: list[Any] | None = None,
    ) -> None:
        self.client = client
        self.task_queue = task_queue
        self.workflows = list(workflows) if workflows else []
        self._ran = False

    async def run(self) -> None:
        self._ran = True
