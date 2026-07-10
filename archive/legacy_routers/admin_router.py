"""ARCHIVED — 06.07 repair sprint (P1 security: admin router collision + body leak).

This module is **not part of the ``aihub`` Python package** and is **not importable, mounted,
or reachable from the running application**. Kept here, outside the runtime tree, purely as
historical reference.

Why archived:
- Same ``/admin`` prefix as the canonical, mounted ``aihub.admin_api`` (``GET /admin/ping``) —
  a namespace collision that invited someone to eventually mount both, at which point
  ``GET /admin/events/body?id=...`` would have served **unredacted** base64-encoded HTTP
  request/response bodies straight from the (also-archived / never-registered) event recorder
  middleware table.
- No redaction of secrets/PII in stored bodies; no auth scoping beyond the global hub API key.
- Depended on a DB table (``events`` / ``compat_router.events``) that nothing in the active
  runtime populates, since ``aihub/middleware/recorder.py`` is not registered on ``app``.

Canonical admin surface going forward: ``aihub/admin_api.py`` (``GET /admin/ping``, mounted in
``aihub/main.py``). Do not reintroduce a second ``/admin`` router without merging into that one
module and adding explicit redaction for any endpoint that can return request/response bodies.
"""

import base64

from fastapi import APIRouter

from aihub.db.sqlite import _use_postgres, db

router = APIRouter(prefix="/admin", tags=["admin"])


def _recent_row(r):
    if isinstance(r, dict):
        return (
            int(r["ts"]),
            r["method"],
            r["path"],
            int(r["status"] or 0),
            int(r["latency_ms"] or 0),
            r.get("client_ip"),
            r.get("user_agent"),
        )
    return (
        int(r[0]),
        r[1],
        r[2],
        int(r[3] if r[3] is not None else 0),
        int(r[4] if r[4] is not None else 0),
        r[5],
        r[6],
    )


@router.get("/events/count")
def events_count():
    if _use_postgres():
        from aihub.db import fetch_one

        row = fetch_one("SELECT COUNT(*) AS n FROM compat_router.events", ())
        n = int(row["n"] if isinstance(row, dict) else row[0])
    else:
        with db() as c:
            n = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return {"count": int(n)}


@router.get("/events/recent")
def events_recent(limit: int = 50):
    limit = max(1, min(int(limit), 500))
    if _use_postgres():
        from aihub.db import fetch_all

        rows = fetch_all(
            """
        SELECT ts,method,path,status,latency_ms,client_ip,user_agent
        FROM compat_router.events ORDER BY ts DESC LIMIT ?
        """,
            (limit,),
        )
    else:
        with db() as c:
            rows = c.execute(
                """
        SELECT ts,method,path,status,latency_ms,client_ip,user_agent
        FROM events ORDER BY ts DESC LIMIT ?
        """,
                (limit,),
            ).fetchall()
    items = []
    for r in rows:
        t = _recent_row(r)
        items.append(
            {
                "ts": t[0],
                "method": t[1],
                "path": t[2],
                "status": t[3],
                "latency_ms": t[4],
                "client_ip": t[5],
                "user_agent": t[6],
            }
        )
    return {"items": items}


@router.get("/events/body")
def events_body(id: str):
    if _use_postgres():
        from aihub.db import fetch_one

        r = fetch_one(
            """
        SELECT req_body_b64, resp_body_b64 FROM compat_router.events WHERE id=?
        """,
            (id,),
        )
    else:
        with db() as c:
            r = c.execute(
                """
        SELECT req_body_b64, resp_body_b64 FROM events WHERE id=?
        """,
                (id,),
            ).fetchone()
    if not r:
        return {"found": False}
    if isinstance(r, dict):
        req_b, resp_b = r.get("req_body_b64"), r.get("resp_body_b64")
    else:
        req_b, resp_b = r[0], r[1]
    return {
        "found": True,
        "req_body_base64": req_b or "",
        "resp_body_base64": resp_b or "",
    }
