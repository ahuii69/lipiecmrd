"""LEGACY / NOT THE CANONICAL DB LAYER — do not import into new code (06.07 repair sprint).

See ``aihub/db/database.py`` module docstring: the canonical DB layer is the ``aihub.db``
package (``aihub/db/runtime.py``). This module is used only by UNMOUNTED legacy routers
(``aihub/api/sse_router.py``, ``aihub/api/memory_router.py`` — see ``aihub/api/_LEGACY.md``).
"""

import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Union

from aihub.core.config import settings


def _use_postgres() -> bool:
    return (os.getenv("DB_BACKEND", "sqlite") or "sqlite").lower().strip() == "postgres"


def connect() -> sqlite3.Connection:
    os.makedirs(settings.data_dir, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def db() -> Iterator[Union[sqlite3.Connection, Any]]:
    """SQLite plik lub PgConnectionWrapper (gdy DB_BACKEND=postgres)."""
    if _use_postgres():
        from aihub.db import _DB_LOCK, _conn

        with _DB_LOCK, _conn() as c:
            yield c
        return
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init_schema() -> None:
    if _use_postgres():
        return
    with connect() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            id TEXT PRIMARY KEY,
            ts INTEGER NOT NULL,
            key TEXT,
            text TEXT NOT NULL,
            meta_json TEXT,
            access_count INTEGER NOT NULL DEFAULT 0,
            last_access_ts INTEGER NOT NULL DEFAULT 0,
            importance REAL NOT NULL DEFAULT 0.0,
            deleted INTEGER NOT NULL DEFAULT 0
        )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_memory_ts ON memory(ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_memory_key ON memory(key)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_memory_deleted ON memory(deleted)")

        # FTS5 for search
        c.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
        USING fts5(text, content='memory', content_rowid='rowid')
        """)
        c.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
            INSERT INTO memory_fts(rowid, text) VALUES (new.rowid, new.text);
        END
        """)
        c.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, text) VALUES('delete', old.rowid, old.text);
        END
        """)
        c.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, text) VALUES('delete', old.rowid, old.text);
            INSERT INTO memory_fts(rowid, text) VALUES (new.rowid, new.text);
        END
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS policy (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            confidence REAL NOT NULL,
            ts INTEGER NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            ts INTEGER NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            query TEXT,
            status INTEGER,
            latency_ms INTEGER,
            req_headers TEXT,
            req_body_b64 TEXT,
            resp_headers TEXT,
            resp_body_b64 TEXT,
            client_ip TEXT,
            user_agent TEXT,
            api_key_fp TEXT
        )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC)")
