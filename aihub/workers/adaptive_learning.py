import hashlib
import logging
import sqlite3
import threading
import time

from aihub.sidecar_db import (
    EVENTS_DB_PATH,
    PSYCHE_DB_PATH,
    ensure_psyche_rules_schema_sqlite,
    http_events_select_for_train_pg,
    http_events_select_for_train_sqlite,
    is_postgres,
    psyche_get_weight_pg,
    psyche_get_weight_sqlite,
    psyche_rules_decay_weights_pg,
    psyche_insert_rule_sqlite,
    psyche_update_rule_weight_pg,
    psyche_update_rule_weight_sqlite,
    psyche_upsert_rule_pg,
)

log = logging.getLogger("aihub.adaptive")

INTERVAL = 60
DECAY = 0.995
BOOST = 0.05
MAX_WEIGHT = 5.0


def ensure_schema() -> None:
    if not is_postgres():
        ensure_psyche_rules_schema_sqlite()


def boost_rule(pattern: str) -> None:
    rule_id = hashlib.sha256(pattern.encode()).hexdigest()
    ts = int(time.time())
    if is_postgres():
        row = psyche_get_weight_pg(rule_id)
        if row:
            w = min(float(row["weight"]) + BOOST, MAX_WEIGHT)
            psyche_update_rule_weight_pg(rule_id, w, ts)
        else:
            psyche_upsert_rule_pg(rule_id, ts, "endpoint", pattern, 1.0)
        return
    row = psyche_get_weight_sqlite(rule_id)
    if row:
        w = min(float(row[0]) + BOOST, MAX_WEIGHT)
        psyche_update_rule_weight_sqlite(rule_id, w, ts)
    else:
        psyche_insert_rule_sqlite(rule_id, ts, "endpoint", pattern, 1.0)


def boost_rule_sqlite_conn(conn: sqlite3.Connection, pattern: str) -> None:
    rule_id = hashlib.sha256(pattern.encode()).hexdigest()
    row = conn.execute("SELECT weight FROM rules WHERE id=?", (rule_id,)).fetchone()
    ts = int(time.time())
    if row:
        w = min(row[0] + BOOST, MAX_WEIGHT)
        conn.execute(
            "UPDATE rules SET weight=?, ts=? WHERE id=?",
            (w, ts, rule_id),
        )
    else:
        conn.execute(
            "INSERT INTO rules VALUES(?,?,?,?,?)",
            (rule_id, ts, "endpoint", pattern, 1.0),
        )


def train_from_events() -> int:
    ensure_schema()
    if is_postgres():
        psyche_rules_decay_weights_pg(DECAY)
        rows = http_events_select_for_train_pg(100)
        for method, path, status in rows:
            boost_rule(f"{method}:{path}:{status}")
        return len(rows)

    if not EVENTS_DB_PATH.exists():
        return 0

    with sqlite3.connect(str(EVENTS_DB_PATH)) as events_conn, sqlite3.connect(
        str(PSYCHE_DB_PATH)
    ) as psyche_conn:
        psyche_conn.execute("UPDATE rules SET weight = weight * ?", (DECAY,))
        rows = events_conn.execute(
            """
            SELECT method, path, status
            FROM events
            ORDER BY ts DESC
            LIMIT 100
            """,
        ).fetchall()
        for method, path, status in rows:
            boost_rule_sqlite_conn(psyche_conn, f"{method}:{path}:{status}")
        psyche_conn.commit()
    return len(rows)


def worker() -> None:
    log.info("adaptive learning worker started interval=%s", INTERVAL)
    ensure_schema()
    while True:
        try:
            n = train_from_events()
            log.info("adaptive learning trained %s patterns", n)
        except Exception as e:
            log.exception("adaptive learning error: %s", e)
        time.sleep(INTERVAL)


def start() -> None:
    thread = threading.Thread(
        target=worker,
        daemon=True,
        name="adaptive-learning",
    )
    thread.start()
    log.info("adaptive learning thread started")
