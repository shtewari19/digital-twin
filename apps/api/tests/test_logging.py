"""Unit tests for the logging configuration (app/core/logging.py)."""

from __future__ import annotations

import sys


class TestConfigureLogging:
    def test_is_importable(self):
        from app.core.logging import configure_logging

        assert callable(configure_logging)

    def test_repo_root_on_sys_path(self):
        from app.core.logging import _repo_root

        assert _repo_root in sys.path
