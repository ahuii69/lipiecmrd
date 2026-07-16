from __future__ import annotations

import logging
import os
import threading
import time
from typing import Iterable

from aihub.core.config import settings
from aihub.memory_core import get_memory_core

log = logging.getLogger("aihub.consolidation")

_stop = threading.Event()
_thread: threading.Thread | None = None


def _users_from_env() -> list[str]:
    # Explicit opt-in only — do not silently maintain the legacy "default" namespace.
    raw = os.getenv("AIHUB_CONSOLIDATION_USERS", "").strip()
    return [part.strip() for part in raw.split(",") if part.strip()]


def _suppress_threshold() -> float:
    raw = os.getenv("AIHUB_MEMORY_V2_SUPPRESS_THRESHOLD", "0.12")
    try:
        value = float(raw)
    except ValueError:
        log.warning(
            "Invalid AIHUB_MEMORY_V2_SUPPRESS_THRESHOLD=%r; using 0.12", raw
        )
        return 0.12
    return min(1.0, max(0.0, value))


def _retention_days(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def run_event_retention_once() -> dict[str, object]:
    """Delete aged rows from high-growth event tables (config days).

    ENV:
      AIHUB_RETENTION_EVENT_LOG_DAYS (default 30)
      AIHUB_RETENTION_PSYCHE_EVENTS_DAYS (default 30)
      AIHUB_RETENTION_ENABLED (default 1)
    """
    if os.getenv("AIHUB_RETENTION_ENABLED", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return {"enabled": False}
    event_days = _retention_days("AIHUB_RETENTION_EVENT_LOG_DAYS", 30)
    psyche_days = _retention_days("AIHUB_RETENTION_PSYCHE_EVENTS_DAYS", 30)
    out: dict[str, object] = {
        "enabled": True,
        "event_log_days": event_days,
        "psyche_days": psyche_days,
    }
    try:
        from aihub.db.runtime import _DB_LOCK, _conn

        cutoff_event = time.time() - event_days * 86400.0
        cutoff_psyche = time.time() - psyche_days * 86400.0
        with _DB_LOCK, _conn() as con:
            cur = con.execute(
                "DELETE FROM event_log WHERE ts < ?",
                (cutoff_event,),
            )
            out["event_log_deleted"] = int(getattr(cur, "rowcount", 0) or 0)
            cur2 = con.execute(
                "DELETE FROM psyche_v2_events WHERE created_ts < ?",
                (cutoff_psyche,),
            )
            out["psyche_v2_events_deleted"] = int(getattr(cur2, "rowcount", 0) or 0)
            con.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("event retention failed: %s", exc, exc_info=True)
        out["error"] = str(exc)
    return out


def _run_once(users: Iterable[str] | None = None) -> dict[str, dict[str, object]]:
    """Run canonical Memory V2 maintenance once for each configured user."""
    core = get_memory_core()
    threshold = _suppress_threshold()
    out: dict[str, dict[str, object]] = {}
    for user_id in list(users or _users_from_env()):
        try:
            consolidation = core.v2_consolidate_user_memory(user_id)
            forgetting = core.v2_run_forgetting_sweep(user_id, threshold)
            decay_updated = 0
            try:
                from aihub.memory_v2_decay import run_decay_pass

                decay_updated = int(run_decay_pass(user_id) or 0)
            except Exception as decay_exc:  # noqa: BLE001
                log.debug(
                    "Memory V2 decay pass failed for user_id=%s: %s",
                    user_id,
                    decay_exc,
                    exc_info=True,
                )
            procedures_extracted = 0
            try:
                procs = core.v2_extract_procedures(user_id)
                procedures_extracted = len(procs or [])
            except Exception as proc_exc:  # noqa: BLE001
                log.debug(
                    "Memory V2 procedural extraction failed for user_id=%s: %s",
                    user_id,
                    proc_exc,
                    exc_info=True,
                )
            out[user_id] = {
                "consolidation": consolidation,
                "forgetting": forgetting,
                "decay_updated": decay_updated,
                "procedures_extracted": procedures_extracted,
                "suppress_threshold": threshold,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Memory V2 maintenance failed for user_id=%s: %s",
                user_id,
                exc,
                exc_info=True,
            )
            out[user_id] = {"error": str(exc), "suppress_threshold": threshold}
    try:
        out["_retention"] = run_event_retention_once()  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        out["_retention"] = {"error": str(exc)}  # type: ignore[assignment]
    return out


def _run() -> None:
    log.info("Memory V2 consolidation + retention worker started")
    interval = max(5, int(settings.consolidate_every_sec))
    while not _stop.is_set():
        _run_once()
        _stop.wait(interval)


def start_background() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(
        target=_run, name="aihub-memory-v2-consolidation", daemon=True
    )
    _thread.start()


def stop_background() -> None:
    _stop.set()
    global _thread
    t = _thread
    if t is not None and t.is_alive():
        t.join(timeout=10.0)
        if t.is_alive():
            raise RuntimeError(
                "Memory V2 consolidation worker did not stop within timeout"
            )
    _thread = None
