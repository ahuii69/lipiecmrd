from __future__ import annotations

import logging
import os

log = logging.getLogger("aihub.background")


def _env_bool(name: str, default: bool = True) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def start_background() -> None:
    """Uruchamia wątki pomocnicze Memory V2."""
    if not _env_bool("AIHUB_CONSOLIDATION_WORKER", True):
        log.info("background: konsolidacja wyłączona (AIHUB_CONSOLIDATION_WORKER=0)")
        return
    try:
        from aihub.workers.consolidation import start_background as start_consolidation

        start_consolidation()
        log.info("background: worker konsolidacji Memory V2 uruchomiony")
    except Exception as e:  # noqa: BLE001
        log.warning("background: konsolidacja nie wystartowała: %s", e, exc_info=True)
