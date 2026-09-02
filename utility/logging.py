"""Shared structured logging setup.

Both apps/api and apps/engine import and call `configure_logging()` once at
startup.  After that, every module obtains its own logger with the standard
`logging.getLogger(__name__)` call.

Usage
-----
In your app's entry-point (e.g. apps/api/app/main.py):

    from utility.logging import configure_logging
    configure_logging()

Environment control
-------------------
Set LOG_LEVEL (default INFO) and LOG_JSON=true (default false) in .env.
JSON output is intended for production log aggregators (CloudWatch, Datadog).
Plain text is friendlier for local development.
"""

from __future__ import annotations

import logging
import os
import sys


def configure_logging(
    level: str | None = None,
    json_logs: bool | None = None,
) -> None:
    """Configure the root logger for the process.

    Parameters
    ----------
    level:
        Override log level (e.g. "DEBUG"). Falls back to the LOG_LEVEL env var,
        then INFO.
    json_logs:
        If True, emit JSON lines.  Falls back to the LOG_JSON env var ("true").
    """
    resolved_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    resolved_json = json_logs if json_logs is not None else (
        os.getenv("LOG_JSON", "false").lower() == "true"
    )

    if resolved_json:
        # Minimal JSON formatter — swap for structlog/python-json-logger in prod
        fmt = (
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    else:
        fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"

    logging.basicConfig(
        level=resolved_level,
        format=fmt,
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
        force=True,  # override any handlers set before this call
    )

    # Quiet down noisy third-party loggers in non-debug mode
    if resolved_level != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
