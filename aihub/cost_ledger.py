#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production cost ledger — token×price per provider, per user, per day."""

from __future__ import annotations

import logging
import time
from typing import Any

from aihub.db import exec_one, fetch_all, fetch_one, json_dumps, json_loads

log = logging.getLogger(__name__)

# USD per 1M tokens (approximate public list prices; override via env later if needed).
PROVIDER_PRICE_PER_MTOK: dict[str, dict[str, float]] = {
    "deepinfra": {"prompt": 0.10, "completion": 0.40},
    "groq": {"prompt": 0.15, "completion": 0.60},
    "ollama": {"prompt": 0.0, "completion": 0.0},
    "openai": {"prompt": 2.50, "completion": 10.0},
    "voyage": {"prompt": 0.06, "completion": 0.0},
    "unknown": {"prompt": 0.20, "completion": 0.60},
}



def _row(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return {}


def ensure_cost_ledger_schema() -> None:
    """Idempotent DDL — always safe across test DB swaps."""
    exec_one(
        """
        CREATE TABLE IF NOT EXISTS cost_ledger_entries (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL DEFAULT '',
            turn_id TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0,
            day_key TEXT NOT NULL,
            meta TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        )
        """
    )
    exec_one(
        "CREATE INDEX IF NOT EXISTS idx_cost_ledger_user_day ON cost_ledger_entries(user_id, day_key)"
    )
    exec_one(
        "CREATE INDEX IF NOT EXISTS idx_cost_ledger_day ON cost_ledger_entries(day_key)"
    )


def estimate_cost_usd(
    *,
    provider: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> float:
    key = (provider or "unknown").strip().lower() or "unknown"
    prices = PROVIDER_PRICE_PER_MTOK.get(key) or PROVIDER_PRICE_PER_MTOK["unknown"]
    p = max(0, int(prompt_tokens or 0))
    c = max(0, int(completion_tokens or 0))
    return round((p * prices["prompt"] + c * prices["completion"]) / 1_000_000.0, 8)


def record_turn_cost(
    *,
    user_id: str,
    session_id: str = "",
    turn_id: str = "",
    provider: str = "",
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one turn cost row; returns the recorded summary."""
    ensure_cost_ledger_schema()
    import uuid

    uid = (user_id or "").strip() or "anonymous"
    pt = max(0, int(prompt_tokens or 0))
    ct = max(0, int(completion_tokens or 0))
    tt = max(0, int(total_tokens or 0)) or (pt + ct)
    cost = estimate_cost_usd(provider=provider, prompt_tokens=pt, completion_tokens=ct)
    day_key = time.strftime("%Y-%m-%d", time.gmtime())
    entry_id = str(uuid.uuid4())
    now = time.time()
    exec_one(
        """
        INSERT INTO cost_ledger_entries(
            id, user_id, session_id, turn_id, provider, model,
            prompt_tokens, completion_tokens, total_tokens, cost_usd,
            day_key, meta, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            entry_id,
            uid,
            session_id or "",
            turn_id or "",
            (provider or "")[:64],
            (model or "")[:128],
            pt,
            ct,
            tt,
            cost,
            day_key,
            json_dumps(meta or {}),
            now,
        ),
    )
    return {
        "id": entry_id,
        "user_id": uid,
        "day_key": day_key,
        "provider": provider,
        "model": model,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": tt,
        "cost_usd": cost,
    }


def user_day_summary(user_id: str, *, day_key: str | None = None) -> dict[str, Any]:
    ensure_cost_ledger_schema()
    day = day_key or time.strftime("%Y-%m-%d", time.gmtime())
    row = _row(
        fetch_one(
        """
        SELECT
            COUNT(*) AS turns,
            COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens),0) AS completion_tokens,
            COALESCE(SUM(total_tokens),0) AS total_tokens,
            COALESCE(SUM(cost_usd),0) AS cost_usd
        FROM cost_ledger_entries
        WHERE user_id=? AND day_key=?
        """,
        (user_id, day),
        )
    )
    by_provider = [
        _row(r)
        for r in (
            fetch_all(
        """
        SELECT provider,
               COUNT(*) AS turns,
               COALESCE(SUM(total_tokens),0) AS total_tokens,
               COALESCE(SUM(cost_usd),0) AS cost_usd
        FROM cost_ledger_entries
        WHERE user_id=? AND day_key=?
        GROUP BY provider
        ORDER BY cost_usd DESC
        """,
        (user_id, day),
            )
            or []
        )
    ]
    return {
        "user_id": user_id,
        "day_key": day,
        "turns": int(row.get("turns") or 0),
        "prompt_tokens": int(row.get("prompt_tokens") or 0),
        "completion_tokens": int(row.get("completion_tokens") or 0),
        "total_tokens": int(row.get("total_tokens") or 0),
        "cost_usd": round(float(row.get("cost_usd") or 0.0), 6),
        "by_provider": [
            {
                "provider": r.get("provider"),
                "turns": int(r.get("turns") or 0),
                "total_tokens": int(r.get("total_tokens") or 0),
                "cost_usd": round(float(r.get("cost_usd") or 0.0), 6),
            }
            for r in by_provider
        ],
        "alert": bool(float(row.get("cost_usd") or 0.0) >= float(
            __import__("os").getenv("AIHUB_COST_DAY_ALERT_USD", "5.0") or 5.0
        )),
    }


def global_day_summary(*, day_key: str | None = None) -> dict[str, Any]:
    ensure_cost_ledger_schema()
    day = day_key or time.strftime("%Y-%m-%d", time.gmtime())
    row = _row(
        fetch_one(
        """
        SELECT
            COUNT(*) AS turns,
            COUNT(DISTINCT user_id) AS users,
            COALESCE(SUM(total_tokens),0) AS total_tokens,
            COALESCE(SUM(cost_usd),0) AS cost_usd
        FROM cost_ledger_entries
        WHERE day_key=?
        """,
        (day,),
        )
    )
    return {
        "day_key": day,
        "turns": int(row.get("turns") or 0),
        "users": int(row.get("users") or 0),
        "total_tokens": int(row.get("total_tokens") or 0),
        "cost_usd": round(float(row.get("cost_usd") or 0.0), 6),
    }
