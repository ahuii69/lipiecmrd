# LEGACY / UNMOUNTED: not mounted in aihub.main; canonical HTTP surface is aihub.main + aihub/*_api.py. See aihub/api/_LEGACY.md.
import json
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aihub.db.sqlite import _use_postgres, db
from aihub.util.token import mint, verify

router = APIRouter(prefix="/sse", tags=["sse"])


class TokenReq(BaseModel):
    scope: str = Field(min_length=1, max_length=50)
    exp_sec: int = Field(default=300, ge=10, le=3600)
    task_id: Optional[str] = Field(default=None, max_length=128)


@router.post("/token")
def token(req: TokenReq):
    payload = {}
    if req.task_id:
        payload["task_id"] = req.task_id
    tok = mint(req.scope, payload, req.exp_sec)
    return {"token": tok, "scope": req.scope, "exp_sec": req.exp_sec}


def _event(data: dict, event: str = "message") -> bytes:
    return (f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n").encode(
        "utf-8"
    )


def _sse_row(r: Any) -> tuple[int, Any, Any, Any, Any]:
    if isinstance(r, dict):
        return (
            int(r["ts"]),
            r["method"],
            r["path"],
            r["status"],
            r["latency_ms"],
        )
    return int(r[0]), r[1], r[2], r[3], r[4]


@router.get("/events")
def events(request: Request, token: str):
    try:
        verify(token, "events")
    except Exception:
        raise HTTPException(status_code=401, detail="bad token")

    async def gen():
        last_ts = int(time.time()) - 60
        yield _event({"ok": True, "ts": int(time.time())}, event="hello")
        while True:
            if await request.is_disconnected():
                break
            rows = []
            if _use_postgres():
                from aihub.db import fetch_all

                rows = fetch_all(
                    """
                SELECT ts,method,path,status,latency_ms
                FROM compat_router.events
                WHERE ts >= ?
                ORDER BY ts DESC
                LIMIT 20
                """,
                    (last_ts,),
                )
            else:
                with db() as c:
                    rows = c.execute(
                        """
                SELECT ts,method,path,status,latency_ms
                FROM events
                WHERE ts >= ?
                ORDER BY ts DESC
                LIMIT 20
                """,
                        (last_ts,),
                    ).fetchall()

            if rows:
                last_ts = max(_sse_row(r)[0] for r in rows)
                for r in rows:
                    t = _sse_row(r)
                    yield _event(
                        {
                            "ts": t[0],
                            "method": t[1],
                            "path": t[2],
                            "status": int(t[3]) if t[3] is not None else 0,
                            "latency_ms": int(t[4]) if t[4] is not None else 0,
                        },
                        event="event",
                    )

            yield _event({"ts": int(time.time())}, event="ping")
            await __import__("asyncio").sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")
