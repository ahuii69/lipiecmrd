"""Persistent turn execution + effect idempotency (Postgres/SQLite)."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from aihub.db import _DB_LOCK, _conn, _db_backend, json_dumps, json_loads

log = logging.getLogger(__name__)

_SCHEMA_READY = False


def ensure_turn_schema() -> None:
    """Create turn_executions / turn_effects tables. Idempotent and explicit."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _DB_LOCK, _conn() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS turn_executions (
                turn_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                request_id TEXT NOT NULL DEFAULT '',
                correlation_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                error_json TEXT,
                started_ts REAL,
                completed_ts REAL,
                created_ts REAL NOT NULL,
                updated_ts REAL NOT NULL
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_turn_exec_user_session_ts "
            "ON turn_executions(user_id, session_id, created_ts DESC)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_turn_exec_status "
            "ON turn_executions(status, updated_ts)"
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS turn_effects (
                id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL,
                effect_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                retry_count INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                last_error TEXT,
                created_ts REAL NOT NULL,
                updated_ts REAL NOT NULL,
                completed_ts REAL,
                UNIQUE(turn_id, effect_type)
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_turn_effects_status "
            "ON turn_effects(status, updated_ts)"
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS turn_outbox (
                id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL,
                effect_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                available_at REAL NOT NULL DEFAULT 0,
                created_ts REAL NOT NULL,
                updated_ts REAL NOT NULL,
                UNIQUE(turn_id, effect_type)
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_turn_outbox_claim "
            "ON turn_outbox(status, available_at, created_ts)"
        )
        con.commit()
    _SCHEMA_READY = True


def _effect_id(turn_id: str, effect_type: str) -> str:
    digest = hashlib.sha256(f"{turn_id}:{effect_type}".encode("utf-8")).hexdigest()[:28]
    return f"eff-{digest}"


def begin_or_reuse_turn(
    *,
    turn_id: str,
    idempotency_key: str,
    user_id: str,
    session_id: str,
    request_id: str = "",
    correlation_id: str = "",
    running_stale_after_s: float = 180.0,
) -> dict[str, Any]:
    """Insert pending turn or return existing success/running row for same key.

    A ``running`` row older than ``running_stale_after_s`` (process crash / kill)
    is reclaimed as a retry instead of permanent conflict.
    """
    ensure_turn_schema()
    now = time.time()
    with _DB_LOCK, _conn() as con:
        existing = con.execute(
            "SELECT * FROM turn_executions WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            row = dict(existing)
            if row.get("status") == "succeeded" and row.get("result_json"):
                return {
                    "action": "reuse",
                    "turn_id": row["turn_id"],
                    "status": row["status"],
                    "result": json_loads(row["result_json"]),
                }
            if row.get("status") == "running":
                started = float(row.get("started_ts") or row.get("updated_ts") or 0.0)
                stale = (now - started) > max(5.0, float(running_stale_after_s))
                if not stale:
                    return {
                        "action": "conflict",
                        "turn_id": row["turn_id"],
                        "status": row["status"],
                    }
                # Reclaim abandoned running turn (restart / kill mid-flight).
                con.execute(
                    """
                    UPDATE turn_executions
                    SET status='running', attempt_count=attempt_count+1,
                        started_ts=?, updated_ts=?, error_json=?
                    WHERE turn_id=?
                    """,
                    (
                        now,
                        now,
                        json_dumps({"reclaimed": "stale_running"}),
                        row["turn_id"],
                    ),
                )
                con.commit()
                return {
                    "action": "retry",
                    "turn_id": row["turn_id"],
                    "status": "running",
                }
            # failed/cancelled/pending → claim again with same turn_id
            con.execute(
                """
                UPDATE turn_executions
                SET status='running', attempt_count=attempt_count+1,
                    started_ts=?, updated_ts=?, error_json=NULL
                WHERE turn_id=?
                """,
                (now, now, row["turn_id"]),
            )
            con.commit()
            return {
                "action": "retry",
                "turn_id": row["turn_id"],
                "status": "running",
            }

        con.execute(
            """
            INSERT INTO turn_executions(
                turn_id, idempotency_key, user_id, session_id, request_id,
                correlation_id, status, attempt_count, started_ts, created_ts, updated_ts
            ) VALUES(?,?,?,?,?,?, 'running', 1, ?, ?, ?)
            """,
            (
                turn_id,
                idempotency_key,
                user_id,
                session_id,
                request_id,
                correlation_id,
                now,
                now,
                now,
            ),
        )
        con.commit()
        return {"action": "started", "turn_id": turn_id, "status": "running"}


def complete_turn(
    turn_id: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    ensure_turn_schema()
    now = time.time()
    with _DB_LOCK, _conn() as con:
        con.execute(
            """
            UPDATE turn_executions
            SET status=?, result_json=?, error_json=?, completed_ts=?, updated_ts=?
            WHERE turn_id=?
            """,
            (
                status,
                json_dumps(result) if result is not None else None,
                json_dumps(error) if error is not None else None,
                now,
                now,
                turn_id,
            ),
        )
        con.commit()


def claim_effect(turn_id: str, effect_type: str) -> dict[str, Any]:
    """Claim exactly-once effect slot. Returns action: execute|skip|retry."""
    ensure_turn_schema()
    now = time.time()
    eid = _effect_id(turn_id, effect_type)
    with _DB_LOCK, _conn() as con:
        row = con.execute(
            "SELECT * FROM turn_effects WHERE turn_id=? AND effect_type=?",
            (turn_id, effect_type),
        ).fetchone()
        if row:
            d = dict(row)
            if d.get("status") == "succeeded":
                return {
                    "action": "skip",
                    "result": json_loads(d.get("result_json") or "") or {},
                }
            con.execute(
                """
                UPDATE turn_effects
                SET status='running', retry_count=retry_count+1, updated_ts=?, last_error=NULL
                WHERE id=?
                """,
                (now, d["id"]),
            )
            con.commit()
            return {"action": "retry", "effect_id": d["id"]}

        con.execute(
            """
            INSERT INTO turn_effects(
                id, turn_id, effect_type, status, retry_count, created_ts, updated_ts
            ) VALUES(?,?,?, 'running', 0, ?, ?)
            """,
            (eid, turn_id, effect_type, now, now),
        )
        con.commit()
        return {"action": "execute", "effect_id": eid}


def finish_effect(
    turn_id: str,
    effect_type: str,
    *,
    ok: bool,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    ensure_turn_schema()
    now = time.time()
    with _DB_LOCK, _conn() as con:
        con.execute(
            """
            UPDATE turn_effects
            SET status=?, result_json=?, last_error=?, completed_ts=?, updated_ts=?
            WHERE turn_id=? AND effect_type=?
            """,
            (
                "succeeded" if ok else "failed",
                json_dumps(result or {}),
                (error or "")[:2000] if error else None,
                now if ok else None,
                now,
                turn_id,
                effect_type,
            ),
        )
        con.commit()


def enqueue_outbox(
    turn_id: str,
    effect_type: str,
    payload: dict[str, Any],
) -> str:
    ensure_turn_schema()
    now = time.time()
    oid = _effect_id(turn_id, f"outbox:{effect_type}")
    with _DB_LOCK, _conn() as con:
        con.execute(
            """
            INSERT INTO turn_outbox(
                id, turn_id, effect_type, payload_json, status,
                retry_count, available_at, created_ts, updated_ts
            ) VALUES(?,?,?,?, 'pending', 0, 0, ?, ?)
            ON CONFLICT(turn_id, effect_type) DO NOTHING
            """,
            (oid, turn_id, effect_type, json_dumps(payload), now, now),
        )
        con.commit()
    return oid


def claim_outbox(limit: int = 10) -> list[dict[str, Any]]:
    ensure_turn_schema()
    now = time.time()
    claimed: list[dict[str, Any]] = []
    with _DB_LOCK, _conn() as con:
        if _db_backend() == "postgres":
            rows = con.execute(
                """
                SELECT * FROM turn_outbox
                WHERE status IN ('pending','retry') AND available_at <= ?
                ORDER BY created_ts ASC
                LIMIT ?
                FOR UPDATE SKIP LOCKED
                """,
                (now, max(1, limit)),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT * FROM turn_outbox
                WHERE status IN ('pending','retry') AND available_at <= ?
                ORDER BY created_ts ASC
                LIMIT ?
                """,
                (now, max(1, limit)),
            ).fetchall()
        for row in rows or []:
            d = dict(row)
            con.execute(
                "UPDATE turn_outbox SET status='running', updated_ts=? WHERE id=?",
                (now, d["id"]),
            )
            d["payload"] = json_loads(d.get("payload_json") or "") or {}
            claimed.append(d)
        con.commit()
    return claimed


def complete_outbox(outbox_id: str, *, ok: bool, error: str | None = None) -> None:
    ensure_turn_schema()
    now = time.time()
    with _DB_LOCK, _conn() as con:
        if ok:
            con.execute(
                """
                UPDATE turn_outbox SET status='succeeded', updated_ts=?, last_error=NULL
                WHERE id=?
                """,
                (now, outbox_id),
            )
        else:
            con.execute(
                """
                UPDATE turn_outbox
                SET status='retry', retry_count=retry_count+1, last_error=?,
                    available_at=?, updated_ts=?
                WHERE id=?
                """,
                ((error or "")[:2000], now + 2.0, now, outbox_id),
            )
        con.commit()
