# -*- coding: utf-8 -*-
"""
Kompozycja kontekstu dla ścieżki LLM w czacie — jedna kolejność i prawda audytowa.

Docelowa kolejność (warstwy w messages i w treści systemowej):
  1. system rules (persona, ciągłość wątku, styl)
  2. product rules (vault, odmowy)
  3. style / psyche (profil + Psyche V2)
  4. deterministic — przed LLM; tu nie wchodzi
  5. recent session history — osobne wiadomości; przy długim wątku: skrót starszej części
    (deterministyczny rollup) + ostatnie N surowych tur (``smart_clip_chat_history``)
  6. long-term memory — skrót retrievalu (bez echo STM przy historii)
  7. web findings — prefetch przed ostatnią wiadomością usera
  8. execution / strategy hints
  9. current user message — na końcu

Bez importu silnika wektorów — tylko to, co runtime już zebrał.
"""

from __future__ import annotations

from typing import Any

from aihub.chat_contracts import ChatMessage
from aihub.config import (
    CHAT_HISTORY_RAW_TAIL,
    CHAT_HISTORY_ROLLUP_MAX_CHARS,
    CHAT_HISTORY_ROLLUP_SNIP,
    CHAT_HISTORY_SMART_TRIM_TRIGGER,
)

# Limit wiadomości w payloadzie; chroni prompt przed zalaniem (twardy sufit przed rollup).
MAX_CHAT_HISTORY_MESSAGES: int = 269

_VAULT_LLM_REDACTION_MESSAGE = (
    "[Vault] Wykryto polecenie typu sejf (vault). Nie powtarzaj ani nie zgaduj "
    "sekretów — popraw składnię: zapamiętaj hasło do X: … / podaj hasło do X."
)


def clip_chat_history(
    history: list[ChatMessage] | None,
    *,
    max_messages: int = MAX_CHAT_HISTORY_MESSAGES,
) -> list[ChatMessage]:
    """Ostatnie N wiadomości z historii sesji (kolejność zachowana)."""
    h = list(history or [])
    if len(h) <= max_messages:
        return h
    return h[-max_messages:]


def _rollup_line(msg: ChatMessage) -> str:
    role = msg.role
    if role not in ("user", "assistant", "system"):
        return ""
    raw = (msg.content or "").strip().replace("\n", " ")
    snip = CHAT_HISTORY_ROLLUP_SNIP
    if len(raw) > snip:
        return f"{role.upper()}: {raw[: snip - 1]}…"
    return f"{role.upper()}: {raw}"


def _rollup_old_messages(old: list[ChatMessage]) -> str:
    lines = [_rollup_line(m) for m in old]
    lines = [ln for ln in lines if ln]
    text = "\n".join(lines)
    cap = CHAT_HISTORY_ROLLUP_MAX_CHARS
    if len(text) > cap:
        return text[: cap - 1] + "…"
    return text


def smart_clip_chat_history(
    history: list[ChatMessage] | None,
    *,
    trigger_over: int | None = None,
    raw_tail: int | None = None,
    max_messages: int = MAX_CHAT_HISTORY_MESSAGES,
) -> tuple[str | None, list[ChatMessage]]:
    """Gdy historia jest długa: zwraca (skrót starszej części, ostatnie N surowych wiadomości).

    Skrót to deterministyczne, przycięte cytaty (nie wywołanie LLM).
    """
    h = list(history or [])
    trig = int(
        trigger_over if trigger_over is not None else CHAT_HISTORY_SMART_TRIM_TRIGGER
    )
    tail_n = int(raw_tail if raw_tail is not None else CHAT_HISTORY_RAW_TAIL)
    tail_n = max(20, min(40, tail_n))

    def _split() -> tuple[list[ChatMessage], list[ChatMessage]]:
        if len(h) <= tail_n:
            return [], h
        return h[:-tail_n], h[-tail_n:]

    need_rollup = len(h) > trig or len(h) > max_messages
    if not need_rollup:
        return None, h

    old, tail = _split()
    if not old:
        return None, tail
    return _rollup_old_messages(old), tail


def sanitize_user_message_for_llm(user_message: str) -> tuple[str, bool]:
    """
    Vault-trafiające na LLM (np. literówka): nie wpychamy treści do modelu.
    Zwraca (tekst_do_promptu, czy_zastosowano_redakcję).

    Import ``vault`` leniwy — unikamy łańcucha ``cryptography`` przy samym ``import
    aihub.chat_runtime`` (np. lekkie skrypty / testy bez vault).
    """
    from aihub.vault.service import classify_vault_intent

    raw = (user_message or "").strip()
    if not raw:
        return raw, False
    if classify_vault_intent(raw) is None:
        return raw, False
    return _VAULT_LLM_REDACTION_MESSAGE, True


def memory_truth_for_prompt(memory_context: dict[str, Any] | None) -> dict[str, Any]:
    """
    Source of truth: co retrieval zwrócił vs co jest „merytorycznym” wkładem do promptu.

    - memory_retrieval_has_rows: jakiekolwiek wiersze z unified retrieve (STM/LTM/V2/graf).
    - memory_substantive_in_prompt: LTM / wektor / graf / V2 (bez samego STM) — to idzie
      do ``memory_lookup_happened`` w trace LLM i oznacza realny wkład LTM/V2, nie sam fakt
      wywołania retrieve.
    - stm_rows_in_retrieval: STM z odczytu (osobno od historii w żądaniu).
    """
    g = memory_context if isinstance(memory_context, dict) else {}
    stm = g.get("stm") or []
    epi = g.get("episodic") or []
    sem = g.get("semantic") or []
    dense = g.get("dense_hits") or []
    gh = g.get("graph_hits") or []
    v2 = g.get("memory_v2_items") or []
    graph_total = int(g.get("total") or 0)

    ltm_or_vector = (
        len(epi) > 0 or len(sem) > 0 or len(dense) > 0 or len(gh) > 0 or graph_total > 0
    )
    v2_nonempty = len(v2) > 0
    substantive = ltm_or_vector or v2_nonempty
    stm_n = len(stm) if isinstance(stm, list) else 0
    retrieval_has_rows = substantive or stm_n > 0

    return {
        "memory_substantive_in_prompt": substantive,
        "memory_retrieval_has_rows": retrieval_has_rows,
        "stm_rows_in_retrieval": stm_n,
        "ltm_or_vector_hits": ltm_or_vector,
        "memory_v2_ranked_hits": v2_nonempty,
    }


def memory_results_count_for_trace(memory_context: dict[str, Any] | None) -> int:
    """Liczba wierszy retrievalu do metryki (graph total + V2)."""
    g = memory_context if isinstance(memory_context, dict) else {}
    n = int(g.get("total") or 0)
    v2 = g.get("memory_v2_items") or []
    if isinstance(v2, list) and v2:
        n += len(v2)
    return max(n, 0)


def web_grounding_in_prompt(controlled_web: dict[str, Any] | None) -> bool:
    """True tylko gdy prefetch się wykonał i dostarczył zweryfikowany wynik (nie sam ``required``)."""
    if not isinstance(controlled_web, dict):
        return False
    if not controlled_web.get("triggered"):
        return False
    if controlled_web.get("ok") is not True:
        return False
    return bool(controlled_web.get("has_results"))


def augment_trace_context_truth(
    trace: dict[str, Any],
    *,
    mem_truth: dict[str, Any],
    controlled_web: dict[str, Any],
    decision_core: dict[str, Any],
    force_no_web_verified: bool = False,
) -> None:
    """Dopina zgodne z runtime pola: retrieve vs wstrzyknięcie LTM, intencja web vs faktyczny web w messages.

    ``force_no_web_verified``: jawny fail weba / brak wstrzyknięcia — wymusza false niezależnie od payloadu.
    """
    trace["memory_retrieval_executed"] = True
    trace["memory_retrieval_has_rows"] = bool(
        mem_truth.get("memory_retrieval_has_rows")
    )
    trace["memory_substantive_injected_in_prompt"] = bool(
        mem_truth.get("memory_substantive_in_prompt")
    )
    trace["web_required_by_selector"] = (
        str(decision_core.get("web_decision") or "off") == "required"
    )
    verified = (
        False if force_no_web_verified else web_grounding_in_prompt(controlled_web)
    )
    trace["web_verified_grounding_in_llm_messages"] = verified
    trace["web_grounding_in_prompt"] = verified


def derive_context_chips_from_trace(
    trace: dict[str, Any] | None,
    *,
    input_via_stt: bool = False,
) -> list[str]:
    """Etykiety źródeł dla UI (SSE done / JSON) — trace + opcjonalnie dyktowanie w tej turze."""
    if not isinstance(trace, dict):
        trace = {}
    chips: list[str] = []
    af = trace.get("attached_files")
    if isinstance(af, dict):
        files = af.get("files") or []
        if isinstance(files, list) and files:
            any_ok = any(isinstance(f, dict) and f.get("ok") for f in files)
            any_img_ok = any(
                isinstance(f, dict) and f.get("ok") and f.get("kind") == "image"
                for f in files
            )
            any_img = any(
                isinstance(f, dict) and f.get("kind") == "image" for f in files
            )
            chips.append("attachment-used" if any_ok else "attachment-failed")
            if any_img_ok:
                chips.append("image-used")
            elif any_img:
                chips.append("image-attached")
    mem_on = (
        trace.get("memory_substantive_in_prompt")
        or trace.get("memory_lookup_happened")
        or trace.get("memory_used_bool")
    )
    if mem_on:
        chips.append("memory-used")
    web_on = (
        trace.get("web_verified_grounding_in_llm_messages")
        or trace.get("web_grounding_in_prompt")
        or trace.get("web_used")
    )
    if web_on:
        chips.append("web-used")
    strat = trace.get("selected_strategy")
    if strat in ("instant", "contextual", "research", "agentic"):
        chips.append(f"strat-{strat}")
    if input_via_stt:
        chips.append("stt-input")
    out: list[str] = []
    for c in chips:
        if c not in out:
            out.append(c)
    return out
