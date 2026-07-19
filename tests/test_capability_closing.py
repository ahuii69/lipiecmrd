"""Capability closing: escalation, whitelist pierce, forced tools, confirmations."""

from __future__ import annotations

import pytest

from aihub.agent_executor import AgentExecutor
from aihub.chat_runtime import ChatRuntime
from aihub.llm.provider_types import ProviderToolSpec
from aihub.tools.mutation_guard import block_unconfirmed_mutation, mutation_is_confirmed
from aihub.tools.policies import can_call_tool
from aihub.tools.registry import get_tool_registry
from aihub.turn.capability_escalation import (
    apply_capability_escalation,
    detect_capability_intents,
    is_external_verify_intent,
    is_local_editorial_check,
)


def test_spelling_check_is_local_not_web_verify() -> None:
    msg = "Sprawdź pisownię tego zdania."
    assert is_local_editorial_check(msg)
    assert not is_external_verify_intent(msg)
    intents = detect_capability_intents(msg)
    assert intents["local_check"] is True
    assert intents["verify"] is False
    assert intents["freshness"] is False


def test_bitcoin_price_is_external_verify() -> None:
    msg = "Sprawdź aktualną cenę Bitcoina."
    assert not is_local_editorial_check(msg)
    assert is_external_verify_intent(msg) or detect_capability_intents(msg)["freshness"]
    intents = detect_capability_intents(msg)
    assert intents["verify"] or intents["freshness"]


def test_bare_sprawdz_without_external_cue_does_not_escalate() -> None:
    dc: dict = {
        "selected_strategy": "instant",
        "web_decision": "off",
        "reason_codes": [],
    }
    apply_capability_escalation(dc, "Sprawdź pisownię tego zdania.")
    assert dc["web_decision"] == "off"
    assert dc["selected_strategy"] == "instant"
    assert "CAPABILITY_LOCAL_CHECK_NO_WEB" in dc["reason_codes"]
    assert not dc.get("capability_tools_required")


def test_detect_verify_and_freshness() -> None:
    i = detect_capability_intents("sprawdź aktualny kurs USD")
    assert i["verify"] or i["freshness"]
    assert not i["image"]


def test_detect_image_and_remember_and_execute() -> None:
    assert detect_capability_intents("narysuj kota")["image"]
    assert detect_capability_intents("zapamiętaj, że lubię kawę")["remember"]
    assert detect_capability_intents("zrób to teraz")["execute"]
    assert detect_capability_intents("tylko plan, niczego nie wykonuj")["plan_only"]


def test_detect_ingest_url() -> None:
    i = detect_capability_intents(
        "wczytaj i zapamiętaj https://example.com/docs/page"
    )
    assert i["ingest"]
    assert not i["remember"]  # ingest takes priority over bare remember


def test_apply_verify_escalates_research_and_web() -> None:
    dc: dict = {
        "selected_strategy": "instant",
        "web_decision": "off",
        "reason_codes": [],
        "forced_tool_prefixes": [],
    }
    apply_capability_escalation(dc, "sprawdź wersję Node.js")
    assert dc["web_decision"] == "required"
    assert dc["selected_strategy"] == "research"
    assert dc["escalation_use_tools"] is True
    assert dc["capability_tools_required"] is True
    assert any(
        p.startswith("research") or p.startswith("web")
        for p in dc["forced_tool_prefixes"]
    )


def test_apply_execute_forces_agentic_handoff_flag() -> None:
    dc: dict = {
        "selected_strategy": "instant",
        "web_decision": "off",
        "reason_codes": [],
    }
    apply_capability_escalation(dc, "wykonaj plan migracji teraz")
    assert dc.get("force_agent_execute") is True
    assert dc["selected_strategy"] == "agentic"
    assert dc.get("escalation_final_mode") == "planner"
    assert dc.get("mutation_auto_confirm") is False
    assert dc.get("mutation_confirmation_required") is True
    assert dc.get("respect_tool_confirmation") is True


def test_apply_image_forces_generate() -> None:
    dc: dict = {
        "selected_strategy": "instant",
        "web_decision": "off",
        "reason_codes": [],
    }
    apply_capability_escalation(dc, "narysuj absurdalnego kota")
    assert dc.get("force_image_generate") is True
    assert "image." in dc.get("forced_tool_prefixes", [])


def test_whitelist_pierced_by_forced_prefixes() -> None:
    rt = ChatRuntime()
    tools = [
        ProviderToolSpec(
            name="memory.search",
            description="m",
            input_schema={"type": "object", "properties": {}},
        ),
        ProviderToolSpec(
            name="image.generate",
            description="i",
            input_schema={"type": "object", "properties": {}},
        ),
        ProviderToolSpec(
            name="research.query",
            description="r",
            input_schema={"type": "object", "properties": {}},
        ),
    ]
    filtered = rt._apply_strategy_to_tools(
        tools,
        "instant",
        forced_tool_prefixes=["image."],
    )
    names = {t.name for t in filtered}
    assert "memory.search" in names
    assert "image.generate" in names
    assert "research.query" not in names


def test_handoff_capability_force_execute() -> None:
    rt = ChatRuntime()
    should, reason = rt._should_handoff_to_agent(
        decision_core={
            "selected_strategy": "agentic",
            "force_agent_execute": True,
            "web_decision": "off",
            "escalation_final_mode": "planner",
            "experience_handoff_bias": 0.0,
            "policy_handoff_bias": 0.0,
        },
        message="zrób to",
    )
    assert should is True
    assert "capability_force_agent_execute" in reason or "agentic" in reason


def test_handoff_research_overrides_execute() -> None:
    rt = ChatRuntime()
    should, reason = rt._should_handoff_to_agent(
        decision_core={
            "selected_strategy": "research",
            "force_agent_execute": True,
            "web_decision": "required",
            "escalation_final_mode": "planner",
            "experience_handoff_bias": 0.0,
            "policy_handoff_bias": 0.0,
        },
        message="sprawdź i zrób",
    )
    assert should is False
    assert "overrides_handoff" in reason


def test_mutation_guard_blocks_without_confirm() -> None:
    blocked = block_unconfirmed_mutation("fs.write_file", {"path": "a.txt"})
    assert blocked is not None
    assert blocked["requires_confirmation"] is True
    assert mutation_is_confirmed({"_confirmed": True}) is True
    assert block_unconfirmed_mutation("fs.write_file", {"_confirmed": True}) is None


def test_tool_router_policy_still_requires_confirmation() -> None:
    tool = get_tool_registry().get("fs.write_file")
    denied = can_call_tool(
        tool,
        mode="chat",
        include_debug=False,
        policy_overrides={"allow_sensitive_mutations": True},
        confirmed=False,
    )
    assert denied.allowed is False
    assert "confirmation" in denied.reason
    allowed = can_call_tool(
        tool,
        mode="chat",
        include_debug=False,
        policy_overrides={"allow_sensitive_mutations": True},
        confirmed=True,
    )
    assert allowed.allowed is True


@pytest.mark.asyncio
async def test_agent_executor_fs_write_requires_confirmation() -> None:
    ex = AgentExecutor()
    blocked = await ex.execute(
        "action",
        {"tool": "fs_write", "params": {"path": "note.txt", "content": "x"}},
        "test_user",
    )
    assert blocked.get("ok") is False
    assert blocked.get("requires_confirmation") is True
    assert "confirmation" in str(blocked.get("error") or "")


def test_plan_from_text_spelling_no_web() -> None:
    from aihub.agent_engine import plan_from_text

    tasks = plan_from_text("u", "Sprawdź pisownię tego zdania.")
    assert tasks == []


def test_plan_from_text_bitcoin_uses_research() -> None:
    from aihub.agent_engine import plan_from_text

    tasks = plan_from_text("u", "Sprawdź aktualną cenę Bitcoina.")
    assert any(t.get("type") in {"research.query", "web.fetch"} for t in tasks)


def test_mutation_policy_http_and_collect() -> None:
    from aihub.tools.mutation_guard import (
        block_unconfirmed_mutation,
        collect_pending_confirmations,
        evaluate_mutation,
    )

    assert evaluate_mutation("fs.write_file", confirmed=False).allowed is False
    assert evaluate_mutation("fs.write_file", confirmed=True).allowed is True
    assert block_unconfirmed_mutation("snapshot.create", {}) is not None

    from aihub.chat_contracts import ToolCallRequest, ToolCallResult

    pending = collect_pending_confirmations(
        tool_calls=[
            ToolCallRequest(
                tool_call_id="t1",
                name="fs.write_file",
                arguments={"path": "a.txt", "content": "x"},
            )
        ],
        tool_results=[
            ToolCallResult(
                tool_call_id="t1",
                name="fs.write_file",
                ok=False,
                error="policy_blocked: tool requires confirmation",
            )
        ],
    )
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "fs.write_file"
    assert pending[0]["arguments"].get("path") == "a.txt"
