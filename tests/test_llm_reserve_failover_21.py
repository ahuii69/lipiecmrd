"""LLM reserve provider failover tests (DeepInfra → Groq)."""

from __future__ import annotations

import pytest

from aihub.chat_contracts import ChatMessage, ChatTurnInput, ModelResponse, ProviderUsage
from aihub.llm.provider_types import ProviderChatRequest, ProviderError
from aihub.turn.models import RuntimeEnvironment
from aihub.turn.provider_service import ProviderExecutionService


def _ok_response(*, provider: str, model: str, content: str) -> ModelResponse:
    return ModelResponse(
        provider=provider,
        model=model,
        content=content,
        finish_reason="stop",
        tool_calls=[],
        usage=ProviderUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        latency_ms=1.0,
    )


class _ScriptedProvider:
    def __init__(self, name: str, script: list) -> None:
        self.provider_name = name
        self._script = list(script)
        self.calls = 0

    async def generate(self, _request: ProviderChatRequest) -> ModelResponse:
        self.calls += 1
        if not self._script:
            raise ProviderError(
                provider=self.provider_name,
                code="unexpected_call",
                message="no script",
                retryable=False,
            )
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.mark.anyio
async def test_deepinfra_200_groq_not_called():
    primary = _ScriptedProvider(
        "deepinfra",
        [_ok_response(provider="deepinfra", model="openai/gpt-oss-120b", content="ok")],
    )
    reserve = _ScriptedProvider("groq", [])
    svc = ProviderExecutionService(primary=primary, reserve=reserve)
    res = await svc.execute(
        messages=[ChatMessage(role="user", content="hi")],
        environment=RuntimeEnvironment(provider_max_attempts=1),
    )
    assert res.ok is True
    assert res.provider == "deepinfra"
    assert primary.calls == 1
    assert reserve.calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "primary_exc",
    [
        ProviderError(
            provider="deepinfra",
            code="insufficient_balance",
            message="402",
            retryable=False,
            status_code=402,
        ),
        ProviderError(
            provider="deepinfra",
            code="timeout",
            message="timeout",
            retryable=True,
            status_code=408,
        ),
        ProviderError(
            provider="deepinfra",
            code="transport",
            message="connection reset",
            retryable=True,
        ),
        ProviderError(
            provider="deepinfra",
            code="http_error",
            message="500",
            retryable=True,
            status_code=503,
        ),
        ProviderError(
            provider="deepinfra",
            code="invalid_json",
            message="bad json",
            retryable=False,
        ),
        ProviderError(
            provider="deepinfra",
            code="empty_response",
            message="empty",
            retryable=False,
        ),
    ],
)
async def test_primary_failure_groq_success(primary_exc):
    from aihub.llm.failover_policy import max_retries_before_failover

    repeats = max_retries_before_failover(primary_exc) + 1
    primary = _ScriptedProvider("deepinfra", [primary_exc] * repeats)
    reserve = _ScriptedProvider(
        "groq",
        [_ok_response(provider="groq", model="openai/gpt-oss-120b", content="groq ok")],
    )
    svc = ProviderExecutionService(primary=primary, reserve=reserve)
    res = await svc.execute(
        messages=[ChatMessage(role="user", content="hi")],
        environment=RuntimeEnvironment(provider_max_attempts=1),
    )
    assert res.ok is True
    assert res.provider == "groq"
    assert res.model == "openai/gpt-oss-120b"
    assert primary.calls == repeats
    assert reserve.calls == 1


@pytest.mark.anyio
async def test_deepinfra_402_then_groq_401_fails_turn():
    primary = _ScriptedProvider(
        "deepinfra",
        [
            ProviderError(
                provider="deepinfra",
                code="insufficient_balance",
                message="402",
                retryable=False,
                status_code=402,
            )
        ],
    )
    reserve = _ScriptedProvider(
        "groq",
        [
            ProviderError(
                provider="groq",
                code="invalid_api_key",
                message="401",
                retryable=False,
                status_code=401,
            )
        ],
    )
    svc = ProviderExecutionService(primary=primary, reserve=reserve)
    with pytest.raises(Exception):
        await svc.execute(
            messages=[ChatMessage(role="user", content="hi")],
            environment=RuntimeEnvironment(provider_max_attempts=1),
        )
    assert primary.calls == 1
    assert reserve.calls == 1


@pytest.mark.anyio
async def test_chat_runtime_402_failover_ok(monkeypatch, isolated_db):
    from aihub import chat_runtime as cr
    from aihub.chat_contracts import ChatTurnInput

    class _Primary:
        provider_name = "deepinfra"

        async def generate(self, _request):
            raise ProviderError(
                provider="deepinfra",
                code="insufficient_balance",
                message="402",
                retryable=False,
                status_code=402,
            )

    class _Reserve:
        provider_name = "groq"
        calls = 0

        async def generate(self, _request):
            self.calls += 1
            return _ok_response(
                provider="groq",
                model="openai/gpt-oss-120b",
                content="Jestem AI-Hub, rezerwowy model Groq.",
            )

    reserve = _Reserve()

    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    monkeypatch.setattr(cr, "get_default_provider", lambda: _Primary())
    from aihub.llm import provider_registry as pr

    monkeypatch.setattr(
        pr,
        "build_provider_execution_service",
        lambda primary=None: ProviderExecutionService(primary=_Primary(), reserve=reserve),
    )

    runtime = cr.ChatRuntime()
    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="u_failover",
            session_id="s1",
            message="Powiedz krótko, kim jesteś i jak działasz.",
            mode="chat",
        )
    )
    assert out.ok is True
    assert out.provider == "groq"
    assert out.model == "openai/gpt-oss-120b"
    assert reserve.calls == 1
    assert out.trace.get("provider_failover_happened") is True
    assert out.trace.get("used_fallback") is False


@pytest.mark.anyio
async def test_simple_meta_no_goals_created(isolated_db):
    from aihub.goal_engine import get_goal_engine

    ge = get_goal_engine()
    ctx = ge.build_goal_context(
        user_id="meta_user",
        input_event={"text": "Powiedz krótko, kim jesteś i jak działasz."},
        memory_context={"total": 0},
    )
    assert ctx.created_goal_ids == []
    assert ctx.selected_reason == "GOAL_SKIPPED_SIMPLE_META"


@pytest.mark.anyio
async def test_provider_failure_no_executive(monkeypatch, isolated_db):
    from aihub import chat_runtime as cr
    from aihub.response_persona_guard import dry_fallback_response

    class _Fail:
        provider_name = "deepinfra"

        async def generate(self, _request):
            raise ProviderError(
                provider="deepinfra",
                code="insufficient_balance",
                message="402",
                retryable=False,
                status_code=402,
            )

    exec_called = {"n": 0}

    class _Exec:
        async def run_cycle(self, *a, **k):
            exec_called["n"] += 1
            return {"ok": True}

    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    monkeypatch.setattr(cr, "get_executive_controller", lambda: _Exec())
    from aihub.llm import provider_registry as pr

    monkeypatch.setattr(
        pr,
        "build_provider_execution_service",
        lambda primary=None: ProviderExecutionService(primary=_Fail(), reserve=None),
    )

    runtime = cr.ChatRuntime()
    out = await runtime.run_turn(
        ChatTurnInput(user_id="u_fb", session_id="s", message="hej", mode="chat")
    )
    assert out.ok is False
    assert exec_called["n"] == 0
    assert out.response_text == dry_fallback_response(user_message="hej")
