"""ACTIVE_CONFIRMED: mounted in :mod:`aihub.main` — ``app.include_router(self_heal_status_router)``.

Prefix ``/system/self-heal-db/*``. Canonical routes: :mod:`aihub.canonical_http_surface`.
"""

from __future__ import annotations

from fastapi import APIRouter

from aihub.config import SELF_HEAL_DB_PATH
from aihub.sidecar_db import healed_recent_pg, healed_recent_sqlite, is_postgres

router = APIRouter(prefix="/system/self-heal-db", tags=["self-heal"])

DB = SELF_HEAL_DB_PATH


@router.get("/status", operation_id="self_heal_db_status")
def status_db():
    if is_postgres():
        healed = healed_recent_pg(50)
    else:
        healed = healed_recent_sqlite(DB, 50)

    return {
        "status": "ok",
        "healed_count": len(healed),
        "recent": [{"path": p, "ts": ts} for p, ts in healed],
    }
