"""Regression: external strategy authority (ChatRuntime → Executive, no second brain)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aihub.executive_controller import (
    EXECUTION_INTENT_WORKER_MAINTENANCE,
    STRATEGY_COGNITIVE,
    STRATEGY_PLANNED,
    STRATEGY_REACTIVE,
    STRATEGY_SOURCE_EXTERNAL,
    STRATEGY_SOURCE_FALLBACK_LOCAL,
    STRATEGY_SOURCE_WORKER_DEFAULT,
    map_chat_execution_mode_to_force_strategy,
    resolve_strategy_source,
)


@pytest.mark.parametrize(
    ("mode_key", "expected"),
    [
        ("planner", STRATEGY_PLANNED),
        ("research", STRATEGY_PLANNED),
        ("memory_augmented", STRATEGY_COGNITIVE),
        ("direct", STRATEGY_COGNITIVE),
    ],
)
def test_map_chat_execution_mode_to_force_strategy(
    mode_key: str, expected: str
) -> None:
    fstr, reason = map_chat_execution_mode_to_force_strategy(
        {"execution_mode": mode_key}
    )
    assert fstr == expected
    assert mode_key in reason


def test_perception_strategy_authority_external() -> None:
    from aihub.executive_controller import PerceptionInput

    p = PerceptionInput.from_event(
        {"force_strategy": STRATEGY_REACTIVE, "text": "x"},
        "run",
        user_id="u1",
    )
    assert p.strategy_authority_external() is True

    p2 = PerceptionInput.from_event({"text": "x"}, "run", user_id="u1")
    assert p2.strategy_authority_external() is False


def test_resolve_strategy_source_external_fallback_worker() -> None:
    from aihub.executive_controller import PerceptionInput

    ext = PerceptionInput.from_event(
        {"force_strategy": STRATEGY_PLANNED}, "run", user_id="u"
    )
    assert resolve_strategy_source(ext) == STRATEGY_SOURCE_EXTERNAL

    http = PerceptionInput.from_event({"text": "hi"}, "run", user_id="u")
    assert resolve_strategy_source(http) == STRATEGY_SOURCE_FALLBACK_LOCAL

    wk = PerceptionInput.from_event(
        {
            "execution_intent_source": EXECUTION_INTENT_WORKER_MAINTENANCE,
            "source": "agent_worker",
        },
        "tick",
        user_id="u",
    )
    assert resolve_strategy_source(wk) == STRATEGY_SOURCE_WORKER_DEFAULT


@pytest.mark.anyio
async def test_force_strategy_blocks_v2_contradiction_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With force_strategy set, Memory V2 contradictions must not switch to planned."""
    import aihub.executive_controller as ec
    from aihub.executive_controller import ExecutiveController

    async def _noop_decide(_req):
        return SimpleNamespace(
            action_type="noop", confidence=0.5, reasoning="telemetry only"
        )

    ctrl = ExecutiveController()
    monkeypatch.setattr(ctrl, "_cognitive", SimpleNamespace(decide=_noop_decide))

    def _fake_summarize(_uid: str, _q: str) -> dict:
        return {
            "available": True,
            "contradictions_count": 5,
            "actionable_contradictions_count": 5,
            "procedures_count": 0,
            "mode": "neutral",
            "relation_trust": 0.5,
        }

    def _fake_psyche_summarize(_uid: str) -> dict:
        return {"mode": "neutral", "relation_trust": 0.5}

    def _fake_identity(_uid: str, _q: str):
        return None

    def _fake_mem_ctx(_uid: str, _q: str):
        return None

    def _fake_psyche_ctx(_uid: str):
        return None

    monkeypatch.setattr(
        "aihub.runtime_memory_bridge.summarize_memory_v2_for_agent", _fake_summarize
    )
    monkeypatch.setattr(
        "aihub.runtime_psyche_bridge.summarize_psyche_v2_for_agent",
        _fake_psyche_summarize,
    )
    monkeypatch.setattr(
        "aihub.runtime_identity_bridge.build_identity_bridge_snapshot",
        _fake_identity,
    )
    monkeypatch.setattr(
        "aihub.runtime_memory_bridge.build_memory_v2_runtime_context",
        _fake_mem_ctx,
    )
    monkeypatch.setattr(
        "aihub.runtime_psyche_bridge.build_psyche_v2_behavior_context",
        _fake_psyche_ctx,
    )
    monkeypatch.setattr(
        ec, "select_strategy", lambda **_kw: SimpleNamespace(trace_payload={})
    )

    out = await ctrl.run_cycle(
        {
            "text": "hello",
            "force_strategy": STRATEGY_REACTIVE,
            "dry_run": True,
        },
        mode="run",
        user_id="ext_auth_user",
    )
    assert out.get("strategy_authority_external") is True
    assert out.get("strategy_source") == STRATEGY_SOURCE_EXTERNAL
    assert out.get("strategy") == STRATEGY_REACTIVE


@pytest.mark.anyio
async def test_tick_without_force_still_selects_reactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker-style tick: no force_strategy → local _select_strategy may pick reactive."""
    import aihub.executive_controller as ec
    from aihub.executive_controller import ExecutiveController
    from aihub.goal_engine import GoalContext

    async def _noop_decide(_req):
        return SimpleNamespace(
            action_type="noop", confidence=0.5, reasoning="telemetry only"
        )

    ctrl = ExecutiveController()
    monkeypatch.setattr(ctrl, "_cognitive", SimpleNamespace(decide=_noop_decide))
    monkeypatch.setattr(
        ctrl._goals,
        "build_goal_context",
        lambda **kw: GoalContext(user_id=str(kw.get("user_id") or "tick_user")),
    )
    monkeypatch.setattr(
        ec,
        "select_strategy",
        lambda **_kw: SimpleNamespace(trace_payload={}),
    )

    out = await ctrl.run_cycle(
        {
            "max_stm": 5,
            "max_tasks": 2,
            "dry_run": True,
        },
        mode="tick",
        user_id="tick_user",
    )
    assert out.get("strategy_authority_external") is False
    assert out.get("strategy_source") == STRATEGY_SOURCE_FALLBACK_LOCAL
    assert out.get("strategy") == STRATEGY_REACTIVE


@pytest.mark.anyio
async def test_worker_maintenance_tick_strategy_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aihub.executive_controller as ec
    from aihub.executive_controller import ExecutiveController
    from aihub.goal_engine import GoalContext

    async def _noop_decide(_req):
        return SimpleNamespace(
            action_type="noop", confidence=0.5, reasoning="telemetry only"
        )

    ctrl = ExecutiveController()
    monkeypatch.setattr(ctrl, "_cognitive", SimpleNamespace(decide=_noop_decide))
    monkeypatch.setattr(
        ctrl._goals,
        "build_goal_context",
        lambda **kw: GoalContext(user_id=str(kw.get("user_id") or "wuser")),
    )
    monkeypatch.setattr(
        ec,
        "select_strategy",
        lambda **_kw: SimpleNamespace(trace_payload={}),
    )

    out = await ctrl.run_cycle(
        {
            "max_stm": 3,
            "max_tasks": 2,
            "source": "agent_worker",
            "execution_intent_source": EXECUTION_INTENT_WORKER_MAINTENANCE,
            "dry_run": True,
        },
        mode="tick",
        user_id="wuser",
    )
    assert out.get("strategy_source") == STRATEGY_SOURCE_WORKER_DEFAULT
    assert out.get("strategy_authority_external") is False


def _handoff_decision_core_planner() -> dict:
    return {
        "selected_strategy": "agentic",
        "execution_mode": "planner",
        "escalation_final_mode": "planner",
        "escalation_path": {"final_mode": "planner"},
        "strategy_selected": {"strategy": "agentic"},
        "selector_output_snapshot": {},
        "reason_codes": [],
        "strategy_confidence": 0.8,
        "strategy_degraded": False,
        "strategy_hints": "",
        "simulation_ran": False,
        "simulation_best_action": None,
        "simulation_variants_count": 0,
        "simulation_risk_summary": "",
        "policy_hints_loaded": False,
        "policy_profile_name": "",
        "consistency_check_ran": False,
        "consistency_classification": "",
        "contradictions_found": 0,
        "web_decision": "off",
        "web_decision_reason": "",
    }


@pytest.mark.anyio
async def test_handoff_passes_force_strategy_from_canon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from aihub import chat_runtime as cr
    from aihub.chat_contracts import ChatTurnInput

    captured: dict[str, dict] = {}

    class _FC:
        async def run_cycle(self, input_event, mode, user_id=None):
            captured["event"] = dict(input_event)
            return {
                "ok": True,
                "cycle_id": "handoff-test",
                "strategy": STRATEGY_PLANNED,
                "strategy_authority_external": True,
                "planning_executed": True,
                "planning_attempted": True,
                "reasoning_executed": False,
                "reasoning_attempted": True,
                "execution_result": {
                    "action_summary": "ok",
                    "errors": [],
                    "payload": {"steps_executed": 0},
                },
                "execution_plan": {"tasks": []},
                "context_signals": {},
                "reflection": {"duration_ms": 1.0},
            }

    monkeypatch.setattr(cr, "get_executive_controller", lambda: _FC())

    def _fake_build(cycle, *, include_debug=False):
        return {
            "ok": True,
            "planning_used": True,
            "reasoning_used": False,
            "execution_summary": {"action_summary": "ok", "steps_executed": 0},
            "errors": [],
            "trace": {"cycle_id": cycle.get("cycle_id", "")},
        }

    monkeypatch.setattr(cr, "build_agent_cycle_response", _fake_build)
    monkeypatch.setattr(cr, "append_event", lambda *a, **k: None)

    rt = cr.ChatRuntime()
    monkeypatch.setattr(rt, "_run_runtime_experience_feedback", lambda *a, **k: None)

    await rt._execute_agent_handoff(
        turn=ChatTurnInput(user_id="uh", session_id="sh", message="zaplanuj kroki"),
        decision_core=_handoff_decision_core_planner(),
        handoff_reason="test_planner",
        started=time.monotonic(),
        psyche_snapshot={},
    )
    ev = captured.get("event") or {}
    assert ev.get("force_strategy") == STRATEGY_PLANNED
    assert "chat_runtime:agent_handoff" in str(ev.get("force_strategy_reason") or "")
