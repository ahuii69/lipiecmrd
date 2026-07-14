import time
import threading
import logging

from aihub.sidecar_db import (
    EVENTS_DB_PATH,
    anomalies_insert_pg,
    anomalies_insert_sqlite,
    anomalies_ensure_schema_sqlite,
    http_events_select_recent_pg,
    http_events_select_recent_sqlite,
    is_postgres,
    psyche_rules_like,
)

log = logging.getLogger("aihub.anomaly")

INTERVAL = 30
CONF_THRESHOLD = 0.7


def ensure_schema() -> None:
    if not is_postgres():
        anomalies_ensure_schema_sqlite()


def get_expected(method: str, path: str):
    pattern = f"{method}:{path}"
    rows = psyche_rules_like(pattern + ":%")
    if not rows:
        return None, 0.0
    total = sum(r[1] for r in rows)
    best = max(rows, key=lambda x: x[1])
    expected_status = int(str(best[0]).split(":")[-1])
    confidence = best[1] / total if total else 0.0
    return expected_status, confidence


def detect() -> int:
    if is_postgres():
        rows = http_events_select_recent_pg(50)
    else:
        if not EVENTS_DB_PATH.exists():
            return 0
        rows = http_events_select_recent_sqlite(50)

    count = 0
    for method, path, status, _ts in rows:
        expected, confidence = get_expected(method, path)
        if expected is None:
            continue
        if status != expected or confidence < CONF_THRESHOLD:
            ts = int(time.time())
            if is_postgres():
                anomalies_insert_pg(ts, method, path, status, expected, confidence)
            else:
                anomalies_insert_sqlite(ts, method, path, status, expected, confidence)
            count += 1
    return count


def worker() -> None:
    log.info("anomaly detector started interval=%s", INTERVAL)
    ensure_schema()
    while True:
        try:
            n = detect()
            if n > 0:
                log.warning("anomalies detected: %s", n)
        except Exception as e:
            log.exception("anomaly detector error: %s", e)
        time.sleep(INTERVAL)


def start() -> None:
    t = threading.Thread(
        target=worker,
        daemon=True,
        name="anomaly-detector",
    )
    t.start()
    log.info("anomaly detector thread started")
