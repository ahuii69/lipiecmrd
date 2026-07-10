"""LEGACY_RETAINED: ``legacy_ui``-shaped psyche state helpers (single global row).

- **Not** :mod:`aihub.psyche_engine` / :mod:`aihub.psyche_core` (canonical runtime psyche).
- **No** import from :mod:`aihub.main` located in this sprint; kept for compat / reference
  chains (e.g. historical ``aihub.web.service``).

DECLARED_BUT_UNPROVEN without a full static audit: whether any runtime path still calls
this module indirectly — grep before relying on it for new work.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from aihub.db import _db_backend, exec_one, fetch_one
from aihub.db.database import audit, db, now_ts


def get_state() -> Dict[str, Any]:
    if _db_backend() == "postgres":
        row = fetch_one(
            "SELECT state_json FROM legacy_ui.psyche_state WHERE id=1", ()
        )
    else:
        c = db()
        row = c.execute("SELECT state_json FROM psyche_state WHERE id=1").fetchone()
    if row is None:
        return {"mood": "neutral", "goals": [], "beliefs": {}, "traits": {}, "last_reflection": 0}
    return json.loads(row["state_json"])


def set_state(state: Dict[str, Any]) -> Dict[str, Any]:
    s = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    ts = now_ts()
    if _db_backend() == "postgres":
        exec_one(
            "UPDATE legacy_ui.psyche_state SET state_json=?, updated=? WHERE id=1",
            (s, ts),
        )
    else:
        c = db()
        c.execute("UPDATE psyche_state SET state_json=?, updated=? WHERE id=1", (s, ts))
        c.commit()
    audit("system", "psyche.set_state", {"keys": list(state.keys())})
    return state


def reflect(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Heurystyczna 'psychika' bez LLM:
    - mood podbija/zbija na bazie sygnałów
    - beliefs dopisuje proste fakty z eventów
    - goals: jeśli event mówi o celu, dopisuje
    """
    st = get_state()
    mood = st.get("mood", "neutral")

    et = str(event.get("type", "event"))
    score = float(event.get("score", 0.0))
    text = str(event.get("text", ""))[:500]

    # mood dynamics
    if score >= 0.6:
        mood = "focused"
    elif score <= -0.6:
        mood = "irritated"
    else:
        mood = "neutral"

    st["mood"] = mood
    st["last_reflection"] = now_ts()

    # beliefs update
    beliefs = st.get("beliefs", {})
    if isinstance(beliefs, dict):
        if text:
            beliefs[f"last_{et}"] = text
    st["beliefs"] = beliefs

    # goals heuristic
    goals = st.get("goals", [])
    if not isinstance(goals, list):
        goals = []
    if event.get("goal") and isinstance(event["goal"], str):
        g = event["goal"].strip()
        if g and g not in goals:
            goals.append(g)
    st["goals"] = goals

    set_state(st)
    audit("system", "psyche.reflect", {"type": et, "mood": mood})
    return st
