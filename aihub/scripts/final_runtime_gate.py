#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final runtime gate — sprzedażowa brama przed release (exit 1 przy FAIL).

Cztery scenariusze (realny ChatRuntime + provider + narzędzia, bez mocków):

1. Pamięć — dwie tury w jednej sesji (zapis + odczyt tokenu).
2. Vault — zapis / odczyt / usunięcie (deterministyczna ścieżka).
3. Web — FastAPI + URL w odpowiedzi (research / fetch).
4. Kontekst sesji — „co było wcześniej?” z historią w payloadzie.

Uruchomienie z katalogu repo::

    python -m aihub.scripts.final_runtime_gate

Wymaga działającego LLM (np. ``LLM_API_KEY`` / ``DEEPINFRA_API_KEY``) oraz kluczy
do research/web zgodnie z konfiguracją hubu.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from typing import Any, Iterable

# Side effect: loads .env in development (see aihub.config).
import aihub.config  # noqa: F401  # pylint: disable=unused-import
from aihub.chat_contracts import ChatMessage, ChatTurnInput, ToolCallResult
from aihub.chat_runtime import ChatRuntime
from aihub.psyche_engine import ensure_user
from aihub.tools.router import _normalize_tool_name

_MAX_ATTEMPTS_FLAKY = 3


def _norm_tool(name: str) -> str:
    return _normalize_tool_name(name or "")


def _snippet(text: str, n: int = 480) -> str:
    s = (text or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 3] + "..."


def _trace_subset(tr: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: tr.get(k) for k in keys}


def _tool_results_brief(results: list[ToolCallResult]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in results or []:
        out.append(
            {
                "name": _norm_tool(r.name),
                "ok": r.ok,
                "error": (r.error or "")[:200] if r.error else None,
            }
        )
    return out


def _any_ok_web_tool(results: Iterable[ToolCallResult]) -> bool:
    for r in results:
        if not r.ok:
            continue
        n = _norm_tool(r.name)
        if n in ("research.query", "web.fetch_url", "research.url"):
            return True
    return False


def _row_fastapi_official(row: dict) -> bool:
    u = str(row.get("url", "")).lower()
    t = str(row.get("title", "")).lower()
    if "fastapi.tiangolo.com" in u:
        return True
    if "tiangolo.com" in u and "fastapi" in u + t:
        return True
    return False


def _research_fastapi_evidence(results: Iterable[ToolCallResult]) -> bool:
    for r in results:
        if _norm_tool(r.name) != "research.query" or not r.ok or not r.output:
            continue
        res = r.output.get("result")
        if not isinstance(res, dict):
            continue
        for row in res.get("results") or []:
            if isinstance(row, dict) and (
                _row_fastapi_official(row)
                or "fastapi" in f"{row.get('url','')} {row.get('title','')}".lower()
            ):
                return True
    return False


def _fetch_fastapi_evidence(calls: list, results: Iterable[ToolCallResult]) -> bool:
    for c in calls or []:
        if _norm_tool(c.name) != "web.fetch_url":
            continue
        u = str((c.arguments or {}).get("url", "")).lower()
        if "fastapi.tiangolo.com" in u or ("tiangolo.com" in u and "fastapi" in u):
            return True
    for r in results:
        if _norm_tool(r.name) != "web.fetch_url" or not r.ok or not r.output:
            continue
        res = r.output.get("result")
        if isinstance(res, dict):
            blob = f"{res.get('url', '')} {str(res.get('text', ''))[:8000]}"
            if "fastapi.tiangolo.com" in blob.lower() or (
                "tiangolo.com" in blob.lower() and "fastapi" in blob.lower()
            ):
                return True
    return False


def _grounding_ok(tr: dict[str, Any]) -> bool:
    gm = tr.get("response_grounding_mode")
    if gm == "tool_verified":
        return True
    if gm == "fallback" and tr.get("used_tools") and tr.get("used_fallback"):
        return True
    return False


def _has_http_url(text: str) -> bool:
    return bool(re.search(r"https?://[^\s\)\]>'\"]+", text or "", re.I))


def _print_debug(tag: str, payload: dict[str, Any]) -> None:
    print(f"--- {tag} ---")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print()


async def _run_turn(
    user_id: str,
    session_id: str,
    message: str,
    history: list[ChatMessage],
):
    ensure_user(user_id)
    turn = ChatTurnInput(
        user_id=user_id,
        session_id=session_id,
        message=message,
        mode="chat",
        history=list(history),
    )
    rt = ChatRuntime()
    out = await rt.run_turn(turn)
    return turn, out


async def _t1_memory_two_turns(uid: str) -> tuple[bool, str, dict[str, Any]]:
    """Dwie tury w jednej sesji: zapis faktu + odczyt."""
    token = f"MEM_GATE_{uuid.uuid4().hex[:8].upper()}"
    sid = f"fg-mem-{uuid.uuid4().hex[:12]}"
    m1 = (
        f"Zapamiętaj na stałe jako fakt użytkownika: mój token testowy bramki to {token}. "
        "Odpowiedz jednym zdaniem potwierdzenia."
    )
    _t1, o1 = await _run_turn(uid, sid, m1, [])
    hist = [
        ChatMessage(role="user", content=m1),
        ChatMessage(role="assistant", content=o1.response_text or ""),
    ]
    m2 = "Jaki jest mój token testowy bramki? Odpowiedz wyłącznie tokenem, bez dodatkowego tekstu."
    _t2, o2 = await _run_turn(uid, sid, m2, hist)
    text = (o2.response_text or "").strip()
    ok = token in text
    dbg = {
        "session_id": sid,
        "turn1": {"request": m1, "response_snippet": _snippet(o1.response_text)},
        "turn2": {
            "request": m2,
            "response_snippet": _snippet(o2.response_text),
            "trace": _trace_subset(
                o2.trace or {},
                ("memory_lookup_happened", "chat_history_message_count"),
            ),
        },
    }
    if not ok:
        return False, f"token {token!r} not in turn2 response", dbg
    return True, "", dbg


async def _t2_vault_cycle(uid: str) -> tuple[bool, str, dict[str, Any]]:
    alias = f"gate_vlt_{uuid.uuid4().hex[:10]}"
    secret = f"SEC_{uuid.uuid4().hex[:12]}"
    dbg: dict[str, Any] = {"alias": alias}

    _tw, w = await _run_turn(
        uid,
        f"fg-vw-{uuid.uuid4().hex[:8]}",
        f"zapamiętaj hasło do {alias}: {secret}",
        [],
    )
    dbg["store"] = {"response": _snippet(w.response_text), "ok": w.ok}
    if not w.ok or "zapisane" not in (w.response_text or "").lower():
        return False, "vault store failed or unexpected copy", dbg

    _tr, r = await _run_turn(
        uid, f"fg-vr-{uuid.uuid4().hex[:8]}", f"podaj hasło do {alias}", []
    )
    dbg["read"] = {"response": _snippet(r.response_text), "ok": r.ok}
    if secret not in (r.response_text or ""):
        return False, "vault read missing secret plaintext", dbg

    _td, d = await _run_turn(
        uid, f"fg-vd-{uuid.uuid4().hex[:8]}", f"usuń hasło do {alias}", []
    )
    dbg["delete"] = {"response": _snippet(d.response_text), "ok": d.ok}
    if "usunięte" not in (d.response_text or "").lower():
        return False, "vault delete unexpected copy", dbg

    _tm, m = await _run_turn(
        uid, f"fg-vm-{uuid.uuid4().hex[:8]}", f"podaj hasło do {alias}", []
    )
    dbg["read_missing"] = {"response": _snippet(m.response_text), "ok": m.ok}
    if "brak wpisu" not in (m.response_text or "").lower():
        return False, "expected missing after delete", dbg

    return True, "", dbg


async def _t3_web_fastapi(uid: str) -> tuple[bool, str, dict[str, Any]]:
    msg = (
        "Sprawdź oficjalną stronę FastAPI i napisz jednym zdaniem, co to jest. "
        "Podaj źródło (URL). Użyj research.query albo web.fetch_url."
    )
    last: dict[str, Any] = {}
    for attempt in range(1, _MAX_ATTEMPTS_FLAKY + 1):
        sid = f"fg-web-{uuid.uuid4().hex[:12]}"
        _t, out = await _run_turn(uid, sid, msg, [])
        tr = out.trace or {}
        results = out.tool_results or []
        calls = out.tool_calls or []
        web_on = tr.get("controlled_web_triggered") is True or (
            tr.get("used_tools") is True and _any_ok_web_tool(results)
        )
        tools_on = _any_ok_web_tool(results)
        grounded = _grounding_ok(tr)
        url_ok = _has_http_url(out.response_text or "")
        fastapi_ev = _fetch_fastapi_evidence(
            calls, results
        ) or _research_fastapi_evidence(results)
        last = {
            "attempt": attempt,
            "request": msg,
            "response_snippet": _snippet(out.response_text),
            "trace": _trace_subset(
                tr,
                (
                    "controlled_web_triggered",
                    "response_grounding_mode",
                    "used_tools",
                    "used_fallback",
                ),
            ),
            "tool_results_summary": _tool_results_brief(results),
        }
        if (
            web_on
            and tools_on
            and grounded
            and url_ok
            and fastapi_ev
            and "fastapi" in (out.response_text or "").lower()
        ):
            return True, "", last
        reason_parts = []
        if not web_on:
            reason_parts.append("no web trace signal")
        if not tools_on:
            reason_parts.append("no successful web/research tool")
        if not grounded:
            reason_parts.append("grounding mode not acceptable")
        if not url_ok:
            reason_parts.append("no http(s) URL in response")
        if not fastapi_ev:
            reason_parts.append("no FastAPI official-site tool evidence")
        if "fastapi" not in (out.response_text or "").lower():
            reason_parts.append("response missing 'fastapi'")
        last["fail_reason"] = "; ".join(reason_parts)

    return False, str(last.get("fail_reason", "web scenario failed")), last


async def _t4_session_context(uid: str) -> tuple[bool, str, dict[str, Any]]:
    marker = f"CTX_GATE_{uuid.uuid4().hex[:10].upper()}"
    sid = f"fg-ctx-{uuid.uuid4().hex[:12]}"
    hist = [
        ChatMessage(role="user", content=f"Notatka startowa: {marker}"),
        ChatMessage(role="assistant", content="OK, zapisuję w kontekście sesji."),
    ]
    q = "co było wcześniej?"
    _t, out = await _run_turn(uid, sid, q, hist)
    text = out.response_text or ""
    tr = out.trace or {}
    ok = marker in text and "przed" in text.lower()
    dbg = {
        "session_id": sid,
        "request": q,
        "response_snippet": _snippet(text),
        "trace": _trace_subset(
            tr,
            (
                "deterministic_hit",
                "selected_route",
                "response_grounding_mode",
            ),
        ),
    }
    if not ok:
        return (
            False,
            "deterministic context reply missing marker or 'przed'",
            dbg,
        )
    return True, "", dbg


async def _main_async() -> int:
    base = f"final-gate-{uuid.uuid4().hex[:12]}"
    results: list[tuple[str, bool, str, dict[str, Any]]] = []

    ok1, why1, d1 = await _t1_memory_two_turns(f"{base}-mem")
    results.append(("1 MEMORY (2 turns)", ok1, why1, d1))

    ok2, why2, d2 = await _t2_vault_cycle(f"{base}-vault")
    results.append(("2 VAULT (store/read/delete)", ok2, why2, d2))

    ok3, why3, d3 = await _t3_web_fastapi(f"{base}-web")
    results.append(("3 WEB (FastAPI + URL)", ok3, why3, d3))

    ok4, why4, d4 = await _t4_session_context(f"{base}-ctx")
    results.append(("4 CONTEXT (wcześniej)", ok4, why4, d4))

    print("[FINAL RUNTIME GATE]")
    for label, ok, why, _dbg in results:
        status = "OK" if ok else "FAIL"
        print(f"{label}: {status}")
        if not ok and why:
            print(f"  -> {why}")

    for label, ok, why, dbg in results:
        payload = dict(dbg)
        payload["PASS"] = ok
        if not ok and why:
            payload["reason"] = why
        _print_debug(label, payload)

    final_ok = all(x[1] for x in results)
    print(f"FINAL: {'OK' if final_ok else 'FAIL'}")
    return 0 if final_ok else 1


def main() -> None:
    try:
        code = asyncio.run(_main_async())
    except KeyboardInterrupt:
        print("\n[FINAL RUNTIME GATE] INTERRUPTED", file=sys.stderr)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
