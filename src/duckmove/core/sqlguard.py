"""Read-only SQL policy for everything the model is free to write.

The trust boundary: `Engine.create_table_from_file` is the *only* code path
that touches the filesystem, and it passes the path as a bound parameter, so
it never reaches this module. Every query the model composes itself
(`run_sql`, map `table_or_sql`, saved queries) goes through `validate_sql`,
which therefore forbids filesystem and network access outright.

Checks run against a copy of the SQL with string literals and quoted
identifiers masked out, so a payload hidden inside a literal can neither
trigger a false rejection nor slip a keyword past the scan.
"""

from __future__ import annotations

import re
from typing import List, Tuple

ALLOWED_FIRST = frozenset(
    {"select", "with", "describe", "summarize", "show", "explain", "pivot", "unpivot"}
)

#: Statement keywords that cannot legitimately appear anywhere in a read-only
#: query. Checked across the whole statement, not just the first word, so they
#: cannot hide inside a subquery or CTE.
FORBIDDEN_KEYWORDS = frozenset(
    {
        "alter",
        "attach",
        "begin",
        "call",
        "checkpoint",
        "commit",
        "copy",
        "create",
        "delete",
        "detach",
        "drop",
        "execute",
        "export",
        "grant",
        "import",
        "insert",
        "install",
        "load",
        "merge",
        "pragma",
        "prepare",
        "reset",
        "revoke",
        "rollback",
        "set",
        "truncate",
        "update",
        "vacuum",
    }
)

#: Table/scalar functions that read from disk, the network, or the
#: environment. `read_*` is handled by prefix so new reader variants
#: (read_ndjson, read_xlsx, ...) are covered without a code change.
FORBIDDEN_FUNCTION_PREFIXES = (
    "read_",
    "st_read",
    "iceberg_",
    "delta_",
    "postgres_",
    "mysql_",
    "sqlite_",
    "azure_",
    "shapefile_",
)

FORBIDDEN_FUNCTIONS = frozenset(
    {
        "glob",
        "getenv",
        "sniff_csv",
        "parquet_scan",
        "parquet_metadata",
        "parquet_schema",
        "parquet_file_metadata",
        "parquet_kv_metadata",
        "load_extension",
        "load_aws_credentials",
        "gdal_drivers",
        "duckdb_extensions",
        "which_secret",
        "arrow_scan",
        "scan_arrow_ipc",
    }
)

# String literals and quoted identifiers are masked with *different*
# sentinels: `FROM 'file.csv'` is a file read to reject, while
# `FROM "my table"` is an ordinary quoted table name to allow.
_STR_SENTINEL = "\x00S{}\x00"
_IDENT_SENTINEL = "\x00I{}\x00"
_STRING_RE = re.compile(r"'(?:[^']|'')*'|\$\$.*?\$\$", re.S)
_IDENT_RE = re.compile(r'"(?:[^"]|"")*"|`[^`]*`', re.S)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_CALL_RE = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)\s*\(")
_FROM_LITERAL_RE = re.compile(r"\b(?:from|join)\s+\x00S\d+\x00", re.I)


class SqlRejected(ValueError):
    """The query violates the read-only / no-filesystem policy."""


def _strip_comments(sql: str) -> str:
    sql = _LINE_COMMENT_RE.sub(" ", sql)
    sql = _BLOCK_COMMENT_RE.sub(" ", sql)
    return sql.strip()


def _mask_literals(sql: str) -> Tuple[str, List[str]]:
    """Replace string literals and quoted identifiers with sentinels.

    Returns the masked SQL and the captured spans in order. Masking first
    means a ';' or a keyword hidden inside a literal is invisible to the
    policy checks below, so it can neither bypass nor falsely trip them.
    """
    spans: List[str] = []

    def mask(pattern: re.Pattern[str], sentinel: str, text: str) -> str:
        def take(m: re.Match[str]) -> str:
            spans.append(m.group(0))
            return sentinel.format(len(spans) - 1)

        return pattern.sub(take, text)

    # Strings first: a lone double quote inside a string literal must not be
    # mistaken for the start of a quoted identifier.
    masked = mask(_STRING_RE, _STR_SENTINEL, sql)
    masked = mask(_IDENT_RE, _IDENT_SENTINEL, masked)
    return masked, spans


_SENTINEL_RE = re.compile(r"\x00[SI](\d+)\x00")


def _unmask(masked: str, spans: List[str]) -> str:
    """Inverse of :func:`_mask_literals`."""
    return _SENTINEL_RE.sub(lambda m: spans[int(m.group(1))], masked)


def _forbidden_function(name: str) -> bool:
    low = name.lower()
    if low in FORBIDDEN_FUNCTIONS:
        return True
    return any(low.startswith(p) for p in FORBIDDEN_FUNCTION_PREFIXES)


def validate_sql(sql: str) -> str:
    """Return the cleaned single-statement SQL, or raise :class:`SqlRejected`.

    Rejects anything that is not a read-only query, and anything that reaches
    outside the database: file readers, remote scanners, extension loading and
    environment access. Use ``load_data`` to bring files in.
    """
    # Mask *before* stripping comments: a '--' inside a string literal starts
    # no comment, and a literal's contents must not be scanned as SQL.
    masked, spans = _mask_literals(sql)
    masked = _strip_comments(masked)
    if not masked:
        raise SqlRejected("Empty SQL")
    while masked.endswith(";"):
        masked = masked[:-1].rstrip()
    if ";" in masked:
        raise SqlRejected(
            "Multiple statements are not allowed; send one query at a time."
        )

    # No leading keyword at all (e.g. a statement starting with '(' or a
    # symbol) is rejected the same as a disallowed one: fail closed.
    head = _WORD_RE.match(masked.lstrip())
    if head is None:
        raise SqlRejected(
            f"Query must start with one of "
            f"{', '.join(sorted(ALLOWED_FIRST))}. Use load_data to create tables."
        )
    first = head.group(0).lower()
    if first not in ALLOWED_FIRST:
        raise SqlRejected(
            f"Only read-only queries are allowed "
            f"({', '.join(sorted(ALLOWED_FIRST))}). Got: {first.upper()}. "
            f"Use load_data to create tables."
        )

    for word in _WORD_RE.findall(masked):
        low = word.lower()
        if low in FORBIDDEN_KEYWORDS:
            raise SqlRejected(
                f"'{low.upper()}' is not allowed in a read-only query. "
                f"Use load_data to create or modify tables."
            )

    for name in _CALL_RE.findall(masked):
        if _forbidden_function(name):
            raise SqlRejected(
                f"'{name}' reads from outside the database, which is not "
                f"allowed here. Use load_data to load a file, then query the "
                f"resulting table."
            )

    if _FROM_LITERAL_RE.search(masked):
        raise SqlRejected(
            "Querying a file path directly is not allowed. Use load_data to "
            "load the file, then query the resulting table by name."
        )

    return _unmask(masked, spans)
