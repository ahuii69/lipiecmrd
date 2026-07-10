#!/usr/bin/env python3
"""Jedna warstwa dla sidecarów (events / psyche rules / anomalies / self-heal): SQLite lub PostgreSQL (schemat ``sidecar``)."""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from aihub.config import DATA_DIR

from aihub.db import _db_backend, exec_one, fetch_all, fetch_one

# ── ścieżki plików SQLite (tylko gdy DB_BACKEND=sqlite) ─────────────────────
EVENTS_DB_PATH = (DATA_DIR / "events.db").resolve()
PSYCHE_DB_PATH = (DATA_DIR / "psyche.db").resolve()


def is_postgres() -> bool:
    return _db_backend() == "postgres"


def _pg_exec(sql: str, params: Sequence[Any] = ()) -> None:
    exec_one(sql, tuple(params))


def _pg_fetch_all(sql: str, params: Sequence[Any] = ()) -> list[Any]:
    return fetch_all(sql, tuple(params))


def _pg_fetch_one(sql: str, params: Sequence[Any] = ()) -> Any:
    return fetch_one(sql, tuple(params))


# ── HTTP events (middleware / EventsStore) ────────────────────────────────────


def ensure_http_events_schema_sqlite() -> None:
    if is_postgres():
        return
    EVENTS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(EVENTS_DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events(
                id TEXT PRIMARY KEY,
                ts INTEGER NOT NULL,
                method TEXT,
                path TEXT,
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
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_path ON events(path);")
        conn.commit()


def http_events_insert_row(row: Sequence[Any]) -> None:
    if is_postgres():
        _pg_exec(
            """
            INSERT INTO sidecar.http_events(
                id, ts, method, path, query, status, latency_ms,
                req_headers, req_body_b64, resp_headers, resp_body_b64,
                client_ip, user_agent, api_key_fp
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            row,
        )
        return
    ensure_http_events_schema_sqlite()
    with sqlite3.connect(str(EVENTS_DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            row,
        )
        conn.commit()


def http_events_select_recent_sqlite(limit: int) -> list[Any]:
    if not EVENTS_DB_PATH.exists():
        return []
    with sqlite3.connect(str(EVENTS_DB_PATH)) as conn:
        return conn.execute(
            """
            SELECT method, path, status, ts FROM events
            ORDER BY ts DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()


def http_events_select_recent_pg(limit: int) -> list[Any]:
    rows = _pg_fetch_all(
        """
        SELECT method, path, status, ts FROM sidecar.http_events
        ORDER BY ts DESC LIMIT ?
        """,
        (limit,),
    )
    return [
        (r["method"], r["path"], r["status"], int(r["ts"])) for r in rows
    ]


def http_events_select_for_train_sqlite(limit: int) -> list[Any]:
    if not EVENTS_DB_PATH.exists():
        return []
    with sqlite3.connect(str(EVENTS_DB_PATH)) as conn:
        return conn.execute(
            "SELECT method, path, status FROM events ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()


def http_events_select_for_train_pg(limit: int) -> list[Any]:
    rows = _pg_fetch_all(
        """
        SELECT method, path, status FROM sidecar.http_events
        ORDER BY ts DESC LIMIT ?
        """,
        (limit,),
    )
    return [(r["method"], r["path"], r["status"]) for r in rows]


def http_events_exists() -> bool:
    if is_postgres():
        r = _pg_fetch_one(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'sidecar' AND table_name = 'http_events' LIMIT 1
            """,
            (),
        )
        return r is not None
    return EVENTS_DB_PATH.exists()


def http_events_group_count(limit: int) -> list[tuple[Any, ...]]:
    if is_postgres():
        rows = _pg_fetch_all(
            """
            SELECT method, path, COUNT(*)::bigint AS cnt
            FROM sidecar.http_events
            GROUP BY method, path
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [(r["method"], r["path"], int(r["cnt"])) for r in rows]
    if not EVENTS_DB_PATH.exists():
        return []
    with sqlite3.connect(str(EVENTS_DB_PATH)) as conn:
        cur = conn.execute(
            """
            SELECT method, path, COUNT(*) as count
            FROM events
            GROUP BY method, path
            ORDER BY count DESC
            LIMIT ?
            """,
            (limit,),
        )
        return cur.fetchall()


def http_events_list_for_store(
    limit: int, path_prefix: str | None
) -> list[dict[str, Any]]:
    """Lista zdarzeń (format jak sqlite Row → dict) dla EventsStore."""
    limit = max(1, min(int(limit), 500))
    if is_postgres():
        if path_prefix:
            rows = _pg_fetch_all(
                """
                SELECT * FROM sidecar.http_events WHERE path LIKE ?
                ORDER BY ts DESC LIMIT ?
                """,
                (f"{path_prefix}%", limit),
            )
        else:
            rows = _pg_fetch_all(
                "SELECT * FROM sidecar.http_events ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in rows]
    ensure_http_events_schema_sqlite()
    q = "SELECT * FROM events"
    params: list[Any] = []
    if path_prefix:
        q += " WHERE path LIKE ?"
        params.append(f"{path_prefix}%")
    q += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(str(EVENTS_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(q, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def http_events_failure_stats() -> list[tuple[Any, ...]]:
    """Ścieżki z udziałem błędów 5xx (predictor)."""
    if is_postgres():
        rows = _pg_fetch_all(
            """
            SELECT path,
                   SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END)::bigint AS errors,
                   COUNT(*)::bigint AS total
            FROM sidecar.http_events
            GROUP BY path
            """,
            (),
        )
        return [(r["path"], int(r["errors"]), int(r["total"])) for r in rows]
    if not EVENTS_DB_PATH.exists():
        return []
    with sqlite3.connect(str(EVENTS_DB_PATH)) as conn:
        return conn.execute(
            """
            SELECT path,
                   SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END) as errors,
                   COUNT(*) as total
            FROM events
            GROUP BY path
            """,
        ).fetchall()


def http_events_count() -> int:
    if is_postgres():
        r = _pg_fetch_one("SELECT COUNT(*)::bigint AS c FROM sidecar.http_events", ())
        return int(r["c"]) if r else 0
    if not EVENTS_DB_PATH.exists():
        return 0
    with sqlite3.connect(str(EVENTS_DB_PATH)) as conn:
        row = conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0]) if row else 0


# ── Psyche rules (osobny plik SQLite / sidecar.psyche_rules) ─────────────────


def ensure_psyche_rules_schema_sqlite() -> None:
    if is_postgres():
        return
    PSYCHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rules (
                id TEXT PRIMARY KEY,
                ts INTEGER,
                kind TEXT,
                pattern TEXT,
                weight REAL
            )
            """
        )
        conn.commit()


def psyche_rules_count() -> int:
    if is_postgres():
        r = _pg_fetch_one("SELECT COUNT(*)::bigint AS c FROM sidecar.psyche_rules", ())
        return int(r["c"]) if r else 0
    if not PSYCHE_DB_PATH.exists():
        return 0
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        row = conn.execute("SELECT COUNT(*) FROM rules").fetchone()
        return int(row[0]) if row else 0


def psyche_rules_decay_weights_sqlite(factor: float) -> None:
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        conn.execute("UPDATE rules SET weight = weight * ?", (factor,))
        conn.commit()


def psyche_rules_decay_weights_pg(factor: float) -> None:
    _pg_exec("UPDATE sidecar.psyche_rules SET weight = weight * ?", (factor,))


def psyche_get_weight_sqlite(rule_id: str) -> Any:
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        return conn.execute(
            "SELECT weight FROM rules WHERE id=?", (rule_id,)
        ).fetchone()


def psyche_get_weight_pg(rule_id: str) -> Any:
    return _pg_fetch_one(
        "SELECT weight FROM sidecar.psyche_rules WHERE id=?", (rule_id,)
    )


def psyche_upsert_rule_sqlite(
    rule_id: str, ts: int, kind: str, pattern: str, weight: float
) -> None:
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO rules VALUES(?,?,?,?,?)",
            (rule_id, ts, kind, pattern, weight),
        )
        conn.commit()


def psyche_upsert_rule_pg(
    rule_id: str, ts: int, kind: str, pattern: str, weight: float
) -> None:
    _pg_exec(
        """
        INSERT INTO sidecar.psyche_rules(id, ts, kind, pattern, weight)
        VALUES(?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          ts=excluded.ts, kind=excluded.kind, pattern=excluded.pattern, weight=excluded.weight
        """,
        (rule_id, ts, kind, pattern, weight),
    )


def psyche_insert_ignore_pg(
    rule_id: str, ts: int, kind: str, pattern: str, weight: float
) -> None:
    _pg_exec(
        """
        INSERT INTO sidecar.psyche_rules(id, ts, kind, pattern, weight)
        VALUES(?,?,?,?,?)
        ON CONFLICT(id) DO NOTHING
        """,
        (rule_id, ts, kind, pattern, weight),
    )


def psyche_update_rule_weight_sqlite(rule_id: str, weight: float, ts: int) -> None:
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        conn.execute(
            "UPDATE rules SET weight=?, ts=? WHERE id=?",
            (weight, ts, rule_id),
        )
        conn.commit()


def psyche_update_rule_weight_pg(rule_id: str, weight: float, ts: int) -> None:
    _pg_exec(
        "UPDATE sidecar.psyche_rules SET weight=?, ts=? WHERE id=?",
        (weight, ts, rule_id),
    )


def psyche_insert_rule_sqlite(
    rule_id: str, ts: int, kind: str, pattern: str, weight: float
) -> None:
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO rules VALUES(?,?,?,?,?)",
            (rule_id, ts, kind, pattern, weight),
        )
        conn.commit()


def psyche_insert_rule_pg(
    rule_id: str, ts: int, kind: str, pattern: str, weight: float
) -> None:
    _pg_exec(
        """
        INSERT INTO sidecar.psyche_rules(id, ts, kind, pattern, weight)
        VALUES(?,?,?,?,?)
        ON CONFLICT(id) DO NOTHING
        """,
        (rule_id, ts, kind, pattern, weight),
    )


def psyche_rules_like(like_arg: str) -> list[tuple[Any, float]]:
    """Zwraca (pattern, weight) dla reguł endpoint (jak ``WHERE kind='endpoint'`` w legacy predict)."""
    kind_ep = " AND (kind IS NULL OR kind = 'endpoint')"
    if is_postgres():
        rows = _pg_fetch_all(
            "SELECT pattern, weight FROM sidecar.psyche_rules WHERE pattern LIKE ?" + kind_ep,
            (like_arg,),
        )
        return [(r["pattern"], float(r["weight"])) for r in rows]
    if not PSYCHE_DB_PATH.exists():
        return []
    ensure_psyche_rules_schema_sqlite()
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        raw = conn.execute(
            "SELECT pattern, weight FROM rules WHERE pattern LIKE ?" + kind_ep,
            (like_arg,),
        ).fetchall()
    return [(r[0], float(r[1])) for r in raw]


def psyche_heal_insert_sqlite(
    rule_id: str, ts: int, kind: str, pattern: str, weight: float
) -> None:
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO rules VALUES(?,?,?,?,?)",
            (rule_id, ts, kind, pattern, weight),
        )
        conn.commit()


def psyche_heal_insert_pg(
    rule_id: str, ts: int, kind: str, pattern: str, weight: float
) -> None:
    psyche_upsert_rule_pg(rule_id, ts, kind, pattern, weight)


def anomalies_ensure_schema_sqlite() -> None:
    if is_postgres():
        return
    ensure_psyche_rules_schema_sqlite()
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS anomalies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                method TEXT,
                path TEXT,
                status INTEGER,
                expected INTEGER,
                confidence REAL
            )
            """
        )
        conn.commit()


def anomalies_insert_sqlite(
    ts: int, method: str, path: str, status: int, expected: int, confidence: float
) -> None:
    anomalies_ensure_schema_sqlite()
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        conn.execute(
            """
            INSERT INTO anomalies (ts, method, path, status, expected, confidence)
            VALUES(?,?,?,?,?,?)
            """,
            (ts, method, path, status, expected, confidence),
        )
        conn.commit()


def anomalies_insert_pg(
    ts: int, method: str, path: str, status: int, expected: int, confidence: float
) -> None:
    _pg_exec(
        """
        INSERT INTO sidecar.anomalies(ts, method, path, status, expected, confidence)
        VALUES(?,?,?,?,?,?)
        """,
        (ts, method, path, status, expected, confidence),
    )


def anomalies_list_sqlite(limit: int) -> list[Any]:
    if not PSYCHE_DB_PATH.exists():
        return []
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        return conn.execute(
            """
            SELECT ts, method, path, status, expected, confidence
            FROM anomalies
            ORDER BY ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def anomalies_list_pg(limit: int) -> list[Any]:
    rows = _pg_fetch_all(
        """
        SELECT ts, method, path, status, expected, confidence
        FROM sidecar.anomalies
        ORDER BY ts DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [
        (
            int(r["ts"]),
            r["method"],
            r["path"],
            int(r["status"]),
            int(r["expected"]),
            float(r["confidence"]),
        )
        for r in rows
    ]


# ── Self-heal (healed) ────────────────────────────────────────────────────────


def healed_init_sqlite(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS healed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                backup_path TEXT NOT NULL,
                snapshot TEXT NOT NULL,
                ts INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_healed_ts ON healed(ts)"
        )
        conn.commit()


def healed_insert_sqlite(
    db_path: Path, path: str, backup_path: str, snapshot: str, ts: int
) -> None:
    healed_init_sqlite(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO healed (path, backup_path, snapshot, ts)
            VALUES (?, ?, ?, ?)
            """,
            (path, backup_path, snapshot, ts),
        )
        conn.commit()


def healed_insert_pg(path: str, backup_path: str, snapshot: str, ts: int) -> None:
    _pg_exec(
        """
        INSERT INTO sidecar.healed(path, backup_path, snapshot, ts)
        VALUES(?,?,?,?)
        """,
        (path, backup_path, snapshot, ts),
    )


def healed_recent_sqlite(db_path: Path, limit: int) -> list[Any]:
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(
            "SELECT path, ts FROM healed ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()


def healed_recent_pg(limit: int) -> list[Any]:
    rows = _pg_fetch_all(
        "SELECT path, ts FROM sidecar.healed ORDER BY ts DESC LIMIT ?",
        (limit,),
    )
    return [(r["path"], int(r["ts"])) for r in rows]


def healed_rollback_rows_sqlite(db_path: Path, limit: int) -> list[Any]:
    healed_init_sqlite(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(
            "SELECT path, snapshot FROM healed ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()


def healed_rollback_rows_pg(limit: int) -> list[Any]:
    rows = _pg_fetch_all(
        "SELECT path, snapshot FROM sidecar.healed ORDER BY ts DESC LIMIT ?",
        (limit,),
    )
    return [(r["path"], r["snapshot"]) for r in rows]
