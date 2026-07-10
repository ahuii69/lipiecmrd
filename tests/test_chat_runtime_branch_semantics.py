"""Sprint 2B: semantyka gałęzi poza centralnym merge LLM (blocker, handoff, executive)."""

from __future__ import annotations

from aihub.chat_contracts import BlockerVerdict
from aihub.chat_decision_trace import (
    ROUTE_AGENT_HANDOFF_ERROR,
    ROUTE_AGENTIC_PLAN_EXECUTE,
    ROUTE_BLOCKED_HARD,
    ROUTE_CONTEXTUAL_ANSWER,
    merge_canonical_decision_trace,
    merge_canonical_executive_handoff_success,
)
from aihub.chat_runtime import ChatRuntime


def test_escalation_trace_includes_handoff_skip_when_not_executed():
    dc = {
        "strategy_selected": {},
        "execution_mode": "chat",
        "escalation_path": {},
        "escalation_use_reasoning": False,
        "escalation_use_tools": True,
        "selector_output_snapshot": {},
        "chat_handoff_evaluated": True,
        "chat_handoff_executed": False,
        "chat_handoff_skip_reason": "standard_chat_sufficient",
    }
    out = ChatRuntime._decision_core_trace_escalation(dc)
    assert out["chat_handoff_evaluated"] is True
    assert out["chat_handoff_executed"] is False
    assert out["chat_handoff_skip_reason"] == "standard_chat_sufficient"


def test_escalation_trace_omits_handoff_when_not_evaluated():
    dc = {
        "strategy_selected": {},
        "execution_mode": "chat",
        "escalation_path": {},
        "escalation_use_reasoning": False,
        "escalation_use_tools": True,
        "selector_output_snapshot": {},
    }
    out = ChatRuntime._decision_core_trace_escalation(dc)
    assert "chat_handoff_evaluated" not in out


def test_merge_executive_handoff_planner_used_vs_not():
    base_dc = {"web_decision": "off", "selected_strategy": "agentic"}
    tr_ok: dict = {}
    merge_canonical_executive_handoff_success(
        tr_ok,
        decision_core=base_dc,
        memory_retrieval_used=False,
        planning_used=True,
    )
    assert tr_ok["selected_route"] == ROUTE_AGENTIC_PLAN_EXECUTE
    assert tr_ok["planner_used"] is True
    assert tr_ok["route_reason"] == "executive_controller_handoff"

    tr_no: dict = {}
    merge_canonical_executive_handoff_success(
        tr_no,
        decision_core=base_dc,
        memory_retrieval_used=False,
        planning_used=False,
    )
    assert tr_no["selected_route"] == ROUTE_CONTEXTUAL_ANSWER
    assert tr_no["planner_used"] is False
    assert "without_planner_execution" in tr_no["route_reason"]


def test_agent_handoff_error_route_constant():
    """Błąd handoff ≠ agentic_plan_execute (brak spoofingu planera)."""
    assert ROUTE_AGENT_HANDOFF_ERROR == "agent_handoff_error"


def test_blocker_evaluated_caution_not_hard_low_confidence():
    """Blocker oceniony (active) ale nie twardy — ścieżka może iść dalej."""
    dc = {
        "selected_strategy": "agentic",
        "reason_codes": [],
        "strategy_confidence": 0.40,
        "strategy_degraded": False,
        "simulation_ran": False,
        "simulation_best_action": None,
        "simulation_variants_count": 0,
        "simulation_risk_summary": None,
        "policy_hints_loaded": False,
        "policy_profile_name": None,
        "policy_hints": [],
        "consistency_check_ran": False,
        "consistency_classification": None,
        "contradictions_found": 0,
        "experience_blocker_reason": None,
        "experience_blocker_severity": 0.0,
        "experience_recurring_failure_detected": False,
        "experience_recurring_failure_types": [],
        "policy_blocker_sensitivity": 0.0,
    }
    v = ChatRuntime._evaluate_blocker_verdict(dc)
    assert v.blocker_active is True
    assert v.hard is False
    assert v.blocker_type == "low_confidence_decision"
    assert v.resolution == "reroute"


def test_blocker_hard_route_reason_documents_verdict():
    tr: dict = {}
    bv = BlockerVerdict(
        blocker_active=True,
        blocker_type="repeated_failure",
        blocker_scope="turn",
        blocker_severity="hard",
        hard=True,
        resolution="hard_block",
        reason="x",
        source="experience_signal",
    )
    merge_canonical_decision_trace(
        tr,
        selected_route=ROUTE_BLOCKED_HARD,
        route_reason=(
            f"blocker_hard_gate|type={bv.blocker_type}|source={bv.source}|"
            f"resolution={bv.resolution}"
        ),
        decision_intent="blocked",
        deterministic_hit=False,
        vault_used=False,
        memory_retrieval_used=False,
        web_required=False,
        planner_used=False,
        blocker_hard=True,
    )
    assert "repeated_failure" in tr["route_reason"]
    assert "experience_signal" in tr["route_reason"]
    assert tr["blocker_hard"] is True
