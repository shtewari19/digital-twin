"""Thin shim that delegates to the shared utility logging module.

Kept inside apps/api so it can be imported without PYTHONPATH tricks from
within the api app, while the real implementation lives in /utility.
"""

from __future__ import annotations

import sys
import os

# Allow importing from the monorepo root /utility package when running
# uvicorn from apps/api/ (the default local-dev pattern).
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from utility.logging import configure_logging  # noqa: E402  re-export

__all__ = ["configure_logging"]
