import logging
import threading
import time

from aihub.sidecar_db import (
    EVENTS_DB_PATH,
    PSYCHE_DB_PATH,
    http_events_select_recent_pg,
    http_events_select_recent_sqlite,
    is_postgres,
    psyche_heal_insert_pg,
    psyche_heal_insert_sqlite,
    psyche_rules_like,
)

log = logging.getLogger("aihub.nervous")

CHECK_INTERVAL = 5
ANOMALY_THRESHOLD = 0.4
BLOCK_THRESHOLD = 0.2

blocked: set[str] = set()


def get_recent_events(limit: int = 50):
    if is_postgres():
        return http_events_select_recent_pg(limit)
    if not EVENTS_DB_PATH.exists():
        return []
    return http_events_select_recent_sqlite(limit)


def confidence(method: str, path: str, status) -> float:
    like_arg = f"{method}:{path}%"
    rows = psyche_rules_like(like_arg)
    total = 0.0
    match = 0.0
    for p, w in rows:
        total += w
        if str(p).endswith(f":{status}"):
            match += w
    if total == 0:
        return 0.0
    return match / total


def detect_anomalies():
    anomalies = []
    for method, path, status, _ts in get_recent_events():
        conf = confidence(method, path, status)
        if conf < ANOMALY_THRESHOLD:
            anomalies.append(
                {
                    "method": method,
                    "path": path,
                    "status": status,
                    "confidence": conf,
                }
            )
            if conf < BLOCK_THRESHOLD:
                blocked.add(str(path))
    return anomalies


def heal() -> int:
    healed = 0
    for path in list(blocked):
        rid = f"heal:{path}"
        ts = int(time.time())
        if is_postgres():
            psyche_heal_insert_pg(rid, ts, "heal", f"GET:{path}:200", 2.0)
        else:
            psyche_heal_insert_sqlite(rid, ts, "heal", f"GET:{path}:200", 2.0)
        healed += 1
    return healed


def nervous_loop() -> None:
    while True:
        anomalies = detect_anomalies()
        if anomalies:
            log.warning("anomalies detected: %d", len(anomalies))
        healed = heal()
        if healed:
            log.info("self-healed endpoints: %d", healed)
        time.sleep(CHECK_INTERVAL)


def start() -> None:
    t = threading.Thread(target=nervous_loop, daemon=True)
    t.start()
    log.info("nervous system online")
