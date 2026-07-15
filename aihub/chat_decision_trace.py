#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jednolity kontrakt decyzji / routingu w trace tur czatu (observability, jeden zestaw pól)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Zgodne z kontraktem produktowym (Sprint 2) — jedna finalna etykieta routy na turę.
ROUTE_DETERMINISTIC_HISTORY = "deterministic_history"
ROUTE_DETERMINISTIC_VAULT_STORE = "deterministic_vault_store"
ROUTE_DETERMINISTIC_VAULT_READ = "deterministic_vault_read"
ROUTE_DETERMINISTIC_VAULT_DELETE = "deterministic_vault_delete"
ROUTE_DETERMINISTIC_VAULT_LIST = "deterministic_vault_list"
ROUTE_DETERMINISTIC_FACT_READ = "deterministic_fact_read"
ROUTE_DETERMINISTIC_IMAGE_GENERATION = "deterministic_image_generation"
ROUTE_INSTANT_ANSWER = "instant_answer"
ROUTE_CONTEXTUAL_ANSWER = "contextual_answer"
ROUTE_RESEARCH_ANSWER = "research_answer"
ROUTE_AGENTIC_PLAN_EXECUTE = "agentic_plan_execute"
ROUTE_BLOCKED_HARD = "blocked_hard"
# Jawny brak ugruntowania przy wymaganym web (bez „zgadywania” z modelu).
ROUTE_WEB_REQUIRED_UNGROUNDED = "web_required_ungrounded"
# Błąd infrastruktury handoff (nie mylić z udanym agentic_plan_execute).
ROUTE_AGENT_HANDOFF_ERROR = "agent_handoff_error"

PROVIDER_TRACE_MERGE_KEYS = (
    "provider_primary",
    "provider_reserve",
    "provider_candidates",
    "provider_attempt_count",
    "provider_attempts",
    "provider_failover_happened",
    "provider_selected_final",
    "provider_final_model",
    "provider_final_ok",
    "provider_total_duration_ms",
)


def merge_provider_trace_from_builder(
    target: dict[str, Any],
    trace_builder: Any | None,
) -> dict[str, Any]:
    """Copy provider failover fields from TraceBuilder into a trace dict."""
    merged: dict[str, Any] = {}
    data = getattr(trace_builder, "_data", None) if trace_builder is not None else None
    if isinstance(data, dict):
        for pk in PROVIDER_TRACE_MERGE_KEYS:
            if pk in data:
                merged[pk] = data[pk]
    if merged:
        target.update(merged)
    return merged


def apply_provider_failure_response_trace_honesty(trace: dict[str, Any]) -> None:
    """Pre-provider modules may run; only mark response impact when LLM succeeded."""
    trace["llm_response_generated"] = False
    trace["provider_failure_prevented_llm"] = True
    trace["response_outcome_quality"] = "provider_failure_fallback"

    trace["memory_v2_context_injected"] = False
    trace["memory_v2_procedure_bias_applied"] = False
    trace["memory_v2_contradiction_guard_applied"] = False
    trace["memory_substantive_injected_in_prompt"] = False
    trace["memory_substantive_in_prompt"] = False

    trace["psyche_v2_behavior_applied"] = False
    trace["psyche_v2_pressure_applied"] = False
    trace["psyche_v2_relation_tone_applied"] = False

    if trace.get("cognitive_integration_happened"):
        trace["cognitive_integration_affected_response"] = False
        trace["cognitive_integration_happened"] = False

    if trace.get("pragmatics_analysis_happened"):
        trace["pragmatics_affected_response"] = False

    if trace.get("simulation_ran"):
        trace["simulation_affected_response"] = False

    if trace.get("policy_feedback_applied"):
        trace["policy_feedback_affected_response"] = False

    if trace.get("graph_influenced_strategy"):
        trace["graph_influenced_response"] = False


def trace_blocker_gate_outcome(
    trace: dict[str, Any],
    *,
    gate_evaluated: bool,
    hard_applied: bool,
) -> dict[str, Any]:
    """Gate blockerów: czy liczony w turze; hard_applied czy zatrzymał turę."""
    trace["blocker_gate_evaluated"] = bool(gate_evaluated)
    trace["blocker_hard_applied"] = bool(hard_applied)
    return trace


def trace_handoff_gate_outcome(
    trace: dict[str, Any],
    *,
    gate_evaluated: bool,
    handoff_executed: bool | None = None,
    skip_reason: str | None = None,
) -> dict[str, Any]:
    """Handoff: evaluated vs faktyczne wykonanie (bez udawania planera)."""
    trace["chat_handoff_evaluated"] = bool(gate_evaluated)
    if handoff_executed is not None:
        trace["chat_handoff_executed"] = bool(handoff_executed)
    if skip_reason is not None:
        trace["chat_handoff_skip_reason"] = skip_reason
    elif gate_evaluated and handoff_executed is True:
        trace["chat_handoff_skip_reason"] = None
    return trace


def _soft_blocker_route_reason_suffix(
    verdict: Mapping[str, Any] | None,
) -> str | None:
    """Bloker nie-hard, aktywny: sufiks do route_reason (jawna kontynuacja)."""
    if verdict is None:
        return None
    if bool(verdict.get("hard")):
        return None
    if not bool(verdict.get("blocker_active")):
        return None
    res = str(verdict.get("resolution") or "allow")
    if res == "allow":
        return None
    return f"blocker_evaluated_{res}_proceeded"


def _append_soft_blocker_to_reason(
    route_reason: str, verdict: Mapping[str, Any] | None
) -> str:
    suf = _soft_blocker_route_reason_suffix(verdict)
    if not suf:
        return route_reason
    return f"{route_reason};{suf}"


def merge_canonical_decision_trace(
    trace: dict[str, Any],
    *,
    selected_route: str,
    route_reason: str,
    decision_intent: str | None = None,
    deterministic_hit: bool = False,
    vault_used: bool = False,
    memory_retrieval_used: bool = False,
    web_required: bool = False,
    planner_used: bool = False,
    blocker_hard: bool | None = None,
) -> dict[str, Any]:
    """Dopina kanoniczne pola decyzji do istniejącego słownika trace (in-place)."""
    trace["selected_route"] = selected_route
    trace["route_reason"] = route_reason
    if decision_intent is not None:
        trace["decision_intent"] = decision_intent
    trace["deterministic_hit"] = bool(deterministic_hit)
    trace["vault_used"] = bool(vault_used)
    trace["memory_retrieval_used"] = bool(memory_retrieval_used)
    # Intencja selektora (``web_decision==required``), NIE to samo co zweryfikowany web w promptcie.
    trace["web_required"] = bool(web_required)
    trace["planner_used"] = bool(planner_used)
    if blocker_hard is not None:
        trace["blocker_hard"] = bool(blocker_hard)
    return trace


def _tool_name_is_research_related(name: str) -> bool:
    n = (name or "").lower()
    tokens = ("web", "research", "fetch", "browser")
    return any(tok in n for tok in tokens)


def _successful_research_tool_in_results(
    tool_results: list[Any] | None,
) -> bool:
    """Udane narzędzie web/research w turze (outcome), nie sama intencja selektora."""
    if not tool_results:
        return False
    for r in tool_results:
        if getattr(r, "ok", None) is not True:
            continue
        if _tool_name_is_research_related(str(getattr(r, "name", "") or "")):
            return True
    return False


def llm_path_verified_research_grounding(
    web_verified_grounding_in_prompt: bool,
    tool_results: list[Any] | None,
) -> bool:
    """True gdy web w messages (prefetch) lub udany tool web/research w turze."""
    if web_verified_grounding_in_prompt:
        return True
    return _successful_research_tool_in_results(tool_results)


def merge_canonical_for_llm_path(
    trace: dict[str, Any],
    *,
    decision_core: dict[str, Any],
    grounding_mode: str,
    memory_lookup_happened: bool,
    research_was_required: bool,
    tool_calls: list[Any],
    planner_executed_flag: bool = False,
    web_verified_grounding_in_prompt: bool = False,
    tool_results: list[Any] | None = None,
    used_fallback: bool = False,
    blocker_verdict_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """``selected_route`` = finalny outcome tury LLM, nie sama intencja selektora.

    ``web_required`` w trace = intencja selektora. ``ROUTE_RESEARCH_ANSWER`` tylko przy
    zweryfikowanym ugruntowaniu (web w messages lub udane narzędzia web/research).
    """
    web_dec = str(decision_core.get("web_decision") or "off")
    web_required = web_dec == "required"
    strat = str(decision_core.get("selected_strategy") or "instant")

    names = [str(getattr(c, "name", c) or "").lower() for c in (tool_calls or [])]
    planner_used = bool(planner_executed_flag) or any(
        n.startswith("planner.") or n.startswith("goal.") for n in names
    )

    verified_grounding = llm_path_verified_research_grounding(
        web_verified_grounding_in_prompt, tool_results
    )
    research_intent = bool(research_was_required) or web_required or strat == "research"

    if planner_used:
        route = ROUTE_AGENTIC_PLAN_EXECUTE
        reason = "planner_or_goal_tools_executed"
        intent = "plan"
    elif research_intent and verified_grounding:
        route = ROUTE_RESEARCH_ANSWER
        reason = "verified_web_or_successful_research_tools_in_turn"
        intent = "research"
    elif research_intent:
        route = ROUTE_CONTEXTUAL_ANSWER
        reason = "research_intent_llm_completion_without_verified_grounding"
        intent = "research"
    elif strat == "agentic":
        route = ROUTE_CONTEXTUAL_ANSWER
        reason = "strategy_agentic_llm_path_without_planner_tools"
        intent = "plan"
    elif strat == "contextual":
        route = ROUTE_CONTEXTUAL_ANSWER
        reason = "strategy_contextual_llm_path"
        intent = "memory"
    else:
        route = ROUTE_INSTANT_ANSWER
        reason = f"strategy_instant_grounding={grounding_mode}"
        intent = "direct"

    if used_fallback:
        reason = f"{reason};provider_fallback"
    reason = _append_soft_blocker_to_reason(reason, blocker_verdict_snapshot)

    return merge_canonical_decision_trace(
        trace,
        selected_route=route,
        route_reason=reason,
        decision_intent=intent,
        deterministic_hit=False,
        vault_used=False,
        memory_retrieval_used=bool(memory_lookup_happened),
        web_required=web_required,
        planner_used=planner_used,
        blocker_hard=False,
    )


def merge_canonical_executive_handoff_success(
    trace: dict[str, Any],
    *,
    decision_core: dict[str, Any],
    memory_retrieval_used: bool,
    planning_used: bool,
    blocker_verdict_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Finalna trasa po udanym wywołaniu executive — bez udawania planera."""
    web_required = str(decision_core.get("web_decision") or "off") == "required"
    if planning_used:
        rreason = _append_soft_blocker_to_reason(
            "executive_controller_handoff", blocker_verdict_snapshot
        )
        return merge_canonical_decision_trace(
            trace,
            selected_route=ROUTE_AGENTIC_PLAN_EXECUTE,
            route_reason=rreason,
            decision_intent="plan",
            deterministic_hit=False,
            vault_used=False,
            memory_retrieval_used=bool(memory_retrieval_used),
            web_required=web_required,
            planner_used=True,
            blocker_hard=False,
        )
    rreason = _append_soft_blocker_to_reason(
        "executive_handoff_without_planner_execution", blocker_verdict_snapshot
    )
    return merge_canonical_decision_trace(
        trace,
        selected_route=ROUTE_CONTEXTUAL_ANSWER,
        route_reason=rreason,
        decision_intent="plan",
        deterministic_hit=False,
        vault_used=False,
        memory_retrieval_used=bool(memory_retrieval_used),
        web_required=web_required,
        planner_used=False,
        blocker_hard=False,
    )


def merge_canonical_web_required_ungrounded(
    trace: dict[str, Any],
    *,
    memory_lookup_happened: bool,
    planner_used: bool = False,
    outcome_reason: str = "no_verified_grounding",
    blocker_verdict_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical trace: tylko po realnym prefetchu (triggered) bez zweryfikowanego wyniku.

    Nie mylić z ``web_decision=required`` bez uruchomionego prefetchu — tam trasa idzie
    normalną ścieżką LLM i ``web_explicit_fail_applied`` pozostaje false.
    """
    trace["web_grounding_outcome"] = outcome_reason
    trace["web_explicit_fail_applied"] = True
    trace["web_prefetch_executed"] = True
    trace["web_continued_after_required_without_prefetch"] = False
    trace["web_final_grounding_outcome"] = "explicit_fail_after_prefetch"
    base_rr = f"prefetch_triggered_no_verified_{outcome_reason}"
    route_reason = _append_soft_blocker_to_reason(base_rr, blocker_verdict_snapshot)
    return merge_canonical_decision_trace(
        trace,
        selected_route=ROUTE_WEB_REQUIRED_UNGROUNDED,
        route_reason=route_reason,
        decision_intent="research",
        deterministic_hit=False,
        vault_used=False,
        memory_retrieval_used=bool(memory_lookup_happened),
        web_required=True,
        planner_used=bool(planner_used),
        blocker_hard=False,
    )
