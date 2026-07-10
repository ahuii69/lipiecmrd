from __future__ import annotations

"""Compatibility facade for the hierarchical memory helpers.

It delegates durable storage/search to canonical project DB primitives and keeps
old names used by ``aihub.memory.utils``.
"""

import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List

from aihub.config import DB_PATH
from aihub.db import exec_one, fetch_all, now_ts


def _db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hierarchical_ltm (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0.5,
            created_ts REAL NOT NULL
        )
        """
    )
    return conn


def ltm_add(text: str, tags: str = "", conf: float = 0.5) -> str:
    mid = str(uuid.uuid4())
    exec_one(
        """
        CREATE TABLE IF NOT EXISTS hierarchical_ltm (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0.5,
            created_ts REAL NOT NULL
        )
        """
    )
    exec_one(
        "INSERT INTO hierarchical_ltm(id,text,tags,confidence,created_ts) VALUES(?,?,?,?,?)",
        (mid, text, tags, float(conf), now_ts()),
    )
    return mid


def ltm_search_hybrid(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    exec_one(
        """
        CREATE TABLE IF NOT EXISTS hierarchical_ltm (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0.5,
            created_ts REAL NOT NULL
        )
        """
    )
    q = (query or "").lower().strip()
    rows = fetch_all(
        "SELECT id,text,tags,confidence,created_ts FROM hierarchical_ltm ORDER BY created_ts DESC LIMIT ?",
        (max(1, int(limit) * 5),),
    )
    scored: list[dict[str, Any]] = []
    q_terms = set(q.split())
    for row in rows:
        text = str(row["text"])
        terms = set(text.lower().split())
        overlap = len(q_terms & terms) / max(1, len(q_terms | terms)) if q_terms else 0.0
        if not q or overlap > 0 or q in text.lower():
            scored.append({
                "id": row["id"],
                "text": text,
                "tags": row["tags"],
                "confidence": float(row["confidence"]),
                "created_ts": row["created_ts"],
                "score": overlap + float(row["confidence"]) * 0.1,
            })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]
