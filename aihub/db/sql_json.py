"""Dialect-aware JSON field expressions for SQLite and PostgreSQL.

Use these helpers instead of raw ``json_extract`` / ``->>`` at call sites.
Column values may be stored as TEXT containing JSON (current goals.metadata).
"""

from __future__ import annotations

import os
import re


_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def db_backend() -> str:
    return (os.getenv("DB_BACKEND", "sqlite") or "sqlite").lower().strip()


def is_postgres() -> bool:
    return db_backend() == "postgres"


def _require_ident(name: str) -> str:
    if not _SAFE_IDENT.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def _require_key(key: str) -> str:
    if not _SAFE_KEY.match(key):
        raise ValueError(f"unsafe JSON key: {key!r}")
    return key


def json_text_path(column: str, key: str) -> str:
    """SQL expression returning a JSON object text field as TEXT/VARCHAR.

    ``key`` is a top-level object key (e.g. ``goal_fingerprint``).
    Works when ``column`` is TEXT-with-JSON or native JSON/JSONB.
    """
    col = _require_ident(column)
    k = _require_key(key)
    if is_postgres():
        # Cast TEXT → jsonb then extract; also fine if column is already jsonb.
        return f"(({col})::jsonb ->> '{k}')"
    return f"json_extract({col}, '$.{k}')"


def json_text_eq(column: str, key: str, *, placeholder: str = "?") -> str:
    """``<json text path> = <placeholder>`` fragment for WHERE clauses."""
    return f"{json_text_path(column, key)} = {placeholder}"
