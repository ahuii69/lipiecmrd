from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from aihub.core.config import settings
from aihub.db import _db_backend, exec_one, fetch_all, fetch_one
from aihub.db.database import audit, db, now_ts
from aihub.memory.embedder import embed_one, embed_texts


def _T(name: str) -> str:
    """PostgreSQL: tabele w schemacie legacy_ui; SQLite: krótkie nazwy w pliku."""
    return f"legacy_ui.{name}" if _db_backend() == "postgres" else name


def _vec_to_blob(v: np.ndarray) -> bytes:
    v = np.asarray(v, dtype=np.float32)
    return v.tobytes()


def _blob_to_vec(b: bytes, dim: int) -> np.ndarray:
    v = np.frombuffer(b, dtype=np.float32)
    if v.size != dim:
        return v.astype(np.float32, copy=False)
    return v


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _importance_decay(importance: float, age_sec: int) -> float:
    half_life = max(1, settings.decay_half_life_hours) * 3600
    return float(importance * (0.5 ** (age_sec / half_life)))


def _score(importance: float, sim: float, recency_sec: int, access_count: int) -> float:
    imp = _importance_decay(importance, recency_sec)
    acc = math.log1p(max(0, access_count)) * 0.1
    return (sim * 1.3) + (imp * 0.9) + acc


@dataclass
class MemoryItem:
    id: str
    kind: str
    text: str
    meta: Dict[str, Any]
    importance: float
    created: int
    updated: int
    last_access: int
    access_count: int
    source: str


def _row_to_item(row: Any) -> MemoryItem:
    meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
    return MemoryItem(
        id=str(row["id"]),
        kind=str(row["kind"]),
        text=str(row["text"]),
        meta=meta,
        importance=float(row["importance"]),
        created=int(row["created"]),
        updated=int(row["updated"]),
        last_access=int(row["last_access"]),
        access_count=int(row["access_count"]),
        source=str(row["source"]),
    )


def create_memory(
    text: str,
    meta: Dict[str, Any] | None = None,
    kind: str = "stm",
    importance: float = 0.5,
    source: str = "api",
) -> MemoryItem:
    if not text or not text.strip():
        raise ValueError("text empty")
    kind = kind.lower().strip()
    if kind not in ("stm", "ltm"):
        kind = "stm"
    importance = float(max(0.0, min(1.0, importance)))
    meta = meta or {}

    mid = str(uuid.uuid4())
    ts = now_ts()
    meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))

    v = embed_one(text)
    dim = int(v.size)
    tm = _T("memory")
    tv = _T("memory_vec")
    tf = _T("memory_fts")

    if _db_backend() == "postgres":
        exec_one(
            f"INSERT INTO {tm}(id,kind,text,meta_json,importance,created,updated,last_access,access_count,source) "
            f"VALUES(?,?,?,?,?,?,?,?,?,?)",
            (mid, kind, text, meta_json, importance, ts, ts, ts, 0, source),
        )
        exec_one(
            f"INSERT INTO {tv}(memory_id,dim,vec) VALUES(?,?,?)",
            (mid, dim, _vec_to_blob(v)),
        )
        exec_one(
            f"INSERT INTO {tf}(memory_id,text) VALUES(?,?)",
            (mid, text),
        )
    else:
        c = db()
        c.execute(
            "INSERT INTO memory(id,kind,text,meta_json,importance,created,updated,last_access,access_count,source) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (mid, kind, text, meta_json, importance, ts, ts, ts, 0, source),
        )
        c.execute(
            "INSERT INTO memory_vec(memory_id,dim,vec) VALUES(?,?,?)",
            (mid, dim, _vec_to_blob(v)),
        )
        c.execute("INSERT INTO memory_fts(memory_id,text) VALUES(?,?)", (mid, text))
        c.commit()

    audit("system", "memory.create", {"id": mid, "kind": kind, "importance": importance, "source": source})
    return get_memory(mid)


def get_memory(mid: str) -> MemoryItem:
    tm = _T("memory")
    if _db_backend() == "postgres":
        row = fetch_one(f"SELECT * FROM {tm} WHERE id=?", (mid,))
    else:
        c = db()
        row = c.execute("SELECT * FROM memory WHERE id=?", (mid,)).fetchone()
    if row is None:
        raise KeyError("not found")
    return _row_to_item(row)


def touch(mid: str) -> None:
    ts = now_ts()
    tm = _T("memory")
    if _db_backend() == "postgres":
        exec_one(
            f"UPDATE {tm} AS m SET last_access=?, access_count=m.access_count+1 WHERE m.id=?",
            (ts, mid),
        )
    else:
        c = db()
        c.execute(
            "UPDATE memory AS m SET last_access=?, access_count=m.access_count+1 WHERE m.id=?",
            (ts, mid),
        )
        c.commit()


def update_memory(
    mid: str,
    text: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    importance: Optional[float] = None,
    kind: Optional[str] = None,
) -> MemoryItem:
    tm = _T("memory")
    tv = _T("memory_vec")
    tf = _T("memory_fts")
    if _db_backend() == "postgres":
        row = fetch_one(f"SELECT * FROM {tm} WHERE id=?", (mid,))
    else:
        c = db()
        row = c.execute("SELECT * FROM memory WHERE id=?", (mid,)).fetchone()
    if row is None:
        raise KeyError("not found")

    new_text = text if text is not None else str(row["text"])
    new_meta = meta if meta is not None else (json.loads(row["meta_json"]) if row["meta_json"] else {})
    new_importance = (
        float(row["importance"])
        if importance is None
        else float(max(0.0, min(1.0, float(importance))))
    )
    new_kind = str(row["kind"]) if kind is None else kind.lower().strip()
    if new_kind not in ("stm", "ltm"):
        new_kind = "stm"

    ts = now_ts()
    meta_json = json.dumps(new_meta, ensure_ascii=False, separators=(",", ":"))

    if _db_backend() == "postgres":
        exec_one(
            f"UPDATE {tm} SET kind=?, text=?, meta_json=?, importance=?, updated=? WHERE id=?",
            (new_kind, new_text, meta_json, new_importance, ts, mid),
        )
        if text is not None:
            v = embed_one(new_text)
            exec_one(
                f"UPDATE {tv} SET dim=?, vec=? WHERE memory_id=?",
                (int(v.size), _vec_to_blob(v), mid),
            )
            exec_one(f"DELETE FROM {tf} WHERE memory_id=?", (mid,))
            exec_one(f"INSERT INTO {tf}(memory_id,text) VALUES(?,?)", (mid, new_text))
    else:
        c = db()
        c.execute(
            "UPDATE memory SET kind=?, text=?, meta_json=?, importance=?, updated=? WHERE id=?",
            (new_kind, new_text, meta_json, new_importance, ts, mid),
        )
        if text is not None:
            v = embed_one(new_text)
            c.execute(
                "UPDATE memory_vec SET dim=?, vec=? WHERE memory_id=?",
                (int(v.size), _vec_to_blob(v), mid),
            )
            c.execute("DELETE FROM memory_fts WHERE memory_id=?", (mid,))
            c.execute("INSERT INTO memory_fts(memory_id,text) VALUES(?,?)", (mid, new_text))
        c.commit()
    audit("system", "memory.update", {"id": mid})
    return get_memory(mid)


def delete_memory(mid: str) -> None:
    tm = _T("memory")
    if _db_backend() == "postgres":
        exec_one(f"DELETE FROM {tm} WHERE id=?", (mid,))
    else:
        c = db()
        c.execute("DELETE FROM memory WHERE id=?", (mid,))
        c.execute("DELETE FROM memory_fts WHERE memory_id=?", (mid,))
        c.commit()
    audit("system", "memory.delete", {"id": mid})


def list_recent(limit: int = 50, kind: Optional[str] = None) -> List[MemoryItem]:
    limit = max(1, min(int(limit), 200))
    tm = _T("memory")
    if _db_backend() == "postgres":
        if kind in ("stm", "ltm"):
            rows = fetch_all(
                f"SELECT id FROM {tm} WHERE kind=? ORDER BY updated DESC LIMIT ?",
                (kind, limit),
            )
        else:
            rows = fetch_all(
                f"SELECT id FROM {tm} ORDER BY updated DESC LIMIT ?",
                (limit,),
            )
    else:
        c = db()
        if kind in ("stm", "ltm"):
            rows = c.execute(
                "SELECT id FROM memory WHERE kind=? ORDER BY updated DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id FROM memory ORDER BY updated DESC LIMIT ?",
                (limit,),
            ).fetchall()
    out: List[MemoryItem] = []
    for r in rows:
        out.append(get_memory(str(r["id"])))
    return out


def fts_search(q: str, limit: int = 50) -> List[Tuple[str, float]]:
    limit = max(1, min(int(limit), 200))
    q = (q or "").strip()
    if not q:
        return []
    tf = _T("memory_fts")
    if _db_backend() == "postgres":
        rows = fetch_all(
            f"""
            SELECT memory_id, ts_rank_cd(text_tsv, plainto_tsquery('simple', ?)) AS score
            FROM {tf}
            WHERE text_tsv @@ plainto_tsquery('simple', ?)
            ORDER BY score DESC
            LIMIT ?
            """,
            (q, q, limit),
        )
        out = []
        for r in rows:
            out.append((str(r["memory_id"]), float(r["score"])))
        return out
    c = db()
    rows = c.execute(
        "SELECT memory_id, bm25(memory_fts) as score FROM memory_fts WHERE memory_fts MATCH ? ORDER BY score ASC LIMIT ?",
        (q, limit),
    ).fetchall()
    out = []
    for r in rows:
        out.append((str(r["memory_id"]), float(-r["score"])))
    return out


def vector_search(q: str, limit: int = 20, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 50))
    q = (q or "").strip()
    if not q:
        return []

    qv = embed_one(q)
    tm = _T("memory")
    tv = _T("memory_vec")

    if _db_backend() == "postgres":
        if kind in ("stm", "ltm"):
            rows = fetch_all(
                f"""
                SELECT m.id, m.text, m.meta_json, m.kind, m.importance, m.updated, m.last_access, m.access_count, v.dim, v.vec
                FROM {tm} m JOIN {tv} v ON v.memory_id=m.id WHERE m.kind=?
                """,
                (kind,),
            )
        else:
            rows = fetch_all(
                f"""
                SELECT m.id, m.text, m.meta_json, m.kind, m.importance, m.updated, m.last_access, m.access_count, v.dim, v.vec
                FROM {tm} m JOIN {tv} v ON v.memory_id=m.id
                """,
                (),
            )
    else:
        c = db()
        if kind in ("stm", "ltm"):
            rows = c.execute(
                "SELECT m.id, m.text, m.meta_json, m.kind, m.importance, m.updated, m.last_access, m.access_count, v.dim, v.vec "
                "FROM memory m JOIN memory_vec v ON v.memory_id=m.id WHERE m.kind=?",
                (kind,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT m.id, m.text, m.meta_json, m.kind, m.importance, m.updated, m.last_access, m.access_count, v.dim, v.vec "
                "FROM memory m JOIN memory_vec v ON v.memory_id=m.id",
            ).fetchall()

    scored: List[Tuple[float, Dict[str, Any]]] = []
    now = now_ts()
    for r in rows:
        dim = int(r["dim"])
        raw_vec = r["vec"]
        vec_bytes = bytes(raw_vec) if not isinstance(raw_vec, bytes) else raw_vec
        vec = _blob_to_vec(vec_bytes, dim)
        sim = _cosine(qv, vec)
        recency = max(0, now - int(r["updated"]))
        score = _score(float(r["importance"]), sim, recency, int(r["access_count"]))
        meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
        scored.append(
            (
                score,
                {
                    "id": str(r["id"]),
                    "kind": str(r["kind"]),
                    "text": str(r["text"]),
                    "meta": meta,
                    "importance": float(r["importance"]),
                    "updated": int(r["updated"]),
                    "sim": sim,
                    "score": score,
                },
            )
        )

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [x[1] for x in scored[:limit]]

    for item in top:
        try:
            touch(item["id"])
        except Exception as exc:
            logger.debug("Memory touch failed for item %s: %s", item.get("id"), exc)

    return top


def hybrid_search(q: str, limit: int = 20, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    vec = vector_search(q, limit=limit, kind=kind)
    fts = fts_search(q, limit=limit * 2)

    vec_map = {x["id"]: x for x in vec}
    for mid, fts_score in fts:
        if mid in vec_map:
            vec_map[mid]["score"] += float(fts_score) * 0.25
        else:
            try:
                m = get_memory(mid)
            except Exception:
                continue
            vec_map[mid] = {
                "id": m.id,
                "kind": m.kind,
                "text": m.text,
                "meta": m.meta,
                "importance": m.importance,
                "updated": m.updated,
                "sim": 0.0,
                "score": float(fts_score) * 0.25,
            }

    out = list(vec_map.values())
    out.sort(key=lambda x: float(x["score"]), reverse=True)
    return out[:limit]
