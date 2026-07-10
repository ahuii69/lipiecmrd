"""Kontrakt decyzji LLM path: finalna trasa = outcome runtime."""

from __future__ import annotations

from aihub.chat_contracts import ToolCallRequest, ToolCallResult
from aihub.chat_decision_trace import (
    ROUTE_AGENTIC_PLAN_EXECUTE,
    ROUTE_CONTEXTUAL_ANSWER,
    ROUTE_INSTANT_ANSWER,
    ROUTE_RESEARCH_ANSWER,
    merge_canonical_for_llm_path,
)


def _dc(
    *,
    strategy: str = "instant",
    web_decision: str = "off",
) -> dict:
    return {"selected_strategy": strategy, "web_decision": web_decision}


def test_merge_llm_path_web_required_without_verified_is_not_research_answer():
    tr: dict = {}
    merge_canonical_for_llm_path(
        tr,
        decision_core=_dc(strategy="instant", web_decision="required"),
        grounding_mode="tool_verified",
        memory_lookup_happened=False,
        research_was_required=False,
        tool_calls=[],
        web_verified_grounding_in_prompt=False,
        tool_results=[],
    )
    assert tr["selected_route"] == ROUTE_CONTEXTUAL_ANSWER
    assert "without_verified_grounding" in tr["route_reason"]
    assert tr["web_required"] is True
    assert tr["decision_intent"] == "research"


def test_merge_llm_path_web_verified_in_prompt_yields_research_answer():
    tr: dict = {}
    merge_canonical_for_llm_path(
        tr,
        decision_core=_dc(strategy="instant", web_decision="required"),
        grounding_mode="tool_verified",
        memory_lookup_happened=False,
        research_was_required=False,
        tool_calls=[],
        web_verified_grounding_in_prompt=True,
        tool_results=[],
    )
    assert tr["selected_route"] == ROUTE_RESEARCH_ANSWER
    assert "verified_web_or_successful_research_tools" in tr["route_reason"]


def test_merge_llm_path_successful_research_tool_counts_as_verified():
    tr: dict = {}
    merge_canonical_for_llm_path(
        tr,
        decision_core=_dc(strategy="contextual", web_decision="off"),
        grounding_mode="tool_verified",
        memory_lookup_happened=False,
        research_was_required=True,
        tool_calls=[
            ToolCallRequest(tool_call_id="1", name="web.search", arguments={"q": "x"})
        ],
        web_verified_grounding_in_prompt=False,
        tool_results=[ToolCallResult(tool_call_id="1", name="web.search", ok=True)],
    )
    assert tr["selected_route"] == ROUTE_RESEARCH_ANSWER


def test_merge_llm_path_planner_tool_wins_over_research_intent():
    tr: dict = {}
    merge_canonical_for_llm_path(
        tr,
        decision_core=_dc(strategy="research", web_decision="required"),
        grounding_mode="tool_verified",
        memory_lookup_happened=False,
        research_was_required=False,
        tool_calls=[
            ToolCallRequest(tool_call_id="p", name="planner.step", arguments={})
        ],
        web_verified_grounding_in_prompt=False,
        tool_results=[],
    )
    assert tr["selected_route"] == ROUTE_AGENTIC_PLAN_EXECUTE
    assert tr["planner_used"] is True


def test_merge_llm_path_agentic_without_planner_is_contextual():
    tr: dict = {}
    merge_canonical_for_llm_path(
        tr,
        decision_core=_dc(strategy="agentic", web_decision="off"),
        grounding_mode="full",
        memory_lookup_happened=False,
        research_was_required=False,
        tool_calls=[],
        web_verified_grounding_in_prompt=False,
        tool_results=[],
    )
    assert tr["selected_route"] == ROUTE_CONTEXTUAL_ANSWER
    assert "without_planner_tools" in tr["route_reason"]


def test_merge_llm_path_instant_no_research_intent():
    tr: dict = {}
    merge_canonical_for_llm_path(
        tr,
        decision_core=_dc(strategy="instant", web_decision="off"),
        grounding_mode="full",
        memory_lookup_happened=False,
        research_was_required=False,
        tool_calls=[],
        web_verified_grounding_in_prompt=False,
        tool_results=[],
    )
    assert tr["selected_route"] == ROUTE_INSTANT_ANSWER


def test_merge_llm_path_provider_fallback_suffix():
    tr: dict = {}
    merge_canonical_for_llm_path(
        tr,
        decision_core=_dc(strategy="instant", web_decision="off"),
        grounding_mode="fallback",
        memory_lookup_happened=False,
        research_was_required=False,
        tool_calls=[],
        web_verified_grounding_in_prompt=False,
        tool_results=[],
        used_fallback=True,
    )
    assert tr["selected_route"] == ROUTE_INSTANT_ANSWER
    assert str(tr["route_reason"]).endswith(";provider_fallback")
