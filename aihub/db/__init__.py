"""Canonical DB package exports.

The old top-level ``aihub/db.py`` runtime now lives at ``aihub.db.runtime`` to
remove the module/package collision. Public and selected legacy-private symbols
are delegated here so existing imports keep working without dynamic loading.
"""

from __future__ import annotations

from .runtime import *  # noqa: F401,F403
from . import runtime as _runtime

_db_backend = _runtime._db_backend
_DB_LOCK = _runtime._DB_LOCK
_conn = _runtime._conn


def __getattr__(name: str):
    return getattr(_runtime, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_runtime)))

__all__ = list(getattr(_runtime, "__all__", [])) or [
    name for name in dir(_runtime) if not name.startswith("_")
]
