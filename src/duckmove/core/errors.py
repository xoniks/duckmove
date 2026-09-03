"""One error contract for every tool.

Tools return a JSON object rather than raising, so the model sees a
recoverable, machine-readable failure instead of a stack trace. Every tool
routes through :func:`guard`, so the contract holds uniformly — previously
about a third of the tools leaked raw tracebacks to the client.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Dict, Optional

import duckdb

from .engine import PathNotAllowed
from .sqlguard import SqlRejected

log = logging.getLogger(__name__)


class ToolError(Exception):
    """Raised by tool bodies to return a specific error code."""

    def __init__(self, message: str, code: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


def error(message: str, code: str, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"error": message, "error_code": code}
    payload.update({k: v for k, v in extra.items() if v is not None})
    return payload


def classify(exc: BaseException) -> Dict[str, Any]:
    """Map an exception to the `{error, error_code}` contract."""
    if isinstance(exc, ToolError):
        return error(str(exc), exc.code, **exc.extra)
    if isinstance(exc, SqlRejected):
        return error(str(exc), "SQL_REJECTED")
    if isinstance(exc, PathNotAllowed):
        return error(str(exc), "PATH_NOT_ALLOWED")
    if isinstance(exc, FileNotFoundError):
        return error(str(exc), "FILE_NOT_FOUND")
    if isinstance(exc, KeyError):
        # KeyError stringifies with quotes; unwrap for a readable message.
        msg = exc.args[0] if exc.args else str(exc)
        return error(str(msg), "NOT_FOUND")
    if isinstance(exc, PermissionError):
        return error(str(exc), "PERMISSION_DENIED")
    if isinstance(exc, duckdb.Error):
        return error(str(exc), "SQL_ERROR")
    if isinstance(exc, ValueError):
        return error(str(exc), "INVALID_INPUT")
    return error(f"{type(exc).__name__}: {exc}", "INTERNAL_ERROR")


#: Codes that represent the model or user getting something wrong. They are a
#: normal part of the loop — the tool explains and the model retries — so they
#: are logged as single lines without a traceback. Anything else is a bug in
#: this server and gets a full traceback.
EXPECTED_CODES = frozenset(
    {
        "SQL_REJECTED",
        "SQL_ERROR",
        "NOT_FOUND",
        "TABLE_NOT_FOUND",
        "FILE_NOT_FOUND",
        "UNSUPPORTED_FORMAT",
        "NAME_CONFLICT",
        "INVALID_INPUT",
        "INVALID_MAP_INPUT",
        "INVALID_GEOFENCE_SOURCE",
        "PATH_NOT_ALLOWED",
        "PERMISSION_DENIED",
    }
)


def _log_failure(tool_name: str, code: str, exc: BaseException) -> None:
    if code in EXPECTED_CODES:
        log.info("tool %s rejected: [%s] %s", tool_name, code, exc)
    else:
        log.error(
            "tool %s failed unexpectedly: [%s] %s",
            tool_name,
            code,
            exc,
            exc_info=True,
        )


def guard(
    fn: Optional[Callable[..., Any]] = None, *, hint: Optional[Callable[[], str]] = None
) -> Callable[..., Any]:
    """Wrap a tool body so any exception becomes an error object.

    `hint` supplies a lazily-computed recovery hint (e.g. the list of loaded
    tables) attached to failures.
    """

    def decorate(f: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return f(*args, **kwargs)
            except Exception as exc:
                payload = classify(exc)
                _log_failure(f.__name__, payload["error_code"], exc)
                if hint is not None and "hint" not in payload:
                    try:
                        payload["hint"] = hint()
                    except Exception:  # pragma: no cover - hint is best effort
                        log.debug("hint generation failed", exc_info=True)
                return payload

        return wrapper

    if fn is not None:
        return decorate(fn)
    return decorate
