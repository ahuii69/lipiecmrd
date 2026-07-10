"""Runtime confidence tests for agent_loop cognitive-direct and adapters."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


class _ConflictResult:
    def __init__(self, has_conflict: bool):
        self.has_conflict = has_conflict
        self.conflict_description = "conflict"
        self.severity = "high"


def test_process_decision_skip_action(monkeypatch):
    import aihub.agent_loop as al

    monkeypatch.setattr(
        al, "check_conflict", lambda *_args, **_kwargs: _ConflictResult(False)
    )

    decision = SimpleNamespace(
        action_type="skip",
        parameters={},
        confidence=0.8,
        skip_reason="low value",
    )

    out = asyncio.get_event_loop().run_until_complete(
        al.process_decision("loop_skip_user", decision, {"mood": "neutral"})
    )

    assert out["executed"] is False
    assert out["action_type"] == "skip"
    assert out["reason"] == "low value"


def test_process_decision_conflict_blocks_execution(monkeypatch):
    import aihub.agent_loop as al

    monkeypatch.setattr(
        al, "check_conflict", lambda *_args, **_kwargs: _ConflictResult(True)
    )

    decision = SimpleNamespace(
        action_type="learn",
        parameters={"fact": "x"},
        confidence=0.7,
        skip_reason=None,
    )

    out = asyncio.get_event_loop().run_until_complete(
        al.process_decision("loop_conflict_user", decision, {"mood": "neutral"})
    )

    assert out["executed"] is False
    assert "Conflict" in out["reason"]


def test_process_decision_executes_action(monkeypatch):
    import aihub.agent_loop as al

    monkeypatch.setattr(
        al, "check_conflict", lambda *_args, **_kwargs: _ConflictResult(False)
    )

    async def _fake_execute_action(_uid, _action, _params):
        return {"ok": True, "result": "done"}

    monkeypatch.setattr(al, "_execute_action", _fake_execute_action)

    decision = SimpleNamespace(
        action_type="query",
        parameters={"query": "python"},
        confidence=0.9,
        skip_reason=None,
    )

    out = asyncio.get_event_loop().run_until_complete(
        al.process_decision("loop_exec_user", decision, {"mood": "neutral"})
    )

    assert out["executed"] is True
    assert out["action_type"] == "query"


def test_run_cognitive_direct_cycle_no_messages(monkeypatch):
    import aihub.agent_loop as al

    monkeypatch.setattr(
        al,
        "get_psyche_core",
        lambda: SimpleNamespace(ensure_user=lambda _uid: {"ok": True}),
    )
    monkeypatch.setattr(al, "get_pending_messages", lambda _uid, limit=20: [])

    out = asyncio.get_event_loop().run_until_complete(
        al.run_cognitive_direct_cycle("loop_empty_user")
    )

    assert out["cycle"] == "completed"
    assert out["messages_processed"] == 0
    assert out["decisions_made"] == 0


def test_run_cognitive_direct_cycle_processes_ranked_messages(monkeypatch):
    import aihub.agent_loop as al

    monkeypatch.setattr(
        al,
        "get_psyche_core",
        lambda: SimpleNamespace(ensure_user=lambda _uid: {"ok": True}),
    )
    monkeypatch.setattr(
        al,
        "get_psyche_state",
        lambda _uid: {"mood": "neutral", "energy": 0.7, "focus": 0.6},
    )

    monkeypatch.setattr(
        al,
        "get_pending_messages",
        lambda _uid, limit=20: [
            {"content": "first"},
            {"content": ""},
            {"content": "third"},
            {"content": "fourth"},
        ],
    )

    rankings = [
        SimpleNamespace(message={"content": "first"}, urgency=0.4, relevance=0.9),
        SimpleNamespace(message={"content": ""}, urgency=0.3, relevance=0.8),
        SimpleNamespace(message={"content": "third"}, urgency=0.5, relevance=0.7),
        SimpleNamespace(message={"content": "fourth"}, urgency=0.2, relevance=0.6),
    ]
    monkeypatch.setattr(al, "rank_messages", lambda _uid, _msgs: rankings)

    async def _fake_decide(_request):
        return SimpleNamespace(
            action_type="query",
            parameters={"query": "x"},
            confidence=0.8,
            skip_reason=None,
        )

    monkeypatch.setattr(al.cognitive_controller, "decide", _fake_decide)
    monkeypatch.setattr(
        al,
        "process_decision",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"executed": True}),
    )

    out = asyncio.get_event_loop().run_until_complete(
        al.run_cognitive_direct_cycle("loop_ranked_user")
    )

    assert out["cycle"] == "completed"
    assert out["messages_processed"] == 4
    # top3 includes one empty content, so 2 executed decisions
    assert out["decisions_made"] == 2


def test_agent_cycle_adapter_prefers_legacy_then_payload(monkeypatch):
    import aihub.agent_loop as al
    import aihub.executive_controller as ec

    class _ControllerLegacy:
        async def run_cycle(self, *_args, **_kwargs):
            return {"legacy_response": {"cycle": "completed", "decisions_made": 1}}

    class _ControllerPayload:
        async def run_cycle(self, *_args, **_kwargs):
            return {
                "execution_result": {
                    "payload": {"cycle": "completed", "decisions_made": 2}
                }
            }

    def _legacy_provider():
        return _ControllerLegacy()

    monkeypatch.setattr(ec, "get_executive_controller", _legacy_provider)
    out_legacy = asyncio.get_event_loop().run_until_complete(
        al.agent_cycle("loop_adapter_user")
    )
    assert out_legacy["decisions_made"] == 1

    def _payload_provider():
        return _ControllerPayload()

    monkeypatch.setattr(ec, "get_executive_controller", _payload_provider)
    out_payload = asyncio.get_event_loop().run_until_complete(
        al.agent_cycle("loop_adapter_user")
    )
    assert out_payload["decisions_made"] == 2


def test_run_loop_stops_after_error_cycle(monkeypatch):
    import aihub.agent_loop as al

    calls = {"n": 0}

    async def _fake_agent_cycle(_uid, *_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            return {"cycle": "error", "error": "boom"}
        return {"cycle": "completed", "messages_processed": 1, "decisions_made": 1}

    monkeypatch.setattr(al, "agent_cycle", _fake_agent_cycle)

    out = asyncio.get_event_loop().run_until_complete(
        al.run_loop("hello", user_id="loop_run_user", max_iterations=5, _dry_run=False)
    )

    assert out["ok"] is True
    assert out["iterations"] == 2
    assert calls["n"] == 2
