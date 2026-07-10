#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent state and task queue persistence.
- Agent state: cursor (last_stm_ts), enabled flag per user.
- Task queue: priority queue with claim/complete, optional TTL and cleanup.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from .db import _db_backend, exec_one, fetch_all, fetch_one, json_dumps, json_loads, now_ts

logger = logging.getLogger(__name__)

# Statusy zadań (wartości w DB)
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# Zadanie uznane za "zawieszone" po tej liczbie sekund (reclaim do kolejki)
TASK_RUNNING_TIMEOUT_S = 300  # 5 min

# Retention: zadania done/failed starsze niż tyle sekund można czyścić
TASK_RETENTION_S = 86400 * 7  # 7 dni

_SCHEMA_VERSION = 1


def ensure_schema() -> None:
    """Tworzy tabele agent_state i agent_tasks oraz indeksy. Idempotentne."""
    if _db_backend() == "postgres":
        return
    exec_one("""
    CREATE TABLE IF NOT EXISTS agent_state (
        user_id TEXT PRIMARY KEY,
        last_stm_ts REAL NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1,
        updated_at REAL NOT NULL
    );
    """)
    exec_one("""
    CREATE TABLE IF NOT EXISTS agent_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 50,
        type TEXT NOT NULL,
        payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        created_at REAL NOT NULL,
        started_at REAL,
        finished_at REAL,
        error TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0
    );
    """)
    exec_one(
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_user_status_pri "
        "ON agent_tasks(user_id, status, priority, created_at);"
    )
    exec_one(
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_started "
        "ON agent_tasks(started_at) WHERE status = 'running';"
    )
    try:
        exec_one("ALTER TABLE agent_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;")
    except Exception as exc:
        logger.debug("agent_tasks.retry_count migration already applied or unavailable: %s", exc)


def _get_retry_count(row: Any) -> int:
    try:
        return int(row["retry_count"])
    except (KeyError, TypeError):
        return 0


def _row_to_state(row: Any) -> Dict[str, Any]:
    return {
        "user_id": row["user_id"],
        "last_stm_ts": float(row["last_stm_ts"]),
        "enabled": bool(row["enabled"]),
        "updated_at": float(row["updated_at"]),
    }


def _row_to_task(row: Any) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "user_id": row["user_id"],
        "priority": int(row["priority"]),
        "type": row["type"],
        "payload": json_loads(row["payload"]) or {},
        "status": row["status"],
        "created_at": float(row["created_at"]),
        "started_at": float(row["started_at"]) if row["started_at"] is not None else None,
        "finished_at": float(row["finished_at"]) if row["finished_at"] is not None else None,
        "error": row["error"],
        "retry_count": _get_retry_count(row),
    }


def get_agent_state(user_id: str) -> Dict[str, Any]:
    """Zwraca stan agenta (cursor, enabled). Tworzy wpis jeśli brak."""
    row = fetch_one("SELECT * FROM agent_state WHERE user_id=?", (user_id,))
    if row:
        return _row_to_state(row)
    ts = now_ts()
    exec_one(
        "INSERT INTO agent_state(user_id,last_stm_ts,enabled,updated_at) VALUES(?,?,?,?)",
        (user_id, 0.0, 1, ts),
    )
    return {"user_id": user_id, "last_stm_ts": 0.0, "enabled": True, "updated_at": ts}


def set_enabled(user_id: str, enabled: bool) -> None:
    get_agent_state(user_id)
    exec_one(
        "UPDATE agent_state SET enabled=?, updated_at=? WHERE user_id=?",
        (1 if enabled else 0, now_ts(), user_id),
    )


def update_cursor(user_id: str, last_stm_ts: float) -> None:
    get_agent_state(user_id)
    exec_one(
        "UPDATE agent_state SET last_stm_ts=?, updated_at=? WHERE user_id=?",
        (float(last_stm_ts), now_ts(), user_id),
    )


def enqueue_task(
    user_id: str,
    typ: str,
    payload: Dict[str, Any],
    priority: int = 50,
) -> int:
    """Dodaje zadanie do kolejki. Zwraca task_id (>0) lub -1 przy błędzie."""
    ts = now_ts()
    if _db_backend() == "postgres":
        row = fetch_one(
            "INSERT INTO agent_tasks(user_id,priority,type,payload,status,created_at,retry_count) "
            "VALUES(?,?,?,?,?,?,0) RETURNING id",
            (user_id, int(priority), str(typ), json_dumps(payload or {}), STATUS_QUEUED, ts),
        )
        tid = int(row["id"]) if row and row.get("id") is not None else -1
    else:
        exec_one(
            "INSERT INTO agent_tasks(user_id,priority,type,payload,status,created_at,retry_count) "
            "VALUES(?,?,?,?,?,?,0)",
            (user_id, int(priority), str(typ), json_dumps(payload or {}), STATUS_QUEUED, ts),
        )
        row = fetch_one("SELECT last_insert_rowid() AS id", ())
        tid = int(row["id"]) if row else -1
    if tid > 0:
        logger.debug("enqueue_task user=%s type=%s priority=%s task_id=%s", user_id, typ, priority, tid)
    return tid


def list_tasks(
    user_id: str,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Lista zadań użytkownika. Opcjonalnie filtrowanie po statusie, z paginacją."""
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    if status:
        rows = fetch_all(
            "SELECT * FROM agent_tasks WHERE user_id=? AND status=? "
            "ORDER BY priority ASC, created_at ASC LIMIT ? OFFSET ?",
            (user_id, status, limit, offset),
        )
    else:
        rows = fetch_all(
            "SELECT * FROM agent_tasks WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        )
    return [_row_to_task(r) for r in rows]


def count_tasks(user_id: str, status: Optional[str] = None) -> int:
    """Liczba zadań użytkownika (opcjonalnie w danym statusie)."""
    if status:
        row = fetch_one(
            "SELECT COUNT(*) AS c FROM agent_tasks WHERE user_id=? AND status=?",
            (user_id, status),
        )
    else:
        row = fetch_one("SELECT COUNT(*) AS c FROM agent_tasks WHERE user_id=?", (user_id,))
    return int(row["c"]) if row else 0


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    """Pobiera pojedyncze zadanie po id."""
    row = fetch_one("SELECT * FROM agent_tasks WHERE id=?", (int(task_id),))
    return _row_to_task(row) if row else None


def claim_next_task(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Atomowo wybiera najstarsze zadanie w statusie queued (najwyższy priority)
    i ustawia status=running, started_at=now(). Zwraca opis zadania lub None.
    """
    rows = fetch_all(
        "SELECT id FROM agent_tasks WHERE user_id=? AND status=? "
        "ORDER BY priority ASC, created_at ASC LIMIT 1",
        (user_id, STATUS_QUEUED),
    )
    if not rows:
        return None
    tid = int(rows[0]["id"])
    ts = now_ts()
    exec_one(
        "UPDATE agent_tasks SET status=?, started_at=? WHERE id=? AND status=?",
        (STATUS_RUNNING, ts, tid, STATUS_QUEUED),
    )
    row = fetch_one("SELECT * FROM agent_tasks WHERE id=?", (tid,))
    if not row or row["status"] != STATUS_RUNNING:
        return None
    out = _row_to_task(row)
    out["finished_at"] = None
    out["error"] = None
    logger.debug("claim_next_task user=%s task_id=%s type=%s", user_id, tid, out.get("type"))
    return out


def complete_task(task_id: int, ok: bool, error: str = "") -> None:
    """Oznacza zadanie jako done lub failed. error obcinany do 2000 znaków."""
    task_id = int(task_id)
    ts = now_ts()
    if ok:
        exec_one(
            "UPDATE agent_tasks SET status=?, finished_at=?, error=NULL WHERE id=?",
            (STATUS_DONE, ts, task_id),
        )
        logger.debug("complete_task task_id=%s ok=True", task_id)
    else:
        err = (error or "")[:2000]
        exec_one(
            "UPDATE agent_tasks SET status=?, finished_at=?, error=? WHERE id=?",
            (STATUS_FAILED, ts, err, task_id),
        )
        logger.debug("complete_task task_id=%s ok=False error=%s", task_id, err[:80])


def reclaim_stale_running_tasks(user_id: Optional[str] = None) -> int:
    """
    Przywraca do kolejki zadania w statusie 'running' starsze niż TASK_RUNNING_TIMEOUT_S.
    Opcjonalnie tylko dla danego user_id. Zwraca liczbę przywróconych zadań.
    """
    deadline = now_ts() - TASK_RUNNING_TIMEOUT_S
    if user_id:
        rows = fetch_all(
            "SELECT id FROM agent_tasks WHERE user_id=? AND status=? AND started_at < ?",
            (user_id, STATUS_RUNNING, deadline),
        )
    else:
        rows = fetch_all(
            "SELECT id FROM agent_tasks WHERE status=? AND started_at < ?",
            (STATUS_RUNNING, deadline),
        )
    count = 0
    for r in rows:
        tid = int(r["id"])
        exec_one(
            "UPDATE agent_tasks SET status=?, started_at=NULL WHERE id=? AND status=?",
            (STATUS_QUEUED, tid, STATUS_RUNNING),
        )
        count += 1
    if count:
        logger.info("reclaim_stale_running_tasks user_id=%s reclaimed=%s", user_id, count)
    return count


def retry_failed_task(task_id: int) -> bool:
    """Ustawia zadanie failed z powrotem na queued (retry). Zwraca True jeśli zaktualizowano."""
    task_id = int(task_id)
    row = fetch_one("SELECT id, retry_count FROM agent_tasks WHERE id=? AND status=?", (task_id, STATUS_FAILED))
    if not row:
        return False
    retry_count = int(getattr(row, "retry_count", 0) or 0)
    exec_one(
        "UPDATE agent_tasks SET status=?, finished_at=NULL, started_at=NULL, error=NULL, retry_count=? WHERE id=?",
        (STATUS_QUEUED, retry_count + 1, task_id),
    )
    logger.info("retry_failed_task task_id=%s retry_count=%s", task_id, retry_count + 1)
    return True


def cleanup_old_tasks(
    older_than_ts: Optional[float] = None,
    statuses: Tuple[str, ...] = (STATUS_DONE, STATUS_FAILED),
) -> int:
    """
    Usuwa stare zadania w podanych statusach (domyślnie done, failed).
    older_than_ts: domyślnie now - TASK_RETENTION_S.
    Zwraca liczbę usuniętych wierszy.
    """
    if older_than_ts is None:
        older_than_ts = now_ts() - TASK_RETENTION_S
    bind_marks = ",".join("?" * len(statuses))
    rows = fetch_all(
        f"SELECT id FROM agent_tasks WHERE status IN ({bind_marks}) AND finished_at < ?",
        (*statuses, older_than_ts),
    )
    count = 0
    for r in rows:
        exec_one("DELETE FROM agent_tasks WHERE id=?", (int(r["id"]),))
        count += 1
    if count:
        logger.info("cleanup_old_tasks deleted=%s older_than=%s", count, older_than_ts)
    return count


def get_queue_stats(user_id: str) -> Dict[str, Any]:
    """Zestawienie liczby zadań wg statusu oraz ewentualnie najstarsze running."""
    rows = fetch_all(
        "SELECT status, COUNT(*) AS c FROM agent_tasks WHERE user_id=? GROUP BY status",
        (user_id,),
    )
    by_status = {r["status"]: int(r["c"]) for r in rows}
    oldest_running = fetch_one(
        "SELECT id, type, started_at FROM agent_tasks WHERE user_id=? AND status=? ORDER BY started_at ASC LIMIT 1",
        (user_id, STATUS_RUNNING),
    )
    return {
        "by_status": by_status,
        "total": sum(by_status.values()),
        "oldest_running": {
            "id": int(oldest_running["id"]),
            "type": oldest_running["type"],
            "started_at": float(oldest_running["started_at"]),
        } if oldest_running else None,
    }
