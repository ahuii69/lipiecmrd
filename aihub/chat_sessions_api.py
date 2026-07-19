from __future__ import annotations

import re
from typing import Any

from datetime import datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from aihub.db import (
    delete_chat_session_messages,
    exec_one,
    fetch_all,
    fetch_chat_session_messages,
    fetch_one,
    json_loads,
    now_ts,
)

router = APIRouter(prefix="/chat", tags=["chat"])

_MAX_ATTACHED_IDS_IN_HISTORY = 8


class RenameBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)


class SessionIdBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=256)


class AutoTitleBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=256)
    first_user_message: str = Field(default="")


def _derive_auto_title(first_user_message: str) -> str:
    s = (first_user_message or "").strip()
    if not s:
        return "Nowa rozmowa"
    words = re.findall(r"\S+", s)
    if not words:
        return "Nowa rozmowa"
    n = len(words)
    if n <= 10:
        chunk = words[: max(1, min(n, 10))]
    else:
        chunk = words[:10]
    out = " ".join(chunk).strip()
    return out if out else "Nowa rozmowa"


def _history_created_iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _row_to_item(row: Any) -> dict[str, Any]:
    archived = bool(int(row["archived"] or 0)) if "archived" in row.keys() else False
    item: dict[str, Any] = {
        "id": str(row["id"]),
        "title": str(row["title"]),
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
        "archived": archived,
    }
    if archived:
        raw_at = row["archived_at"] if "archived_at" in row.keys() else None
        if raw_at is not None:
            item["archived_at"] = float(raw_at)
    return item


def _ensure_session_row(user_id: str, session_id: str, *, title: str = "Nowa rozmowa") -> None:
    row = fetch_one(
        "SELECT id FROM chat_sessions WHERE user_id=? AND id=?",
        (user_id, session_id),
    )
    if row:
        return
    ts = now_ts()
    exec_one(
        """
        INSERT INTO chat_sessions(user_id, id, title, created_at, updated_at, archived, archived_at)
        VALUES(?,?,?,?,?,0,NULL)
        """,
        (user_id, session_id, title, ts, ts),
    )


@router.get("/session/{session_id}/history")
def get_session_history(
    session_id: str,
    user_id: str = Query(min_length=1, max_length=128),
) -> dict[str, Any]:
    """Transkrypt sesji (chronologicznie) — źródło prawdy dla UI."""
    sid = (session_id or "").strip() or "default"
    rows = fetch_chat_session_messages(user_id, sid, limit=2000)
    messages: list[dict[str, Any]] = []
    for r in rows:
        item: dict[str, Any] = {
            "id": str(r["id"]),
            "role": str(r["role"]),
            "content": str(r["content"] or ""),
            "created_at": _history_created_iso(float(r["created_at"])),
        }
        raw_meta = r.get("meta")
        if raw_meta:
            meta = json_loads(raw_meta) or {}
            if isinstance(meta, dict):
                af = meta.get("attached_file_ids")
                if isinstance(af, list) and af:
                    item["attached_file_ids"] = [
                        str(x) for x in af[:_MAX_ATTACHED_IDS_IN_HISTORY] if str(x).strip()
                    ]
        messages.append(item)
    return {
        "session_id": sid,
        "messages": messages,
    }


@router.get("/sessions")
def list_sessions(
    user_id: str = Query(min_length=1, max_length=128),
    include_archived: bool = Query(
        True,
        description="When true (default), archived sessions are included with archived=true.",
    ),
) -> dict[str, Any]:
    if include_archived:
        rows = fetch_all(
            """
            SELECT id, title, created_at, updated_at, archived, archived_at
            FROM chat_sessions
            WHERE user_id=?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        )
    else:
        rows = fetch_all(
            """
            SELECT id, title, created_at, updated_at, archived, archived_at
            FROM chat_sessions
            WHERE user_id=? AND COALESCE(archived, 0)=0
            ORDER BY updated_at DESC
            """,
            (user_id,),
        )
    return {"sessions": [_row_to_item(r) for r in rows]}


@router.patch("/session/rename")
def rename_session(body: RenameBody) -> dict[str, Any]:
    ts = now_ts()
    row = fetch_one(
        "SELECT id FROM chat_sessions WHERE user_id=? AND id=?",
        (body.user_id, body.session_id),
    )
    if row:
        exec_one(
            """
            UPDATE chat_sessions SET title=?, updated_at=? WHERE user_id=? AND id=?
            """,
            (body.title, ts, body.user_id, body.session_id),
        )
    else:
        exec_one(
            """
            INSERT INTO chat_sessions(user_id, id, title, created_at, updated_at, archived, archived_at)
            VALUES(?,?,?,?,?,0,NULL)
            """,
            (body.user_id, body.session_id, body.title, ts, ts),
        )
    return {"ok": True, "title": body.title}


@router.delete("/session")
def delete_session(body: SessionIdBody) -> dict[str, Any]:
    delete_chat_session_messages(body.user_id, body.session_id)
    exec_one(
        "DELETE FROM chat_sessions WHERE user_id=? AND id=?",
        (body.user_id, body.session_id),
    )
    return {"ok": True}


@router.post("/session/archive")
def archive_session(body: SessionIdBody) -> dict[str, Any]:
    """Mark session archived (soft). Transcript retained; listable with archived=true."""
    ts = now_ts()
    _ensure_session_row(body.user_id, body.session_id)
    exec_one(
        """
        UPDATE chat_sessions
        SET archived=1, archived_at=?, updated_at=?
        WHERE user_id=? AND id=?
        """,
        (ts, ts, body.user_id, body.session_id),
    )
    return {"ok": True, "session_id": body.session_id, "archived": True, "archived_at": ts}


@router.post("/session/unarchive")
def unarchive_session(body: SessionIdBody) -> dict[str, Any]:
    """Restore archived session to the active list."""
    ts = now_ts()
    _ensure_session_row(body.user_id, body.session_id)
    exec_one(
        """
        UPDATE chat_sessions
        SET archived=0, archived_at=NULL, updated_at=?
        WHERE user_id=? AND id=?
        """,
        (ts, body.user_id, body.session_id),
    )
    return {"ok": True, "session_id": body.session_id, "archived": False}


@router.post("/session/auto-title")
def auto_title_session(body: AutoTitleBody) -> dict[str, Any]:
    title = _derive_auto_title(body.first_user_message)
    ts = now_ts()
    row = fetch_one(
        "SELECT id FROM chat_sessions WHERE user_id=? AND id=?",
        (body.user_id, body.session_id),
    )
    if row:
        exec_one(
            """
            UPDATE chat_sessions SET title=?, updated_at=? WHERE user_id=? AND id=?
            """,
            (title, ts, body.user_id, body.session_id),
        )
    else:
        exec_one(
            """
            INSERT INTO chat_sessions(user_id, id, title, created_at, updated_at, archived, archived_at)
            VALUES(?,?,?,?,?,0,NULL)
            """,
            (body.user_id, body.session_id, title, ts, ts),
        )
    return {"ok": True, "title": title}
