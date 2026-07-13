"""Unit tests for turn pipeline: idempotency, tools args, provider classify, concurrency."""

from __future__ import annotations

import asyncio
import json

import pytest

from aihub.turn.errors import ProviderTimeoutError, ToolArgumentsError
from aihub.turn.idempotency import (
    begin_or_reuse_turn,
    claim_effect,
    complete_turn,
    ensure_turn_schema,
    finish_effect,
)
from aihub.turn.models import RuntimeEnvironment, ExecutionMode, new_turn_id
from aihub.turn.provider_service import classify_provider_error
from aihub.turn.tool_service import parse_tool_arguments
from aihub.turn.concurrency import session_turn_lock, ensure_lock_schema
from aihub.turn.trace import TraceBuilder, redact


def test_runtime_environment_audit_disables_writebacks():
    env = RuntimeEnvironment.from_explicit_mode("audit")
    assert env.mode == ExecutionMode.AUDIT
    assert env.allow_write_backs is False
    assert env.allow_side_effects is False


def test_parse_tool_arguments_dict_json_empty_and_errors():
    assert parse_tool_arguments({"a": 1}, max_bytes=1000) == {"a": 1}
    assert parse_tool_arguments('{"b":2}', max_bytes=1000) == {"b": 2}
    assert parse_tool_arguments("", max_bytes=1000) == {}
    assert parse_tool_arguments(None, max_bytes=1000) == {}
    with pytest.raises(ToolArgumentsError):
        parse_tool_arguments("{not-json", max_bytes=1000)
    with pytest.raises(ToolArgumentsError):
        parse_tool_arguments("[1,2,3]", max_bytes=1000)
    with pytest.raises(ToolArgumentsError):
        parse_tool_arguments("x" * 10_000, max_bytes=100)


def test_classify_provider_errors():
    code, retryable, cls = classify_provider_error(TimeoutError("timed out"))
    assert code == "provider_timeout" and retryable and cls is ProviderTimeoutError
    code, retryable, _ = classify_provider_error(RuntimeError("429 rate limit"))
    assert code == "provider_rate_limit" and retryable
    code, retryable, _ = classify_provider_error(RuntimeError("401 unauthorized"))
    assert code == "provider_auth" and not retryable
    code, retryable, _ = classify_provider_error(TypeError("bug"))
    assert code == "provider_internal_bug" and not retryable


def test_idempotency_reuse_and_effects(isolated_db):
    from aihub.turn import idempotency as tidem
    from aihub.turn import concurrency as tconc

    tidem._SCHEMA_READY = False
    tconc._LOCK_TABLE_READY = False
    ensure_turn_schema()
    tid = new_turn_id()
    key = f"test-idem-{tid}"
    first = begin_or_reuse_turn(
        turn_id=tid,
        idempotency_key=key,
        user_id="u1",
        session_id="s1",
    )
    assert first["action"] == "started"
    complete_turn(tid, status="succeeded", result={"ok": True, "response_text": "hi", "model": "m", "provider": "p", "tool_calls": [], "tool_results": [], "selected_mode": "chat", "usage": {}, "trace": {}, "errors": []})
    # Need valid ChatTurnResult shape for model_validate in application — unit test only checks gate
    second = begin_or_reuse_turn(
        turn_id=new_turn_id(),
        idempotency_key=key,
        user_id="u1",
        session_id="s1",
    )
    assert second["action"] == "reuse"
    assert second["turn_id"] == tid

    claim1 = claim_effect(tid, "memory_v2")
    assert claim1["action"] == "execute"
    finish_effect(tid, "memory_v2", ok=True, result={"n": 1})
    claim2 = claim_effect(tid, "memory_v2")
    assert claim2["action"] == "skip"
    assert claim2["result"]["n"] == 1


def test_session_lock_serializes(isolated_db):
    from aihub.turn import concurrency as tconc

    tconc._LOCK_TABLE_READY = False
    ensure_lock_schema()
    order: list[int] = []

    def worker(n: int) -> None:
        with session_turn_lock(user_id="u", session_id="s", turn_id=f"t{n}", timeout_s=10):
            order.append(n)
            import time

            time.sleep(0.05)
            order.append(n + 100)

    import threading

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # Serialized: either 1,101,2,102 or 2,102,1,101 — never interleaved mid-critical
    assert order in ([1, 101, 2, 102], [2, 102, 1, 101])


def test_trace_redacts_secrets():
    tb = TraceBuilder(turn_id="t1", request_id="r1")
    tb.merge({"authorization": "Bearer secret-token", "ok": True})
    out = tb.build()
    assert out["authorization"] == "[REDACTED]"
    assert out["schema_version"]
    assert out["turn_id"] == "t1"
    assert redact({"api_key": "x", "nested": {"password": "y"}})["api_key"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_application_idempotent_replay(isolated_db, monkeypatch):
    from aihub.chat_contracts import ChatTurnInput, ChatTurnResult
    from aihub.turn.application import ChatTurnApplicationService

    calls = {"n": 0}

    class FakeOps:
        _active_turn_ctx = None
        _active_trace_builder = None

        async def run_turn_core(self, turn: ChatTurnInput) -> ChatTurnResult:
            calls["n"] += 1
            return ChatTurnResult(
                ok=True,
                response_text="once",
                model="m",
                provider="p",
                selected_mode="chat",
                trace={"path": "fake"},
            )

        def _apply_persona_guard(self, turn, res):
            return None

    app = ChatTurnApplicationService(FakeOps())
    turn = ChatTurnInput(
        user_id="u_idem",
        session_id="s_idem",
        message="hello idem",
        idempotency_key="fixed-key-123",
    )
    r1 = await app.execute(turn)
    r2 = await app.execute(turn)
    assert r1.response_text == "once"
    assert r2.response_text == "once"
    assert calls["n"] == 1
