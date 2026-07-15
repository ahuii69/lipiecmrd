#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wykrywanie korekt użytkownika, zapis w event_log i tekst do wstrzyknięcia w prompt."""

from __future__ import annotations

import re
from typing import Any

from aihub.db import append_event, fetch_recent_events_by_type

EVENT_TYPE = "user.correction"

# Silne sygnały korekty (PL), bez uruchamiania się na zwykłych pytaniach typu „czy nie…”.
_STRONG_PATTERNS: list[tuple[str, str]] = [
    (
        r"(?iu)^nie\s*,.*\b(lubi|nie\s+lubi|nazywa\s+się|nazywa\s+sie)\b",
        "factual",
    ),
    (r"nie\s+o\s+to\s+chodzi", "negative"),
    (r"nie\s+o\s+to\s*$", "negative"),
    (r"chodziło\s+o|chodzilo\s+o|miałem\s+na\s+myśli|miałam\s+na\s+myśli|"
     r"mialem\s+na\s+mysli|miam\s+na\s+mysli", "factual"),
    (r"(?<![\w])(źle|żle)(?![\w])", "negative"),
    (r"\b(błędnie|to\s+błąd|jest\s+błąd|totalnie\s+źle)\b", "negative"),
    (r"nie\s+podałem|nie\s+podałam|nie\s+podałeś|nie\s+podałaś", "factual"),
    (r"nie\s+podawałem|nie\s+podawałam", "factual"),
    (r"zmyśliłeś|zmyśliłaś|wymyśliłeś|wymyśliłaś|halucyn", "factual"),
    (r"nie\s+masz\s+w\s+treści|nie\s+było\s+w\s+moim", "factual"),
    (r"ma\s+być\s+krócej|ma\s+być\s+dłużej|ma\s+być\s+inaczej", "style"),
    (r"\bkrócej\b|\bzwięźlej\b|\bza\s+długo\b|\bza\s+krótko\b", "style"),
    (r"bardziej\s+formalnie|bardziej\s+zwięźle|mniej\s+rozwlek", "style"),
    (r"\binny\s+styl\b|\bnapisz\s+inaczej\b", "style"),
]

# Trwała preferencja — osobna sesja też widzi zapis (przez durable w eventach).
_DURABLE_MARKERS = re.compile(
    r"(zawsze|od\s+teraz|preferuj\w*|pamiętaj\s+że|nie\s+rób\s+tego|"
    r"nie\s+powtarzaj|na\s+przyszłość)",
    re.IGNORECASE | re.UNICODE,
)


def _normalize_summary(text: str, max_len: int = 320) -> str:
    t = " ".join(str(text or "").split())
    if len(t) > max_len:
        return t[: max_len - 1] + "…"
    return t


def detect_user_correction(message: str) -> dict[str, Any] | None:
    """Zwraca słownik z kind/summary/durable albo None, gdy to nie jest korekta."""
    raw = str(message or "").strip()
    if len(raw) < 4:
        return None

    lower = raw.lower()
    kind: str | None = None
    for pat, k in _STRONG_PATTERNS:
        if re.search(pat, raw, re.IGNORECASE | re.UNICODE):
            kind = k
            break
    if kind is None:
        return None

    durable = bool(_DURABLE_MARKERS.search(raw))
    if kind == "factual" and re.search(
        r"(?iu)\b(lubi|nie\s+lubi|nazywa|pies|kot|preferuj)\b", raw
    ):
        durable = True
    # Krótka, jednozdaniowa korekta stylu często jest „regułą” na przyszłość
    if kind == "style" and len(raw) < 160 and not durable:
        if re.search(
            r"\b(odpowiadaj|pisz|trzymaj|używaj|unikaj|bez\s+)", lower
        ):
            durable = True

    summary = _normalize_summary(raw)
    return {
        "kind": kind,
        "summary": summary,
        "durable": durable,
    }


def record_user_correction_turn(turn: Any) -> dict[str, Any]:
    """Zapisuje korektę z bieżącej wiadomości; zwraca metadane do trace."""
    uid = str(getattr(turn, "user_id", "") or "")
    sid = str(getattr(turn, "session_id", "") or "")
    msg = str(getattr(turn, "message", "") or "")

    out: dict[str, Any] = {"recorded": False, "kind": None, "durable": False}

    if str(getattr(turn, "runtime_mode", "") or "").lower() == "audit":
        return out

    det = detect_user_correction(msg)
    if not det:
        return out

    payload = {
        "session_id": sid,
        "kind": det["kind"],
        "summary": det["summary"],
        "durable": bool(det["durable"]),
        "snippet": _normalize_summary(msg, 500),
    }
    append_event(uid, EVENT_TYPE, payload)
    out["recorded"] = True
    out["kind"] = det["kind"]
    out["durable"] = bool(det["durable"])
    if det["kind"] == "factual" and det["durable"]:
        _persist_factual_correction_memory(uid, sid, msg, det)
    return out


def _persist_factual_correction_memory(
    user_id: str, session_id: str, message: str, det: dict[str, Any]
) -> None:
    """Write durable correction to Memory V2 and supersede conflicting items."""
    try:
        from aihub.memory_core import get_memory_core
        from aihub.memory_v2_contradictions import (
            detect_contradictions,
            resolve_contradiction_supersede,
        )

        core = get_memory_core()
        item = core.v2_create_item(
            user_id=user_id,
            memory_type="preference",
            scope="long_term",
            title="Korekta użytkownika",
            content=_normalize_summary(message, 480),
            source_kind="explicit_learning",
            source_ref=session_id,
            session_id=session_id,
            importance_score=0.95,
            confidence_score=0.95,
        )
        if not item:
            return
        for contradiction in detect_contradictions(user_id, item):
            older_id = str(contradiction.to_memory_id or "")
            if older_id and older_id != item.id:
                resolve_contradiction_supersede(item.id, older_id, user_id)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).debug("correction memory persist skipped: %s", exc)


def _event_row_applies(data: dict[str, Any], session_id: str) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("durable"):
        return True
    return str(data.get("session_id") or "") == session_id


def build_correction_hints_for_prompt(user_id: str, session_id: str) -> str:
    """Skrót korekt do sekcji system (sesja + trwałe)."""
    uid = str(user_id or "")
    sid = str(session_id or "")
    # Audit mode is explicit on the turn; this helper has no turn object — never
    # skip by user_id prefix (removed audit_* production hook).
    if not uid:
        return ""

    rows = fetch_recent_events_by_type(uid, EVENT_TYPE, limit=48)
    lines: list[str] = []
    seen: set[str] = set()
    for row in rows:
        data = row.get("data") if isinstance(row, dict) else {}
        if not isinstance(data, dict):
            continue
        if not _event_row_applies(data, sid):
            continue
        kind = str(data.get("kind") or "feedback")
        summary = str(data.get("summary") or "").strip()
        if not summary:
            continue
        key = f"{kind}:{summary[:200]}"
        if key in seen:
            continue
        seen.add(key)
        label = {"style": "styl", "factual": "fakt", "negative": "uwaga"}.get(
            kind, kind
        )
        lines.append(f"• ({label}) {summary}")
        if len(lines) >= 8:
            break

    if not lines:
        return ""

    lines.reverse()
    return (
        "Korekty użytkownika (ostatnie — stosuj w tej odpowiedzi; nie ignoruj):\n"
        + "\n".join(lines)
    )
