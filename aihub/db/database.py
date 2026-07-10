"""LEGACY / NOT THE CANONICAL DB LAYER — do not import into new code (06.07 repair sprint).

The canonical SQLite/Postgres access layer for the active runtime is the ``aihub.db`` package
(``aihub/db/runtime.py``, imported as ``from aihub.db import ...``). This module defines a
**separate, parallel schema** (``memory_items``, ``embeddings``, ``psyche_state``, ``audit_log``
via ``aihub/db/schema.py``) used only by the LEGACY/UNWIRED packages ``aihub/memory/`` and
``aihub/psyche/`` (see their ``_LEGACY.md``).

Risk this header exists to prevent: ``from aihub.db.database import db`` looks superficially
similar to the canonical ``from aihub.db import db`` but reads/writes a **different SQLite
database schema** with no error raised — a silent wrong-DB bug. If you are writing new code,
you almost certainly want ``aihub.db`` (the package), not ``aihub.db.database`` (this module).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Optional, Tuple

from aihub.core.config import settings


def _is_postgres() -> bool:
    try:
        from aihub.db import _db_backend

        return _db_backend() == "postgres"
    except Exception:
        return False


_DB_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def db() -> sqlite3.Connection:
    if _is_postgres():
        raise RuntimeError(
            "legacy database.db() jest dostępne tylko przy DB_BACKEND=sqlite; "
            "przy PostgreSQL używaj warstwy aihub.db / legacy_ui."
        )
    global _CONN
    with _DB_LOCK:
        if _CONN is None:
            os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
            _CONN = _connect()
            init_schema(_CONN)
        return _CONN



def get_conn() -> sqlite3.Connection:
    """Return the process SQLite connection used by legacy database helpers.

    Kept for modules that historically imported ``get_conn`` from
    ``aihub.db.database``. PostgreSQL callers should use ``aihub.db`` runtime
    helpers instead of this SQLite-only compatibility accessor.
    """
    return db()

def now_ts() -> int:
    return int(time.time())


def init_schema(conn: sqlite3.Connection) -> None:
    if _is_postgres():
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit (
            id TEXT PRIMARY KEY,
            ts INTEGER NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            meta_json TEXT NOT NULL
        );

        -- memory core
        CREATE TABLE IF NOT EXISTS memory (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL, -- stm|ltm
            text TEXT NOT NULL,
            meta_json TEXT NOT NULL,
            importance REAL NOT NULL,
            created INTEGER NOT NULL,
            updated INTEGER NOT NULL,
            last_access INTEGER NOT NULL,
            access_count INTEGER NOT NULL,
            source TEXT NOT NULL
        );

        -- vector store
        CREATE TABLE IF NOT EXISTS memory_vec (
            memory_id TEXT PRIMARY KEY REFERENCES memory(id) ON DELETE CASCADE,
            dim INTEGER NOT NULL,
            vec BLOB NOT NULL
        );

        -- FTS index (contentless -> sync manually)
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            memory_id UNINDEXED,
            text,
            tokenize="porter"
        );

        CREATE INDEX IF NOT EXISTS idx_memory_kind_updated ON memory(kind, updated DESC);
        CREATE INDEX IF NOT EXISTS idx_memory_updated ON memory(updated DESC);
        CREATE INDEX IF NOT EXISTS idx_memory_importance ON memory(importance DESC);

        -- psyche state
        CREATE TABLE IF NOT EXISTS psyche_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            state_json TEXT NOT NULL,
            updated INTEGER NOT NULL
        );

        INSERT OR IGNORE INTO psyche_state (id, state_json, updated)
        VALUES (1, '{"mood":"neutral","goals":[],"beliefs":{},"traits":{},"last_reflection":0}', strftime('%s','now'));
        """
    )
    conn.commit()


def kv_get(key: str) -> Optional[str]:
    if _is_postgres():
        from aihub.db import fetch_one

        row = fetch_one("SELECT value FROM legacy_ui.kv WHERE key=?", (key,))
        return None if row is None else str(row["value"])
    c = db()
    row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def kv_set(key: str, value: str) -> None:
    if _is_postgres():
        from aihub.db import exec_one

        exec_one(
            """
            INSERT INTO legacy_ui.kv(key, value, updated) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = excluded.updated
            """,
            (key, value, now_ts()),
        )
        return
    c = db()
    c.execute(
        "INSERT OR REPLACE INTO kv(key,value,updated) VALUES(?,?,?)",
        (key, value, now_ts()),
    )
    c.commit()


def audit(actor: str, action: str, meta: Dict[str, Any]) -> None:
    import uuid

    aid = str(uuid.uuid4())
    payload = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    ts = now_ts()
    if _is_postgres():
        from aihub.db import exec_one

        exec_one(
            "INSERT INTO legacy_ui.audit(id,ts,actor,action,meta_json) VALUES(?,?,?,?,?)",
            (aid, ts, actor, action, payload),
        )
        return
    c = db()
    c.execute(
        "INSERT INTO audit(id,ts,actor,action,meta_json) VALUES(?,?,?,?,?)",
        (aid, ts, actor, action, payload),
    )
    c.commit()
