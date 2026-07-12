"""Durable, leased jobs with idempotent TurnCompleted fan-out.

PostgreSQL workers claim work with ``FOR UPDATE SKIP LOCKED``.  SQLite keeps
the same state machine for development/tests and serializes claims with
``BEGIN IMMEDIATE``.  A completed handler receipt is the idempotency boundary:
redelivery, process restart and an expired job lease do not repeat it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from aihub.db import _DB_LOCK, _conn, _db_backend, json_dumps, json_loads

log = logging.getLogger(__name__)

TURN_COMPLETED = "TurnCompleted"
TURN_HANDLERS = ("memory", "psyche", "reflection")
Handler = Callable[[dict[str, Any], str], Any]


def ensure_schema() -> None:
    with _DB_LOCK, _conn() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS durable_jobs (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 12,
                next_attempt_ts REAL NOT NULL DEFAULT 0,
                lease_owner TEXT,
                lease_until_ts REAL,
                last_error TEXT,
                created_ts REAL NOT NULL,
                updated_ts REAL NOT NULL,
                completed_ts REAL
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_durable_jobs_claim "
            "ON durable_jobs(status, next_attempt_ts, lease_until_ts, created_ts)"
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS durable_job_receipts (
                job_id TEXT NOT NULL,
                handler TEXT NOT NULL,
                status TEXT NOT NULL,
                lease_owner TEXT,
                lease_until_ts REAL,
                attempts INTEGER NOT NULL DEFAULT 0,
                result TEXT,
                last_error TEXT,
                completed_ts REAL,
                updated_ts REAL NOT NULL,
                PRIMARY KEY(job_id, handler)
            )
            """
        )
        con.commit()


def enqueue(
    kind: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str,
    max_attempts: int = 12,
) -> str:
    """Insert once and return the stable job id for an idempotency key."""
    ensure_schema()
    now = time.time()
    job_id = "job-" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
    with _DB_LOCK, _conn() as con:
        con.execute(
            """
            INSERT INTO durable_jobs(
                id, idempotency_key, kind, payload, status, attempts,
                max_attempts, next_attempt_ts, created_ts, updated_ts
            ) VALUES(?,?,?,?, 'pending',0,?,0,?,?)
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            (job_id, idempotency_key, kind, json_dumps(payload), max(1, max_attempts), now, now),
        )
        row = con.execute(
            "SELECT id FROM durable_jobs WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        con.commit()
    return str(row["id"])


def enqueue_turn_completed(
    *,
    turn_id: str,
    user_id: str,
    user_message: str,
    assistant_message: str,
    intent: str = "chat",
    metadata: dict[str, Any] | None = None,
    reflection: dict[str, Any] | None = None,
) -> str:
    """Publish one canonical completion event; repeated publication is a no-op."""
    if not turn_id.strip():
        raise ValueError("turn_id is required for exactly-once delivery")
    payload = {
        "turn_id": turn_id,
        "user_id": user_id,
        "user_message": user_message,
        "assistant_message": assistant_message,
        "intent": intent,
        "metadata": dict(metadata or {}),
        "reflection": dict(reflection or {}),
    }
    return enqueue(
        TURN_COMPLETED,
        payload,
        idempotency_key=f"{TURN_COMPLETED}:{user_id}:{turn_id}",
    )


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _claim_one(owner: str, lease_seconds: float) -> dict[str, Any] | None:
    ensure_schema()
    now = time.time()
    lease_until = now + max(1.0, lease_seconds)
    with _DB_LOCK, _conn() as con:
        try:
            if _db_backend() == "postgres":
                row = con.execute(
                    """
                    SELECT * FROM durable_jobs
                    WHERE attempts < max_attempts
                      AND next_attempt_ts <= ?
                      AND (status IN ('pending','retry')
                           OR (status='running' AND lease_until_ts < ?))
                    ORDER BY created_ts
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (now, now),
                ).fetchone()
            else:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute(
                    """
                    SELECT * FROM durable_jobs
                    WHERE attempts < max_attempts
                      AND next_attempt_ts <= ?
                      AND (status IN ('pending','retry')
                           OR (status='running' AND lease_until_ts < ?))
                    ORDER BY created_ts
                    LIMIT 1
                    """,
                    (now, now),
                ).fetchone()
            if row is None:
                con.commit()
                return None
            con.execute(
                """
                UPDATE durable_jobs
                SET status='running', lease_owner=?, lease_until_ts=?,
                    attempts=attempts+1, updated_ts=?
                WHERE id=?
                """,
                (owner, lease_until, now, row["id"]),
            )
            con.commit()
            out = dict(row)
            out["attempts"] = int(out["attempts"]) + 1
            return out
        except Exception:
            con.rollback()
            raise


def _receipt_done(job_id: str, handler: str) -> bool:
    with _DB_LOCK, _conn() as con:
        row = con.execute(
            "SELECT status FROM durable_job_receipts WHERE job_id=? AND handler=?",
            (job_id, handler),
        ).fetchone()
    return bool(row and row["status"] == "completed")


def _save_receipt(
    job_id: str,
    handler: str,
    owner: str,
    *,
    status: str,
    result: Any = None,
    error: str | None = None,
) -> None:
    now = time.time()
    with _DB_LOCK, _conn() as con:
        con.execute(
            """
            INSERT INTO durable_job_receipts(
                job_id, handler, status, lease_owner, attempts, result,
                last_error, completed_ts, updated_ts
            ) VALUES(?,?,?,?,1,?,?,?,?)
            ON CONFLICT(job_id,handler) DO UPDATE SET
                status=excluded.status,
                lease_owner=excluded.lease_owner,
                attempts=durable_job_receipts.attempts+1,
                result=excluded.result,
                last_error=excluded.last_error,
                completed_ts=excluded.completed_ts,
                updated_ts=excluded.updated_ts
            """,
            (
                job_id,
                handler,
                status,
                owner,
                json_dumps(result) if result is not None else None,
                (error or "")[:2000],
                now if status == "completed" else None,
                now,
            ),
        )
        con.commit()


def _default_handlers() -> dict[str, Handler]:
    def memory(payload: dict[str, Any], event_id: str) -> Any:
        from aihub.memory_core import get_memory_core

        meta = {**payload.get("metadata", {}), "durable_event_id": event_id}
        return get_memory_core().ingest_turn(
            payload["user_id"],
            payload["user_message"],
            payload["assistant_message"],
            payload.get("intent", "chat"),
            meta,
        )

    def psyche(payload: dict[str, Any], event_id: str) -> Any:
        from aihub.psyche_core import get_psyche_core

        core = get_psyche_core()
        core.evolve_once(payload["user_id"], payload["user_message"], "user", f"{event_id}:user")
        return core.evolve_once(
            payload["user_id"], payload["assistant_message"], "assistant", f"{event_id}:assistant"
        )

    def reflection(payload: dict[str, Any], event_id: str) -> Any:
        from aihub.reflection_engine import ReflectionInput, reflect_on_action

        data = payload.get("reflection") or {}
        out = reflect_on_action(
            ReflectionInput(
                user_id=payload["user_id"],
                action_type=data.get("action_type", "chat_turn"),
                parameters=data.get("parameters", {}),
                confidence=float(data.get("confidence", 1.0)),
                execution_result=data.get("execution_result", {"ok": True}),
                decision_reasoning=data.get("decision_reasoning", "TurnCompleted"),
                context={**data.get("context", {}), "durable_event_id": event_id},
            ),
        )
        return {"reflection_id": out.reflection_id}

    return {"memory": memory, "psyche": psyche, "reflection": reflection}


@dataclass
class RunReport:
    claimed: int = 0
    completed: int = 0
    retried: int = 0
    failed: int = 0


def run_once(
    *,
    handlers: dict[str, Handler] | None = None,
    owner: str | None = None,
    lease_seconds: float = 30.0,
) -> RunReport:
    """Claim and execute one job. Handler receipts survive worker restarts."""
    owner = owner or _worker_id()
    job = _claim_one(owner, lease_seconds)
    report = RunReport()
    if job is None:
        return report
    report.claimed = 1
    payload = json_loads(job["payload"]) or {}
    selected = handlers or _default_handlers()
    try:
        names = TURN_HANDLERS if job["kind"] == TURN_COMPLETED else tuple(selected)
        for name in names:
            if _receipt_done(job["id"], name):
                continue
            handler = selected.get(name)
            if handler is None:
                raise RuntimeError(f"missing durable handler: {name}")
            result = handler(payload, str(job["id"]))
            _save_receipt(job["id"], name, owner, status="completed", result=result)
        now = time.time()
        with _DB_LOCK, _conn() as con:
            con.execute(
                """
                UPDATE durable_jobs SET status='completed', lease_owner=NULL,
                    lease_until_ts=NULL, completed_ts=?, updated_ts=?
                WHERE id=? AND lease_owner=?
                """,
                (now, now, job["id"], owner),
            )
            con.commit()
        report.completed = 1
    except Exception as exc:
        attempts = int(job["attempts"])
        terminal = attempts >= int(job["max_attempts"])
        delay = min(3600.0, 2.0 ** min(attempts, 10))
        with _DB_LOCK, _conn() as con:
            con.execute(
                """
                UPDATE durable_jobs SET status=?, next_attempt_ts=?,
                    lease_owner=NULL, lease_until_ts=NULL, last_error=?, updated_ts=?
                WHERE id=? AND lease_owner=?
                """,
                (
                    "failed" if terminal else "retry",
                    time.time() + delay,
                    str(exc)[:2000],
                    time.time(),
                    job["id"],
                    owner,
                ),
            )
            con.commit()
        log.warning("durable job failed id=%s attempts=%s: %s", job["id"], attempts, exc)
        report.failed = int(terminal)
        report.retried = int(not terminal)
    return report


def get_job(job_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with _DB_LOCK, _conn() as con:
        row = con.execute("SELECT * FROM durable_jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def execute_turn_completed_inline(
    *,
    turn_id: str,
    user_id: str,
    user_message: str,
    assistant_message: str,
    intent: str = "chat",
    metadata: dict[str, Any] | None = None,
    reflection: dict[str, Any] | None = None,
    handlers: dict[str, Handler] | None = None,
    owner: str = "inline",
) -> dict[str, Any]:
    """Enqueue and synchronously execute TurnCompleted handlers once each."""
    job_id = enqueue_turn_completed(
        turn_id=turn_id,
        user_id=user_id,
        user_message=user_message,
        assistant_message=assistant_message,
        intent=intent,
        metadata=metadata,
        reflection=reflection,
    )
    payload = {
        "turn_id": turn_id,
        "user_id": user_id,
        "user_message": user_message,
        "assistant_message": assistant_message,
        "intent": intent,
        "metadata": dict(metadata or {}),
        "reflection": dict(reflection or {}),
    }
    selected = handlers or _default_handlers()
    results: dict[str, Any] = {"job_id": job_id, "handlers": {}}
    for name in TURN_HANDLERS:
        if _receipt_done(job_id, name):
            continue
        handler = selected.get(name)
        if handler is None:
            raise RuntimeError(f"missing durable handler: {name}")
        result = handler(payload, job_id)
        _save_receipt(job_id, name, owner, status="completed", result=result)
        results["handlers"][name] = result
    now = time.time()
    with _DB_LOCK, _conn() as con:
        con.execute(
            """
            UPDATE durable_jobs SET status='completed', lease_owner=NULL,
                lease_until_ts=NULL, completed_ts=?, updated_ts=?
            WHERE id=?
            """,
            (now, now, job_id),
        )
        con.commit()
    return results

