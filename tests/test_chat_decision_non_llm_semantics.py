"""Semantyka Decision Core poza merge LLM: blocker, handoff, executive."""

from __future__ import annotations

from aihub.chat_contracts import ToolCallRequest, ToolCallResult
from aihub.chat_decision_trace import (
    ROUTE_AGENTIC_PLAN_EXECUTE,
    ROUTE_BLOCKED_HARD,
    ROUTE_CONTEXTUAL_ANSWER,
    ROUTE_DETERMINISTIC_HISTORY,
    ROUTE_INSTANT_ANSWER,
    ROUTE_WEB_REQUIRED_UNGROUNDED,
    merge_canonical_decision_trace,
    merge_canonical_executive_handoff_success,
    merge_canonical_for_llm_path,
    merge_canonical_web_required_ungrounded,
    trace_blocker_gate_outcome,
    trace_handoff_gate_outcome,
)


def _dc(strategy: str = "instant", web: str = "off") -> dict:
    return {"selected_strategy": strategy, "web_decision": web}


def test_blocker_evaluated_but_not_applied_llm_merge_soft_suffix():
    """Bloker downgrade + LLM: sufiks w route_reason, blocker_hard False."""
    tr: dict = {}
    verdict = {
        "hard": False,
        "blocker_active": True,
        "resolution": "downgrade",
    }
    merge_canonical_for_llm_path(
        tr,
        decision_core=_dc(),
        grounding_mode="full",
        memory_lookup_happened=False,
        research_was_required=False,
        tool_calls=[],
        blocker_verdict_snapshot=verdict,
    )
    assert tr["blocker_hard"] is False
    assert "blocker_evaluated_downgrade_proceeded" in tr["route_reason"]


def test_blocker_hard_canonical_route_reason_includes_verdict():
    """Hard gate: route_reason z metadanymi, selected_route blocked_hard."""
    tr: dict = {}
    rr = "blocker_hard_gate|type=test|source=unit|resolution=hard_block"
    merge_canonical_decision_trace(
        tr,
        selected_route=ROUTE_BLOCKED_HARD,
        route_reason=rr,
        decision_intent="blocked",
        blocker_hard=True,
    )
    assert tr["selected_route"] == ROUTE_BLOCKED_HARD
    assert "blocker_hard_gate" in tr["route_reason"]
    assert tr["blocker_hard"] is True


def test_trace_blocker_gate_evaluated_vs_hard_applied():
    tr: dict = {}
    trace_blocker_gate_outcome(tr, gate_evaluated=True, hard_applied=False)
    assert tr["blocker_gate_evaluated"] is True
    assert tr["blocker_hard_applied"] is False


def test_handoff_eligible_but_not_executed_trace_shape():
    """Pola handoff jak po gate (evaluated, not executed)."""
    tr: dict = {"strategy_selected": {}}
    trace_handoff_gate_outcome(
        tr,
        gate_evaluated=True,
        handoff_executed=False,
        skip_reason="standard_chat_sufficient",
    )
    assert tr["chat_handoff_evaluated"] is True
    assert tr["chat_handoff_executed"] is False
    assert tr["chat_handoff_skip_reason"] == "standard_chat_sufficient"


def test_handoff_executed_without_planner_success_not_agentic_route():
    """Executive bez planera: contextual, planner_used False."""
    tr: dict = {}
    merge_canonical_executive_handoff_success(
        tr,
        decision_core=_dc(strategy="agentic", web="off"),
        memory_retrieval_used=False,
        planning_used=False,
        blocker_verdict_snapshot=None,
    )
    assert tr["selected_route"] == ROUTE_CONTEXTUAL_ANSWER
    assert "without_planner_execution" in tr["route_reason"]
    assert tr["planner_used"] is False
    assert tr["decision_intent"] == "plan"


def test_handoff_executed_with_planner_success_agentic_route():
    tr: dict = {}
    merge_canonical_executive_handoff_success(
        tr,
        decision_core=_dc(strategy="agentic", web="required"),
        memory_retrieval_used=True,
        planning_used=True,
        blocker_verdict_snapshot=None,
    )
    assert tr["selected_route"] == ROUTE_AGENTIC_PLAN_EXECUTE
    assert tr["planner_used"] is True
    assert tr["web_required"] is True


def test_planner_eligible_escalation_vs_planner_actually_used():
    """Strategia agentic bez tooli vs faktyczny planner.* w turze."""
    tr: dict = {}
    merge_canonical_for_llm_path(
        tr,
        decision_core=_dc(strategy="agentic"),
        grounding_mode="full",
        memory_lookup_happened=False,
        research_was_required=False,
        tool_calls=[],
        planner_executed_flag=False,
    )
    assert tr["selected_route"] == ROUTE_CONTEXTUAL_ANSWER
    assert "without_planner_tools" in tr["route_reason"]
    assert tr["planner_used"] is False

    tr2: dict = {}
    merge_canonical_for_llm_path(
        tr2,
        decision_core=_dc(strategy="instant"),
        grounding_mode="full",
        memory_lookup_happened=False,
        research_was_required=False,
        tool_calls=[
            ToolCallRequest(tool_call_id="1", name="planner.plan", arguments={})
        ],
        planner_executed_flag=False,
        tool_results=[ToolCallResult(tool_call_id="1", name="planner.plan", ok=True)],
    )
    assert tr2["planner_used"] is True
    assert tr2["selected_route"] == ROUTE_AGENTIC_PLAN_EXECUTE


def test_non_llm_early_exit_deterministic_truthful_route():
    """Deterministic: własna trasa; blocker/handoff nie liczone w turze."""
    tr: dict = {}
    merge_canonical_decision_trace(
        tr,
        selected_route=ROUTE_DETERMINISTIC_HISTORY,
        route_reason="session_transcript_meta_query",
        decision_intent="deterministic",
        deterministic_hit=True,
        blocker_hard=False,
    )
    trace_blocker_gate_outcome(tr, gate_evaluated=False, hard_applied=False)
    trace_handoff_gate_outcome(tr, gate_evaluated=False, handoff_executed=False)
    assert tr["selected_route"] == ROUTE_DETERMINISTIC_HISTORY
    assert tr["deterministic_hit"] is True
    assert tr["blocker_gate_evaluated"] is False
    assert tr["chat_handoff_evaluated"] is False


def test_fallback_adjacent_branch_truthful_route_reason():
    """Fallback: kontrakt LLM + sufiks provider_fallback."""
    tr: dict = {}
    merge_canonical_for_llm_path(
        tr,
        decision_core=_dc(),
        grounding_mode="fallback",
        memory_lookup_happened=False,
        research_was_required=False,
        tool_calls=[],
        used_fallback=True,
        blocker_verdict_snapshot={
            "hard": False,
            "blocker_active": False,
            "resolution": "allow",
        },
    )
    assert str(tr["route_reason"]).endswith(";provider_fallback")
    assert tr["selected_route"] == ROUTE_INSTANT_ANSWER


def test_web_required_ungrounded_with_soft_blocker_suffix():
    tr: dict = {}
    merge_canonical_web_required_ungrounded(
        tr,
        memory_lookup_happened=True,
        planner_used=False,
        outcome_reason="empty_results",
        blocker_verdict_snapshot={
            "hard": False,
            "blocker_active": True,
            "resolution": "reroute",
        },
    )
    assert tr["selected_route"] == ROUTE_WEB_REQUIRED_UNGROUNDED
    assert "prefetch_triggered_no_verified_empty_results" in tr["route_reason"]
    assert "blocker_evaluated_reroute_proceeded" in tr["route_reason"]
