#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Krótkie obejścia czatu bez LLM: vault sekretów, odpowiedzi z historii sesji, jeden fakt z pamięci."""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from aihub.chat_contracts import (
    ChatMessage,
    ChatTurnInput,
    ChatTurnResult,
    ProviderUsage,
)
from aihub.chat_decision_trace import (
    ROUTE_DETERMINISTIC_FACT_READ,
    ROUTE_DETERMINISTIC_HISTORY,
    ROUTE_DETERMINISTIC_IMAGE_GENERATION,
    ROUTE_DETERMINISTIC_VAULT_DELETE,
    ROUTE_DETERMINISTIC_VAULT_LIST,
    ROUTE_DETERMINISTIC_VAULT_READ,
    ROUTE_DETERMINISTIC_VAULT_STORE,
    merge_canonical_decision_trace,
    trace_blocker_gate_outcome,
    trace_handoff_gate_outcome,
)
from aihub.chat_history_trace import build_history_trace
from aihub.chat_image_generation import (
    build_image_generation_reply,
    is_image_generation_intent,
)
from aihub.chat_product_policy import MEMORY_FACT_RECALL_HINT
from aihub.memory_context_pack import is_junk_memory_content
from aihub.vault.firewall import blocks_memory_fact_recall_for_credentials
from aihub.vault.service import try_vault_turn

_RE_N_UP = re.compile(r"(?i)(\d+)\s*wiadomości\s*(wyżej|wyzej)")
_RE_FEW_UP = re.compile(r"(?i)kilka\s+wiadomości\s*(wyżej|wyzej)")
_RE_BEGIN = re.compile(
    r"(?i)(co\s+pisałem\s+na\s+początku|"
    r"pierwsz[ąa]\s+wiadomo|"
    r"początku\s+(tej\s+)?rozmow|"
    r"jak\s+zacząłem\s+rozmow|"
    r"na\s+starcie\s+(tej\s+)?rozmow|"
    r"jak[ąa]\s+był[ao]\s+moj[ąa]\s+pierwsz[ąa]\s+wiadomo|"
    r"co\s+napisałem\s+jako\s+pierwsz|"
    r"początek\s+(tego\s+)?czat)"
)
_RE_PREV_ONE = re.compile(
    r"(?i)(przedostatni[ąa]?\s+wiadomo|"
    r"wiadomość\s+przed\s+ostatni[ąa]|"
    r"co\s+było\s+przed\s+ostatni[ąa])"
)
_RE_LAST_USER_ABOVE = re.compile(
    r"(?i)(co\s+pisałem\s+wyżej|co\s+napisałem\s+wyżej|"
    r"co\s+pisalem\s+wyzej|co\s+napisałem\s+wyzej)\??"
)
_RE_LOCAL_HEALTH = re.compile(
    r"(?iu)\b("
    r"sprawd[źz]\s+.*backend\w*|"
    r"status\s+backend\w*|"
    r"health\s+(?:\w+\s+){0,4}backend\w*|"
    r"zrestartuj\s+.*backend\w*|"
    r"restartuj\s+.*backend\w*|"
    r"/ops/ready|/system/ping"
    r")\b"
)
_RE_EPISODE_QA_ECHO = re.compile(r"(?is)U\s*:.+\|\|.*A\s*:")


def _transcript(turn: ChatTurnInput) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for m in turn.history or []:
        if isinstance(m, ChatMessage):
            role, content = m.role, (m.content or "").strip()
        elif isinstance(m, dict):
            role = str(m.get("role") or "")
            content = str(m.get("content") or "").strip()
        else:
            continue
        if role not in ("user", "assistant") or not content:
            continue
        out.append((role, content))
    return out


_VAULT_ROUTE: dict[str, tuple[str, str]] = {
    "store": ("deterministic_vault_store", ROUTE_DETERMINISTIC_VAULT_STORE),
    "read": ("deterministic_vault_read", ROUTE_DETERMINISTIC_VAULT_READ),
    "delete": ("deterministic_vault_delete", ROUTE_DETERMINISTIC_VAULT_DELETE),
    "list": ("deterministic_vault_list", ROUTE_DETERMINISTIC_VAULT_LIST),
}


def _history_turn(turn: ChatTurnInput, msg: str) -> Optional[str]:
    t = msg.strip()
    tr = _transcript(turn)
    if not tr:
        return None

    if _RE_BEGIN.search(t):
        first_u = next((c for r, c in tr if r == "user"), None)
        if first_u is None:
            return "Nie widzę wcześniejszej wiadomości użytkownika w tej sesji."
        return f"Na początku pisałeś: {first_u}"

    if _RE_FEW_UP.search(t):
        msgs = [c for _, c in tr]
        n = min(3, len(msgs))
        if n < 1:
            return None
        chunk = msgs[-n:]
        return f"{n} wiadomości wyżej: " + " | ".join(chunk)

    m = _RE_N_UP.search(t)
    if m:
        n = int(m.group(1))
        msgs = [c for _, c in tr]
        if n < 1 or n > len(msgs):
            return f"Mam w historii {len(msgs)} wiadomości — nie da się cofnąć o {n}."
        return msgs[-n]

    if re.search(
        r"(?i)(co\s+było\s+wcześniej|wcześniejsz[ąa]\s+wiadomo|"
        r"co\s+napisałem\s+przed\s+chwilą)",
        t,
    ):
        if len(tr) < 2:
            return "To za mało historii, żeby sensownie odpowiedzieć."
        role, content = tr[-2]
        return f"Przed tym było ({role}): {content}"

    if _RE_PREV_ONE.search(t):
        if len(tr) < 2:
            return "To za mało historii, żeby sensownie odpowiedzieć."
        role, content = tr[-2]
        return f"Przedostatnia wiadomość ({role}): {content}"

    if _RE_LAST_USER_ABOVE.search(t):
        last_u: str | None = None
        for role, content in reversed(tr):
            if role == "user":
                last_u = content
                break
        if last_u is not None:
            return last_u

    return None


_DOMINANCE_GAP = 0.07
_DOMINANT_MIN_SCORE = 0.42
_DOMINANT_STRONG_SCORE = 0.60
_WEAK_RUNNER_MAX = 0.42
_WEAK_RUNNER_TOP_MIN = 0.40


def _norm_text(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _legacy_single_graph_snippet(mem_ctx: Dict[str, Any]) -> tuple[Optional[str], str]:
    """Zachowanie jak wcześniej: ``total`` z grafu == 1 i brak konkurującego V2 w tej samej turze."""
    if int(mem_ctx.get("total") or 0) != 1:
        return None, ""
    if int(mem_ctx.get("memory_v2_total") or 0) != 0:
        return None, ""
    if mem_ctx.get("memory_v2_items"):
        return None, ""
    chunks: list[str] = []
    for key in ("episodic", "semantic"):
        for it in mem_ctx.get(key) or []:
            if not isinstance(it, dict):
                continue
            c = str(it.get("content") or "").strip()
            if c:
                chunks.append(c)
    if len(chunks) == 1:
        return chunks[0], "legacy_graph_total_eq_1"
    return None, ""


def _requires_live_external_lookup(msg: str) -> bool:
    """Freshness / market / release queries must not short-circuit on stale memory."""
    low = (msg or "").lower()
    if any(
        p in low
        for p in (
            "najnowsz",
            "aktualn",
            "dzisiaj",
            "dziś",
            "teraz ",
            "kurs ",
            "sprawdź",
            "sprawdz",
            "wersja python",
            "pythona",
            "exchange rate",
        )
    ):
        return True
    if re.search(r"(?i)\b(eur|usd|pln)\b", low) and any(
        w in low for w in ("kurs", "cena", "ile", "koszt")
    ):
        return True
    return False


def _collect_memory_fact_candidates(
    mem_ctx: Dict[str, Any], query_text: str = ""
) -> list[tuple[float, str]]:
    """(score, content) z L1/L2, STM user, V2, dense, graph — posortowane malejąco po score."""
    qn = _norm_text(query_text)
    pairs: list[tuple[float, str]] = []
    for key in ("episodic", "semantic"):
        for it in mem_ctx.get(key) or []:
            if not isinstance(it, dict):
                continue
            c = str(it.get("content") or "").strip()
            if len(c) < 3 or is_junk_memory_content(c, query=query_text):
                continue
            if qn and _norm_text(c) == qn:
                continue
            sc = float(it.get("score") or 0.0)
            pairs.append((sc, c))
    for it in mem_ctx.get("stm") or []:
        if not isinstance(it, dict):
            continue
        if str(it.get("role") or "") != "user":
            continue
        c = str(it.get("content") or "").strip()
        if len(c) < 8 or is_junk_memory_content(c, query=query_text):
            continue
        if qn and _norm_text(c) == qn:
            continue
        pairs.append((0.18, c))
    for it in mem_ctx.get("memory_v2_items") or []:
        if not isinstance(it, dict):
            continue
        c = str(it.get("content") or "").strip()
        if len(c) < 3 or is_junk_memory_content(c, query=query_text):
            continue
        if qn and _norm_text(c) == qn:
            continue
        sc = max(
            float(it.get("retrieval_priority_score") or 0),
            float(it.get("salience_score") or 0),
            float(it.get("relation_relevance_score") or 0),
            float(it.get("importance_score") or 0) * 0.55,
        )
        if sc <= 0:
            sc = 0.35
        pairs.append((sc, c))
    for it in mem_ctx.get("dense_hits") or []:
        if not isinstance(it, dict):
            continue
        c = str(it.get("text") or "").strip()
        if len(c) < 3 or is_junk_memory_content(c, query=query_text):
            continue
        if qn and _norm_text(c) == qn:
            continue
        sim = float(it.get("similarity") or 0.0)
        pairs.append((max(0.15, min(0.95, sim)), c))
    for it in mem_ctx.get("graph_hits") or []:
        if not isinstance(it, dict):
            continue
        c = str(it.get("content") or "").strip()
        if len(c) < 3 or is_junk_memory_content(c, query=query_text):
            continue
        if qn and _norm_text(c) == qn:
            continue
        conf = float(it.get("confidence") or 0.5)
        pairs.append((max(0.12, min(0.9, conf)), c))
    pairs.sort(key=lambda x: -x[0])
    return pairs


def _best_scoring_content(items: Any) -> tuple[Optional[str], float]:
    best_c: Optional[str] = None
    best_sc = -1.0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        c = str(it.get("content") or "").strip()
        if len(c) < 3:
            continue
        sc = float(it.get("score") or 0.0)
        if sc > best_sc:
            best_sc = sc
            best_c = c
    return best_c, best_sc


def _is_episode_qa_echo(s: str) -> bool:
    return bool(_RE_EPISODE_QA_ECHO.search(s or ""))


def _pick_dominant_memory_snippet(
    mem_ctx: Dict[str, Any],
    query_text: str = "",
) -> tuple[Optional[str], str]:
    """Jedna odpowiedź: legacy total==1, jedna treść, dominacja score lub słaby drugi wynik."""
    leg, leg_reason = _legacy_single_graph_snippet(mem_ctx)
    if leg:
        qn = _norm_text(query_text)
        if not (qn and _norm_text(leg) == qn):
            return leg, leg_reason

    sem_best, sem_sc = _best_scoring_content(mem_ctx.get("semantic"))
    epi_best, epi_sc = _best_scoring_content(mem_ctx.get("episodic"))
    if (
        sem_best
        and epi_best
        and _is_episode_qa_echo(epi_best)
        and not _is_episode_qa_echo(sem_best)
        and epi_sc >= sem_sc
    ):
        qn = _norm_text(query_text)
        if not (qn and _norm_text(sem_best) == qn):
            return sem_best, "semantic_fact_priority"

    pairs = _collect_memory_fact_candidates(mem_ctx, query_text)
    best_by_norm: dict[str, tuple[float, str]] = {}
    for sc, c in pairs:
        n = " ".join(c.lower().split())
        if len(n) < 6:
            continue
        prev = best_by_norm.get(n)
        if prev is None or sc > prev[0]:
            best_by_norm[n] = (sc, c)
    uniq = sorted(best_by_norm.values(), key=lambda x: -x[0])
    if not uniq:
        return None, "no_candidates"
    if len(uniq) == 1:
        return uniq[0][1], "single_distinct_hit"
    top_sc, top_c = uniq[0]
    second_sc, _second_c = uniq[1]
    if top_sc >= _DOMINANT_STRONG_SCORE:
        return top_c, "strong_top_score"
    if top_sc >= _DOMINANT_MIN_SCORE and (top_sc - second_sc) >= _DOMINANCE_GAP:
        return top_c, "dominant_over_runner_up"
    if second_sc < _WEAK_RUNNER_MAX and top_sc >= _WEAK_RUNNER_TOP_MIN:
        return top_c, "weak_runner_up_boost"
    return None, "ambiguous_retrieval"


def _result(
    text: str,
    grounding_mode: str,
    duration_ms: float,
    turn: ChatTurnInput,
    *,
    selected_route: str,
    route_reason: str,
    vault_used: bool = False,
    vault_operation: str | None = None,
    memory_retrieval_used: bool = False,
    memory_results_count: int = 0,
) -> ChatTurnResult:
    base_trace: dict[str, Any] = {
        "response_grounding_mode": grounding_mode,
        "deterministic_short_circuit": True,
        "deterministic_immediate_response": True,
        "deterministic_fact_in_llm_context": False,
        "provider_calls": 0,
        "tool_iterations": 0,
        "used_tools": False,
        "used_fallback": False,
        "duration_ms": duration_ms,
        "memory_lookup_happened": memory_retrieval_used,
        "memory_results_count": memory_results_count,
        "memory_retrieval_executed": False,
        "memory_retrieval_has_rows": memory_results_count > 0,
        "memory_substantive_injected_in_prompt": memory_retrieval_used,
        "web_required_by_selector": False,
        "web_verified_grounding_in_llm_messages": False,
        "web_grounding_in_prompt": False,
        "chat_history_message_count": len(turn.history or []),
        **build_history_trace(turn),
    }
    if vault_used and vault_operation:
        base_trace["vault_turn"] = True
        base_trace["vault_operation"] = vault_operation
    merge_canonical_decision_trace(
        base_trace,
        selected_route=selected_route,
        route_reason=route_reason,
        decision_intent="deterministic",
        deterministic_hit=True,
        vault_used=vault_used,
        memory_retrieval_used=memory_retrieval_used,
        web_required=False,
        planner_used=False,
        blocker_hard=False,
    )
    trace_blocker_gate_outcome(base_trace, gate_evaluated=False, hard_applied=False)
    trace_handoff_gate_outcome(
        base_trace, gate_evaluated=False, handoff_executed=False
    )
    return ChatTurnResult(
        ok=True,
        response_text=text,
        model="deterministic",
        provider="aihub",
        selected_mode="chat",
        usage=ProviderUsage(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            reporting_mode="unavailable",
        ),
        trace=base_trace,
        errors=[],
    )


def try_memory_fact_read_turn(
    turn: ChatTurnInput,
    mem_ctx: Dict[str, Any],
    *,
    started_monotonic: float,
) -> Optional[ChatTurnResult]:
    """Proste pytanie o fakt + jednoznaczny (lub wyraźnie dominujący) hit — bez LLM."""
    msg = (turn.message or "").strip()
    if blocks_memory_fact_recall_for_credentials(msg):
        return None
    if _requires_live_external_lookup(msg):
        return None
    if len(msg) > 280 or not MEMORY_FACT_RECALL_HINT.search(msg):
        return None
    snippet, pick_reason = _pick_dominant_memory_snippet(mem_ctx, msg)
    if not snippet:
        return None
    if is_junk_memory_content(snippet, query=msg):
        return None
    if _RE_EPISODE_QA_ECHO.search(snippet) or "||" in snippet or snippet.strip().startswith("U:"):
        return None
    if len(snippet) > 160 or "zapamiętaj" in snippet.lower():
        return None
    # Require lexical overlap for recall questions (avoid stale unrelated dominant hits).
    q_tokens = {t for t in re.findall(r"\w+", msg.lower()) if len(t) > 3}
    s_tokens = {t for t in re.findall(r"\w+", snippet.lower()) if len(t) > 3}
    if q_tokens and not (q_tokens & s_tokens) and "borys" not in snippet.lower():
        return None
    duration_ms = (time.monotonic() - started_monotonic) * 1000.0
    n_graph = int(mem_ctx.get("total") or 0)
    n_v2 = int(mem_ctx.get("memory_v2_total") or 0)
    mem_count = max(1, n_graph + n_v2)
    return _result(
        f"Z pamięci: {snippet}",
        "deterministic_memory_fact",
        duration_ms,
        turn,
        selected_route=ROUTE_DETERMINISTIC_FACT_READ,
        route_reason=pick_reason,
        memory_retrieval_used=True,
        memory_results_count=mem_count,
    )


def try_deterministic_turn(
    turn: ChatTurnInput, *, started_monotonic: float
) -> Optional[ChatTurnResult]:
    msg = (turn.message or "").strip()
    if not msg:
        return None

    duration_ms = (time.monotonic() - started_monotonic) * 1000.0

    vault_out = try_vault_turn(turn.user_id, msg)
    if vault_out is not None:
        gmode, sroute = _VAULT_ROUTE[vault_out.operation]
        return _result(
            vault_out.response_text,
            gmode,
            duration_ms,
            turn,
            selected_route=sroute,
            route_reason=f"vault_{vault_out.operation}",
            vault_used=True,
            vault_operation=vault_out.operation,
        )

    if _RE_LOCAL_HEALTH.search(msg):
        try:
            from aihub.main import ping
            from aihub.ops_platform import get_platform_health, readiness_from_health

            ping_body = ping()
            ready_body = readiness_from_health(get_platform_health())
            text = (
                f"Lokalny backend: GET /system/ping → HTTP 200, body={ping_body!s}; "
                f"GET /ops/ready → ready={ready_body.get('ready')}, body={ready_body!s}."
            )
            if re.search(r"(?iu)\b(zrestartuj|restartuj)\b", msg):
                text = (
                    "Nie mogę realnie zrestartować backendu z tego czatu — brak uprawnienia do tej akcji. "
                    "Mogę natomiast podać aktualny health:\n" + text
                )
            return _result(
                text,
                "deterministic_local_health",
                duration_ms,
                turn,
                selected_route=ROUTE_DETERMINISTIC_FACT_READ,
                route_reason="local_backend_health_probe",
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).debug("local health probe skipped: %s", exc)

    hist_reply = _history_turn(turn, msg)
    if hist_reply:
        return _result(
            hist_reply,
            "deterministic_session_history",
            duration_ms,
            turn,
            selected_route=ROUTE_DETERMINISTIC_HISTORY,
            route_reason="session_transcript_meta_query",
        )

    # An attached image means the user is talking ABOUT that image (describe/analyze),
    # not asking us to synthesize a new one. Words like "obrazek"/"obrazu" would otherwise
    # trip the image-generation intent and return a DALL·E prompt instead of using vision.
    if is_image_generation_intent(msg) and not (turn.attached_file_ids or []):
        return _result(
            build_image_generation_reply(msg),
            "deterministic_image_generation",
            duration_ms,
            turn,
            selected_route=ROUTE_DETERMINISTIC_IMAGE_GENERATION,
            route_reason="image_generation_intent",
        )

    return None
