"""User-facing final text for /chat/turn agent handoff (not raw planner/executive reports).

Semantyka przywitania w głównej ścieżce czatu (nie ten moduł) jest w ``aihub.chat_runtime``:
pierwszy turn = pusta ``history`` w żądaniu (wątek sesji po stronie klienta), nie „raz na user_id”.
Ten plik tylko sanitizuje tekst przy handoff do agenta; fallback dla samego „hej” ma ton kumpelski.
"""

from __future__ import annotations

import re
from typing import Any

_CASUAL_RE = re.compile(
    r"^[\s!?.]*("
    r"hi|hello|hey|yo|elo|hej|siema|cześć|czesc|dzień dobry|dzien dobry|"
    r"good morning|witaj|privet|sup|wassup"
    r")[\s!?.]*$",
    re.IGNORECASE,
)

_INTERNAL_SNIPPETS = (
    "zrealizowałem",
    "zrealizowalem",
    "kroków planu",
    "krokow planu",
    "kroków rozumowania",
    "krokow rozumowania",
    "postęp zaktualizowany",
    "postep zaktualizowany",
    "plan gotowy",
    "kroki oczekują",
    "kroki oczekuja",
    "kontekst pamięci:",
    "elementów pamięci",
    "elementow pamieci",
    "brak kontekstu",
    "wykonałem zadanie przez agent runtime",
    "wykonalem zadanie przez agent runtime",
    "reasoning steps=",
    "planner_tasks=",
    "przetworzono ",
    " sygnałów stm",
    " sygnalow stm",
    "w kolejce:",
)

_REPORT_KEYS = (
    "raport",
    "szczegół",
    "szczegol",
    "krok po kroku",
    "co zrobiłeś",
    "co zrobiles",
    "co zrobiłes",
    "wykonanie planu",
    "log agenta",
    "podsumuj wykonanie",
    "trace agenta",
    "status planu",
    "postęp zadań",
    "postep zadan",
    "podsumowanie cyklu",
    "telemetry",
    "internal summary",
)


def user_requested_execution_report(message: str) -> bool:
    ml = (message or "").lower()
    return any(k in ml for k in _REPORT_KEYS)


def looks_like_execution_report_text(text: str) -> bool:
    t = (text or "").lower().strip()
    if not t:
        return False
    if "cykl zakończony z" in t or "cykl zakonczony z" in t:
        return False
    if "niepowodzeniem" in t or "niepowodzeniu" in t:
        return False
    if "przekroczył limit" in t or "przekroczyl limit" in t:
        return False
    if 'cel: "' in t or "cel: '" in t or t.startswith("cel:"):
        return True
    if "cykl zakończony." in t or "cykl zakonczony." in t:
        if "cel:" in t or "aktywnych celów" in t or "aktywnych celow" in t:
            return True
    return any(s in t for s in _INTERNAL_SNIPPETS)


def is_casual_greeting(message: str) -> bool:
    m = (message or "").strip()
    if len(m) > 80:
        return False
    return bool(_CASUAL_RE.match(m))


def _extract_text_blob_from_result(res: dict[str, Any]) -> str | None:
    for key in (
        "response_text",
        "text",
        "content",
        "answer",
        "summary",
        "message",
        "output_text",
    ):
        v = res.get(key)
        if isinstance(v, str) and len(v.strip()) > 12:
            return v.strip()
    out = res.get("output")
    if isinstance(out, dict):
        for key in ("text", "content", "summary", "response", "result", "answer"):
            v = out.get(key)
            if isinstance(v, str) and len(v.strip()) > 12:
                return v.strip()
        data = out.get("data")
        if isinstance(data, dict):
            got = _extract_text_blob_from_result(data)
            if got:
                return got
    return None


def extract_substantive_from_reasoning_payload(payload: dict[str, Any]) -> str | None:
    ctx = payload.get("context")
    if not isinstance(ctx, dict):
        return None
    hist = ctx.get("history")
    if not isinstance(hist, list):
        return None
    for h in reversed(hist):
        if not isinstance(h, dict):
            continue
        res = h.get("result")
        if not isinstance(res, dict):
            continue
        blob = _extract_text_blob_from_result(res)
        if blob and not looks_like_execution_report_text(blob):
            return blob[:12000]
    return None


def synthesize_chat_handoff_user_text(
    *,
    user_message: str,
    internal_reply: str,
    action_summary: str,
    cycle: dict[str, Any],
    agent_ok: bool,
) -> str:
    if user_requested_execution_report(user_message):
        base = (internal_reply or action_summary or "").strip()
        return base or "Brak sensownego raportu z planu — doprecyzuj, co zobaczyć."

    combined = (internal_reply or "").strip()
    if not combined:
        combined = (action_summary or "").strip()

    if not agent_ok:
        return (
            combined
            or "Tura agenta nie doszła do końca — spróbuj jeszcze raz albo krócej."
        )

    if combined and not looks_like_execution_report_text(combined):
        return combined

    exec_result = cycle.get("execution_result")
    payload = exec_result.get("payload", {}) if isinstance(exec_result, dict) else {}
    if isinstance(payload, dict):
        extracted = extract_substantive_from_reasoning_payload(payload)
        if extracted:
            return extracted

    if is_casual_greeting(user_message):
        return "Siema — lecimy z tematem, o co chodzi?"

    um = (user_message or "").lower()
    if any(
        x in um
        for x in (
            "wykonaj teraz",
            "wykonaj migracj",
            "zrób migracj",
            "zrob migracj",
        )
    ):
        return (
            "Nie wykonałem migracji na Twoim serwerze — nie mam dostępu SSH ani "
            "potwierdzonego tool result. Mogę podać bezpieczny plan i komendy weryfikacyjne."
        )

    return (
        "Z planu wyszło coś jak suchy meldunek — napisz krócej, czego potrzebujesz, "
        "albo poproś wprost o raport wykonania."
    )
