"""Logging setup.

Critical for a stdio MCP server: stdout carries the JSON-RPC stream, so every
handler must write to stderr. Anything printed to stdout corrupts the
protocol and the client drops the connection.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

LEVEL_ENV = "DUCKMOVE_LOG_LEVEL"
DEFAULT_LEVEL = "WARNING"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: Optional[str] = None) -> None:
    """Attach a stderr handler to the `duckmove` logger. Idempotent."""
    resolved = (level or os.environ.get(LEVEL_ENV) or DEFAULT_LEVEL).upper()
    root = logging.getLogger("duckmove")
    root.setLevel(getattr(logging, resolved, logging.WARNING))
    if not any(
        isinstance(h, logging.StreamHandler) and h.stream is sys.stderr
        for h in root.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    # Never let records reach the root logger, which may log to stdout.
    root.propagate = False
