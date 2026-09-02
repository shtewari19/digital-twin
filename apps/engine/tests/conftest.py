"""Pytest configuration for the Engine test suite.

The engine imports `app.worker` at module level, which triggers `load_dotenv`
and reads `APP_TEMPORAL_HOST` / `APP_TEMPORAL_NAMESPACE` / `APP_TASK_QUEUE`.
Those environment variables must exist BEFORE any `app.*` import — that is
why this module sets the environment first and imports the application second
(E402 is suppressed for this file in pyproject.toml).

The suite is hermetic: no Temporal server access.  Temporal client and
worker creation are replaced by in-memory fakes (tests/fakes.py).
"""

from __future__ import annotations

import os

import pytest

from tests.fakes import FakeTemporalClient

_TEST_ENV = {
    "APP_TEMPORAL_HOST": "localhost:7233",
    "APP_TEMPORAL_NAMESPACE": "default",
    "APP_TASK_QUEUE": "study-runs-test",
}
for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)


@pytest.fixture(autouse=True)
def _isolate_test_state():
    """Wipe class-level fake state between tests."""
    FakeTemporalClient.connect_calls = []
    yield
    FakeTemporalClient.connect_calls = []
