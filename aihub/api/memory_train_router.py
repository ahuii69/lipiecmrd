# LEGACY / UNMOUNTED: not mounted in aihub.main; canonical HTTP surface is aihub.main + aihub/*_api.py. See aihub/api/_LEGACY.md.
import hashlib
import sqlite3
import time

from fastapi import APIRouter, HTTPException

from aihub.db import exec_one, fetch_all
from aihub.sidecar_db import (
    EVENTS_DB_PATH,
    PSYCHE_DB_PATH,
    ensure_psyche_rules_schema_sqlite,
    is_postgres,
)

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("/train-from-events")
def train_from_events(limit: int = 1000):
    if is_postgres():
        rows = fetch_all(
            """
            SELECT method, path, status, COUNT(*)::bigint AS cnt
            FROM sidecar.http_events
            GROUP BY method, path, status
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (limit,),
        )
        now = int(time.time())
        trained = 0
        for r in rows:
            method, path, status, count = (
                r["method"],
                r["path"],
                r["status"],
                int(r["cnt"]),
            )
            pattern = f"{method}:{path}:{status}"
            rule_id = hashlib.sha256(f"endpoint:{pattern}".encode()).hexdigest()
            weight = min(1.0, count / 10.0)
            exec_one(
                """
                INSERT INTO sidecar.psyche_rules(id, ts, kind, pattern, weight)
                VALUES(?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  ts=excluded.ts, kind=excluded.kind, pattern=excluded.pattern, weight=excluded.weight
                """,
                (rule_id, now, "endpoint", pattern, weight),
            )
            trained += 1
        return {
            "status": "ok",
            "trained_rules": trained,
            "events_db": "postgresql:sidecar.http_events",
            "psyche_db": "postgresql:sidecar.psyche_rules",
        }

    if not EVENTS_DB_PATH.exists():
        raise HTTPException(500, "events.db not found")

    ensure_psyche_rules_schema_sqlite()

    with sqlite3.connect(str(EVENTS_DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT method, path, status, COUNT(*) as cnt
            FROM events
            GROUP BY method, path, status
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    trained = 0
    now = int(time.time())

    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        for method, path, status, count in rows:
            pattern = f"{method}:{path}:{status}"
            rule_id = hashlib.sha256(f"endpoint:{pattern}".encode()).hexdigest()
            weight = min(1.0, count / 10.0)
            conn.execute(
                """
                INSERT OR REPLACE INTO rules
                (id, ts, kind, pattern, weight)
                VALUES (?, ?, ?, ?, ?)
                """,
                (rule_id, now, "endpoint", pattern, weight),
            )
            trained += 1
        conn.commit()

    return {
        "status": "ok",
        "trained_rules": trained,
        "events_db": str(EVENTS_DB_PATH),
        "psyche_db": str(PSYCHE_DB_PATH),
    }
