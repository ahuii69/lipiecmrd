"""Fallback behavior tests for chat runtime provider failure paths."""

from __future__ import annotations

import pytest

from aihub.chat_contracts import ChatTurnInput
from aihub.llm.provider_types import ProviderError


class _FailingProvider:
    provider_name = "deepinfra"

    async def generate(self, _request):
        raise ProviderError(
            provider="deepinfra",
            code="timeout",
            message="timeout",
            retryable=True,
            status_code=504,
        )


@pytest.mark.anyio
async def test_chat_runtime_provider_failure_uses_canonical_fallback(monkeypatch):
    from aihub import chat_runtime as cr

    class _FakeController:
        def __init__(self) -> None:
            self.last_event: dict | None = None

        async def run_cycle(self, input_event, mode, user_id=None):
            self.last_event = dict(input_event)
            return {
                "ok": True,
                "mode": "run",
                "strategy": "planned_reasoning",
                "strategy_reason": "fallback",
                "planning_used": True,
                "reasoning_used": True,
                "execution_result": {
                    "action_summary": "fallback",
                    "errors": [],
                    "payload": {"steps_executed": 1},
                },
                "reflection": {"duration_ms": 1.0},
            }

    def _provider_factory():
        return _FailingProvider()

    ctrl_holder: dict[str, _FakeController] = {}

    def _controller_factory():
        c = _FakeController()
        ctrl_holder["c"] = c
        return c

    monkeypatch.setattr(cr, "get_default_provider", _provider_factory)
    monkeypatch.setattr(cr, "get_executive_controller", _controller_factory)

    runtime = cr.ChatRuntime()
    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="fallback_user", session_id="sf", message="hej", mode="chat"
        )
    )

    assert out.ok is False
    assert "fallback" in out.trace
    # 06.07 response-quality fix: the provider-failure fallback is now dry/utilitarian (no
    # personification, no theatre). It must state system readiness and ask for a concrete next step.
    from aihub.response_persona_guard import (
        contains_persona_leakage,
        dry_fallback_response,
    )

    assert out.response_text == dry_fallback_response(user_message="hej")
    assert "nie mam teraz pełnej odpowiedzi z modelu" in out.response_text
    assert not contains_persona_leakage(out.response_text)
    for token in ("kawa", "kawą", "żyję", "poezj", "nudę"):
        assert token not in out.response_text.lower()
    assert out.trace.get("used_fallback") is True
    assert out.trace.get("response_grounding_mode") == "fallback"
    fake_ctrl = ctrl_holder.get("c")
    assert fake_ctrl is not None
    assert fake_ctrl.last_event is not None
    assert fake_ctrl.last_event.get("force_strategy") == "cognitive_direct"
    assert "chat_runtime:provider_fallback" in str(
        fake_ctrl.last_event.get("force_strategy_reason") or ""
    )
