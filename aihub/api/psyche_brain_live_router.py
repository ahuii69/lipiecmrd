# LEGACY / UNMOUNTED: not mounted in aihub.main; canonical HTTP surface is aihub.main + aihub/*_api.py. See aihub/api/_LEGACY.md.
import asyncio
import json
import sqlite3
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from aihub.db import fetch_one
from aihub.sidecar_db import EVENTS_DB_PATH, is_postgres, psyche_rules_like

router = APIRouter(prefix="/psyche", tags=["psyche"])

DEFAULT_TAIL_N = 10
DEFAULT_POLL_SEC = 1.0
DEFAULT_HEARTBEAT_SEC = 10.0

DEFAULT_IGNORE_PREFIXES = (
    "/psyche/brain/live",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/health",
)


def _event_count() -> int:
    if is_postgres():
        r = fetch_one("SELECT COUNT(*)::bigint AS c FROM sidecar.http_events", ())
        return int(r["c"]) if r else 0
    if not EVENTS_DB_PATH.exists():
        return 0
    with sqlite3.connect(str(EVENTS_DB_PATH)) as conn:
        row = conn.execute("SELECT COUNT(*) FROM events").fetchone()
    return int(row[0]) if row else 0


def get_last_event_id() -> int:
    return _event_count()


def load_event(rowid: int) -> Optional[Dict[str, Any]]:
    if is_postgres():
        if rowid < 1:
            return None
        row = fetch_one(
            """
            SELECT id, ts, method, path, status
            FROM sidecar.http_events
            ORDER BY ts ASC, id ASC
            OFFSET ? LIMIT 1
            """,
            (rowid - 1,),
        )
        if not row:
            return None
        return {
            "id": rowid,
            "ts": int(row["ts"]),
            "method": str(row["method"]),
            "path": str(row["path"]),
            "status": int(row["status"]),
        }
    if not EVENTS_DB_PATH.exists():
        return None
    with sqlite3.connect(str(EVENTS_DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT rowid, ts, method, path, status
            FROM events
            WHERE rowid = ?
            """,
            (rowid,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": int(row[0]),
        "ts": int(row[1]),
        "method": str(row[2]),
        "path": str(row[3]),
        "status": int(row[4]),
    }


def predict_confidence(method: str, path: str, status: int) -> float:
    pattern_prefix = f"{method}:{path}"
    rows = psyche_rules_like(pattern_prefix + "%")
    total = 0.0
    match = 0.0
    for pattern, weight in rows:
        try:
            parts = str(pattern).split(":")
            if len(parts) >= 3 and int(parts[2]) == int(status):
                match += float(weight)
            total += float(weight)
        except Exception:
            continue
    if total <= 0:
        return 0.0
    return round(match / total, 3)


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _should_ignore(path: str, ignore_prefixes: tuple[str, ...]) -> bool:
    for p in ignore_prefixes:
        if path.startswith(p):
            return True
    return False


async def brain_stream(
    request: Request,
    tail_n: int,
    poll_sec: float,
    heartbeat_sec: float,
    include_ignored: bool,
):
    ignore_prefixes = () if include_ignored else DEFAULT_IGNORE_PREFIXES

    last_id = get_last_event_id()
    start_id = max(0, last_id - max(0, int(tail_n)))

    for i in range(start_id + 1, last_id + 1):
        if await request.is_disconnected():
            return
        event = load_event(i)
        if not event:
            continue
        if _should_ignore(event["path"], ignore_prefixes):
            continue
        confidence = predict_confidence(event["method"], event["path"], event["status"])
        payload = {
            "ts": event["ts"],
            "method": event["method"],
            "path": event["path"],
            "status": event["status"],
            "confidence": confidence,
            "anomaly": confidence < 0.7,
            "mode": "tail",
        }
        yield _sse_data(payload)

    last_heartbeat = time.time()
    last_id_seen = last_id

    while True:
        if await request.is_disconnected():
            break
        new_id = get_last_event_id()
        if new_id > last_id_seen:
            for i in range(last_id_seen + 1, new_id + 1):
                if await request.is_disconnected():
                    return
                event = load_event(i)
                if not event:
                    continue
                if _should_ignore(event["path"], ignore_prefixes):
                    continue
                confidence = predict_confidence(
                    event["method"], event["path"], event["status"]
                )
                payload = {
                    "ts": event["ts"],
                    "method": event["method"],
                    "path": event["path"],
                    "status": event["status"],
                    "confidence": confidence,
                    "anomaly": confidence < 0.7,
                    "mode": "live",
                }
                yield _sse_data(payload)
            last_id_seen = new_id
        now = time.time()
        if now - last_heartbeat >= heartbeat_sec:
            yield f": heartbeat {int(now)}\n\n"
            last_heartbeat = now
        await asyncio.sleep(poll_sec)


@router.get("/brain/live")
async def brain_live(
    request: Request,
    tail: int = Query(DEFAULT_TAIL_N, ge=0, le=500),
    poll: float = Query(DEFAULT_POLL_SEC, ge=0.05, le=10.0),
    heartbeat: float = Query(DEFAULT_HEARTBEAT_SEC, ge=1.0, le=120.0),
    include_ignored: bool = Query(False),
):
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    gen = brain_stream(
        request=request,
        tail_n=tail,
        poll_sec=poll,
        heartbeat_sec=heartbeat,
        include_ignored=include_ignored,
    )
    return StreamingResponse(gen, media_type="text/event-stream", headers=headers)
