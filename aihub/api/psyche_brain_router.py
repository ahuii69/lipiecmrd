# LEGACY / UNMOUNTED: not mounted in aihub.main; canonical HTTP surface is aihub.main + aihub/*_api.py. See aihub/api/_LEGACY.md.
import sqlite3
import time

from fastapi import APIRouter

from aihub.db import fetch_all
from aihub.sidecar_db import (
    EVENTS_DB_PATH,
    PSYCHE_DB_PATH,
    http_events_count,
    is_postgres,
    psyche_rules_count,
)

router = APIRouter(prefix="/psyche", tags=["psyche"])


def load_rules():
    if is_postgres():
        rows = fetch_all(
            "SELECT pattern, weight, ts FROM sidecar.psyche_rules ORDER BY weight DESC",
            (),
        )
        out = []
        for r in rows:
            pattern = r["pattern"]
            weight = float(r["weight"])
            ts = r["ts"]
            try:
                parts = pattern.split(":")
                method = parts[0]
                path = parts[1]
                status = int(parts[2]) if len(parts) > 2 else None
            except Exception:
                continue
            out.append(
                {
                    "method": method,
                    "path": path,
                    "expected_status": status,
                    "confidence": round(weight, 3),
                    "last_seen": ts,
                }
            )
        return out
    if not PSYCHE_DB_PATH.exists():
        return []
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT pattern, weight, ts
            FROM rules
            ORDER BY weight DESC
        """
        ).fetchall()
    rules = []
    for pattern, weight, ts in rows:
        try:
            parts = pattern.split(":")
            method = parts[0]
            path = parts[1]
            status = int(parts[2]) if len(parts) > 2 else None
        except Exception:
            continue
        rules.append(
            {
                "method": method,
                "path": path,
                "expected_status": status,
                "confidence": round(float(weight), 3),
                "last_seen": ts,
            }
        )
    return rules


def load_stats():
    if is_postgres():
        return {"events": http_events_count(), "rules": psyche_rules_count()}
    events = 0
    rules = 0
    if EVENTS_DB_PATH.exists():
        with sqlite3.connect(str(EVENTS_DB_PATH)) as conn:
            events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    if PSYCHE_DB_PATH.exists():
        with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
            rules = conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
    return {"events": events, "rules": rules}


@router.get("/brain")
def brain():
    stats = load_stats()
    rules = load_rules()
    return {
        "status": "ok",
        "timestamp": int(time.time()),
        "stats": stats,
        "patterns": rules,
    }
