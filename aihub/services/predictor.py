from aihub.sidecar_db import http_events_failure_stats, is_postgres


def predict_failure():
    if is_postgres():
        rows = http_events_failure_stats()
    else:
        import sqlite3
        from pathlib import Path

        from aihub.config import DATA_DIR

        events_db = DATA_DIR / "events.db"
        if not events_db.exists():
            return []
        with sqlite3.connect(str(events_db)) as conn:
            rows = conn.execute(
                """
                SELECT path,
                       SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END) as errors,
                       COUNT(*) as total
                FROM events
                GROUP BY path
                """
            ).fetchall()

    predictions = []
    for path, errors, total in rows:
        if total == 0:
            continue
        rate = errors / total
        if rate > 0.3:
            predictions.append(
                {"path": path, "failure_probability": round(rate, 3)}
            )
    return predictions
