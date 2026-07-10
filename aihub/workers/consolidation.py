from __future__ import annotations

import logging
import os
import threading
from typing import Iterable

from aihub.core.config import settings
from aihub.memory_core import get_memory_core

log = logging.getLogger("aihub.consolidation")

_stop = threading.Event()
_thread: threading.Thread | None = None


def _users_from_env() -> list[str]:
    raw = os.getenv("AIHUB_CONSOLIDATION_USERS", "default")
    users = [part.strip() for part in raw.split(",") if part.strip()]
    return users or ["default"]


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


def _run_once(users: Iterable[str] | None = None) -> dict[str, dict[str, object]]:
    """Run canonical Memory V2 maintenance once for each configured user."""
    core = get_memory_core()
    threshold = _suppress_threshold()
    out: dict[str, dict[str, object]] = {}
    for user_id in list(users or _users_from_env()):
        try:
            consolidation = core.v2_consolidate_user_memory(user_id)
            forgetting = core.v2_run_forgetting_sweep(user_id, threshold)
            out[user_id] = {
                "consolidation": consolidation,
                "forgetting": forgetting,
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
    return out


def _run() -> None:
    log.info("Memory V2 consolidation worker started")
    interval = max(5, int(settings.consolidate_every_sec))
    while not _stop.is_set():
        _run_once()
        _stop.wait(interval)


def start_background() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="aihub-memory-v2-consolidation", daemon=True)
    _thread.start()


def stop_background() -> None:
    _stop.set()
