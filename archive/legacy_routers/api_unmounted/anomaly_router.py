# LEGACY / UNMOUNTED: not mounted in aihub.main; canonical HTTP surface is aihub.main + aihub/*_api.py. See aihub/api/_LEGACY.md.
from fastapi import APIRouter

from aihub.sidecar_db import (
    PSYCHE_DB_PATH,
    anomalies_list_pg,
    anomalies_list_sqlite,
    is_postgres,
)

router = APIRouter(prefix="/psyche", tags=["psyche"])


@router.get("/anomalies")
def anomalies(limit: int = 50):
    if is_postgres():
        rows = anomalies_list_pg(limit)
    else:
        if not PSYCHE_DB_PATH.exists():
            return {"status": "ok", "anomalies": []}
        rows = anomalies_list_sqlite(limit)

    return {
        "status": "ok",
        "count": len(rows),
        "anomalies": [
            {
                "ts": r[0],
                "method": r[1],
                "path": r[2],
                "status": r[3],
                "expected": r[4],
                "confidence": round(r[5], 3),
            }
            for r in rows
        ],
    }
