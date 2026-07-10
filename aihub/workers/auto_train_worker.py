import hashlib
import logging
import sqlite3
import threading
import time

from aihub.config import DATA_DIR
from aihub.sidecar_db import (
    EVENTS_DB_PATH,
    PSYCHE_DB_PATH,
    ensure_psyche_rules_schema_sqlite,
    is_postgres,
    psyche_insert_ignore_pg,
)

log = logging.getLogger("aihub.auto_train")

EVENTS_DB = DATA_DIR / "events.db"
PSYCHE_DB = DATA_DIR / "psyche.db"

INTERVAL = 60


def train_once(limit: int = 1000) -> int:
    if is_postgres():
        from aihub.db import fetch_all

        rows = fetch_all(
            """
            SELECT method, path, status FROM sidecar.http_events
            ORDER BY ts DESC LIMIT ?
            """,
            (limit,),
        )
        if not rows:
            return 0
        count = 0
        for r in rows:
            method, path, status = r["method"], r["path"], r["status"]
            pattern = f"{method}:{path}:{status}"
            rule_id = hashlib.sha256(pattern.encode()).hexdigest()
            psyche_insert_ignore_pg(
                rule_id,
                int(time.time()),
                "endpoint",
                pattern,
                1.0,
            )
            count += 1
        return count

    if not EVENTS_DB.exists():
        return 0
    ensure_psyche_rules_schema_sqlite()
    with sqlite3.connect(str(EVENTS_DB)) as conn:
        rows = conn.execute(
            """
            SELECT method, path, status
            FROM events
            ORDER BY ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    if not rows:
        return 0
    count = 0
    with sqlite3.connect(str(PSYCHE_DB)) as conn:
        for method, path, status in rows:
            pattern = f"{method}:{path}:{status}"
            rule_id = hashlib.sha256(pattern.encode()).hexdigest()
            conn.execute(
                """
                INSERT OR IGNORE INTO rules
                (id, ts, kind, pattern, weight)
                VALUES (?, ?, ?, ?, ?)
                """,
                (rule_id, int(time.time()), "endpoint", pattern, 1.0),
            )
            count += 1
        conn.commit()
    return count


def loop() -> None:
    log.info("auto-train worker started interval=%s", INTERVAL)
    while True:
        try:
            trained = train_once()
            if trained:
                log.info("auto-train added %s rules", trained)
        except Exception as e:
            log.error("auto-train error: %s", e)
        time.sleep(INTERVAL)


def start() -> None:
    t = threading.Thread(target=loop, daemon=True, name="auto-train")
    t.start()
