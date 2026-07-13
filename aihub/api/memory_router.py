# LEGACY / UNMOUNTED: not mounted in aihub.main; canonical HTTP surface is aihub.main + aihub/*_api.py. See aihub/api/_LEGACY.md.
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aihub.db.sqlite import _use_postgres, db, init_schema

router = APIRouter(prefix="/memory", tags=["memory"])
init_schema()


class MemoryWriteReq(BaseModel):
    key: Optional[str] = Field(default=None, max_length=200)
    text: str = Field(min_length=1, max_length=50_000)
    meta: Dict[str, Any] = Field(default_factory=dict)


class MemoryWriteResp(BaseModel):
    id: str
    ts: int
    key: Optional[str]
    importance: float


class MemoryGetResp(BaseModel):
    id: str
    ts: int
    key: Optional[str]
    text: str
    meta: Dict[str, Any]
    access_count: int
    last_access_ts: int
    importance: float


class MemorySearchResp(BaseModel):
    items: List[MemoryGetResp]


def _mem_tuple(r: Any) -> tuple[Any, ...]:
    """SQLite Row lub dict (PG) → krotka zgodna z SELECT legacy memory."""
    if isinstance(r, dict):
        return (
            r["id"],
            r["ts"],
            r.get("key"),
            r.get("meta_json"),
            r["text"],
            r["access_count"],
            r["last_access_ts"],
            r["importance"],
        )
    return (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7])


def _row_to_obj(r: Any) -> MemoryGetResp:
    t = _mem_tuple(r)
    meta = {}
    try:
        raw = t[3]
        meta = json.loads(raw) if raw else {}
    except Exception:
        meta = {}
    return MemoryGetResp(
        id=str(t[0]),
        ts=int(t[1]),
        key=t[2],
        text=str(t[4]),
        meta=meta,
        access_count=int(t[5]),
        last_access_ts=int(t[6]),
        importance=float(t[7]),
    )


@router.post("/write", response_model=MemoryWriteResp)
def write(req: MemoryWriteReq):
    now = int(time.time())
    mid = str(uuid.uuid4())
    meta_json = json.dumps(req.meta, ensure_ascii=False)

    if _use_postgres():
        sql = """
        INSERT INTO compat_router.mem(id,ts,key,text,meta_json,access_count,last_access_ts,importance,deleted)
        VALUES(?,?,?,?,?,?,?,?,0)
        """
    else:
        sql = """
        INSERT INTO memory(id,ts,key,text,meta_json,access_count,last_access_ts,importance,deleted)
        VALUES(?,?,?,?,?,?,?,?,0)
        """

    with db() as c:
        c.execute(
            sql,
            (mid, now, req.key, req.text, meta_json, 0, 0, 0.0),
        )

    return MemoryWriteResp(id=mid, ts=now, key=req.key, importance=0.0)


@router.get("/get", response_model=MemoryGetResp)
def get(id: str):
    now = int(time.time())
    if _use_postgres():
        sel = """
        SELECT id,ts,key,meta_json,text,access_count,last_access_ts,importance
        FROM compat_router.mem WHERE id=? AND deleted=0
        """
        upd = """
        UPDATE compat_router.mem AS m SET access_count=m.access_count+1, last_access_ts=? WHERE m.id=?
        """
    else:
        sel = """
        SELECT id,ts,key,meta_json,text,access_count,last_access_ts,importance
        FROM memory WHERE id=? AND deleted=0
        """
        upd = """
        UPDATE memory AS m SET access_count=m.access_count+1, last_access_ts=? WHERE m.id=?
        """

    with db() as c:
        r = c.execute(sel, (id,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="not found")
        c.execute(upd, (now, id))

    return _row_to_obj(r)


@router.get("/search", response_model=MemorySearchResp)
def search(q: str, limit: int = 50):
    limit = max(1, min(int(limit), 200))
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q required")

    if _use_postgres():
        sql = """
        SELECT m.id,m.ts,m.key,m.meta_json,m.text,m.access_count,m.last_access_ts,m.importance
        FROM compat_router.mem m
        WHERE m.deleted=0 AND m.text_tsv @@ plainto_tsquery('simple', ?)
        ORDER BY m.ts DESC
        LIMIT ?
        """
        params = (q, limit)
    else:
        sql = """
        SELECT m.id,m.ts,m.key,m.meta_json,m.text,m.access_count,m.last_access_ts,m.importance
        FROM memory_fts f
        JOIN memory m ON m.rowid=f.rowid
        WHERE memory_fts MATCH ? AND m.deleted=0
        ORDER BY m.ts DESC
        LIMIT ?
        """
        params = (q, limit)

    with db() as c:
        rows = c.execute(sql, params).fetchall()

    return MemorySearchResp(items=[_row_to_obj(r) for r in rows])


@router.post("/delete")
def delete(id: str):
    if _use_postgres():
        sql = "UPDATE compat_router.mem SET deleted=1 WHERE id=?"
    else:
        sql = "UPDATE memory SET deleted=1 WHERE id=?"
    with db() as c:
        c.execute(sql, (id,))
    return {"success": True}


@router.get("/export")
def export(limit: int = 500):
    limit = max(1, min(int(limit), 5000))
    if _use_postgres():
        sql = """
        SELECT id,ts,key,meta_json,text,access_count,last_access_ts,importance
        FROM compat_router.mem WHERE deleted=0
        ORDER BY ts DESC LIMIT ?
        """
    else:
        sql = """
        SELECT id,ts,key,meta_json,text,access_count,last_access_ts,importance
        FROM memory WHERE deleted=0
        ORDER BY ts DESC LIMIT ?
        """
    with db() as c:
        rows = c.execute(sql, (limit,)).fetchall()
    out = []
    for r in rows:
        out.append(_row_to_obj(r).model_dump())
    return {"items": out}
