# LEGACY / UNMOUNTED: not mounted in aihub.main; canonical HTTP surface is aihub.main + aihub/*_api.py. See aihub/api/_LEGACY.md.
import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter

from aihub.config import DATA_DIR
from aihub.sidecar_db import (
    EVENTS_DB_PATH,
    PSYCHE_DB_PATH,
    http_events_count,
    http_events_group_count,
    is_postgres,
    psyche_rules_count,
)

log = logging.getLogger("aihub.memory.stats")

router = APIRouter(prefix="/memory", tags=["memory"])


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    except Exception:
        return False


def safe_count(db_path: Path, table: str) -> int:
    try:
        if not db_path.exists():
            return 0

        with sqlite3.connect(str(db_path)) as conn:

            if not table_exists(conn, table):
                return 0

            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()

            return int(row[0]) if row else 0

    except Exception as e:
        log.error("safe_count failed: %s", e)
        return 0


def top_endpoints(limit: int = 10):
    try:
        return [
            {"method": r[0], "path": r[1], "count": r[2]}
            for r in http_events_group_count(limit)
        ]
    except Exception as e:
        log.error("top_endpoints failed: %s", e)
        return []


@router.get("/stats")
def memory_stats():
    if is_postgres():
        events = http_events_count()
        rules = psyche_rules_count()
        ev_path = "postgresql:sidecar.http_events"
        ps_path = "postgresql:sidecar.psyche_rules"
    else:
        events = safe_count(EVENTS_DB_PATH, "events")
        rules = safe_count(PSYCHE_DB_PATH, "rules")
        ev_path = str(EVENTS_DB_PATH)
        ps_path = str(PSYCHE_DB_PATH)

    return {
        "status": "ok",
        "events": events,
        "rules": rules,
        "top_endpoints": top_endpoints(),
        "events_db": ev_path,
        "psyche_db": ps_path,
    }
