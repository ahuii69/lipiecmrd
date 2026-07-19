#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression: extract assistant text; never ``str(dict)`` ChatTurnContext into response_text.

CASE1 / CASE2 (probe inputs that previously leaked via ``str(dict)``):
  CASE1: ``{"content": <ChatTurnContext-shaped dict>, "model": "m"}``
  CASE2: ``{"message": {"content": <same dict>}, "model": "m"}``

Empty extracted text is correct: the payload has no text leaves
(``text`` / ``content`` / ``value`` / ``output_text`` with prose). That is extraction,
not field-name masking. ``normalize_model_response`` then raises retryable
``provider_non_text_content`` so failover sees a rejected payload.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from aihub.chat_contracts import ChatMessage, ChatTurnInput, ModelResponse, ProviderUsage
from aihub.llm.failover_policy import is_failover_eligible
from aihub.response_runtime_guard import extract_assistant_text
from aihub.turn.errors import ProviderExecutionError
from aihub.turn.models import RuntimeEnvironment
from aihub.turn.provider_service import ProviderExecutionService, normalize_model_response


# Exact shape used in the original leak probe (ChatTurnContext / debug.context fields).
RUNTIME_CTX = {
    "user_id": "u",
    "session_id": "s",
    "mode": "chat",
    "include_debug": True,
    "memory_context": {"stm": [], "total": 0},
    "system_context": {
        "memory_context_pack": {"selected_ids": ["m1"], "used_chars": 12},
        "identity_bridge_snapshot": {"loaded": True},
        "correction_turn_trace": {"recorded": False},
        "assistant_meta_ask": False,
        "memory_context_pack_prompt": "PAMIĘĆ: test",
    },
    "capabilities": [],
}

CASE1_INPUT = {"content": RUNTIME_CTX, "model": "m"}
CASE2_INPUT = {"message": {"content": RUNTIME_CTX}, "model": "m"}


# --- CASE1 / CASE2: exact inputs + why empty is correct ---


def test_case1_case2_inputs_documented():
    assert CASE1_INPUT["content"] is RUNTIME_CTX
    assert CASE2_INPUT["message"]["content"] is RUNTIME_CTX
    # No prose leaves → extraction yields empty string (not str(dict)).
    assert extract_assistant_text(RUNTIME_CTX) == ""
    assert "system_context" not in extract_assistant_text(RUNTIME_CTX)


def test_case1_normalize_rejects_non_text_payload():
    with pytest.raises(ProviderExecutionError) as ei:
        normalize_model_response(CASE1_INPUT, provider_name="mock", default_model="m")
    assert ei.value.info.code == "provider_non_text_content"
    assert ei.value.retryable is True
    assert is_failover_eligible(ei.value) is True


def test_case2_normalize_rejects_non_text_payload():
    with pytest.raises(ProviderExecutionError) as ei:
        normalize_model_response(CASE2_INPUT, provider_name="mock", default_model="m")
    assert ei.value.info.code == "provider_non_text_content"
    assert is_failover_eligible(ei.value) is True


# --- Legal content must survive ---


def test_plain_text():
    out = normalize_model_response(
        {"content": "Normalna odpowiedź asystenta.", "model": "m"},
        provider_name="mock",
        default_model="m",
    )
    assert out.content == "Normalna odpowiedź asystenta."


def test_openai_style_message_content():
    out = normalize_model_response(
        {"message": {"content": "Z message.content"}, "model": "m"},
        provider_name="mock",
        default_model="m",
    )
    assert out.content == "Z message.content"


def test_content_as_list_of_text_blocks():
    out = normalize_model_response(
        {
            "content": [
                {"type": "text", "text": "Pierwszy."},
                {"type": "text", "text": "Drugi."},
            ],
            "model": "m",
        },
        provider_name="mock",
        default_model="m",
    )
    assert out.content == "Pierwszy.\nDrugi."


def test_legal_user_json_string_kept():
    """Serialized JSON string is assistant text — never filtered by field names."""
    payload = json.dumps({"status": "ok", "items": [1, 2], "system_context": "x"}, ensure_ascii=False)
    out = normalize_model_response(
        {"content": payload, "model": "m"},
        provider_name="mock",
        default_model="m",
    )
    assert out.content == payload
    assert "system_context" in out.content


def test_legal_provider_text_dict_not_dropped():
    out = normalize_model_response(
        {"content": {"type": "text", "text": "Słownik z liściem text"}, "model": "m"},
        provider_name="mock",
        default_model="m",
    )
    assert out.content == "Słownik z liściem text"


def test_tool_calls_without_text_preserved():
    out = normalize_model_response(
        {
            "content": None,
            "model": "m",
            "tool_calls": [
                {
                    "id": "tc1",
                    "function": {"name": "web_search", "arguments": {"q": "x"}},
                }
            ],
        },
        provider_name="mock",
        default_model="m",
    )
    assert out.content == ""
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "web_search"
    assert out.tool_calls[0].arguments == {"q": "x"}


def test_tool_calls_with_runtime_dict_content_keeps_tools():
    """Structured non-text content + tools → empty text, tools intact (no raise)."""
    out = normalize_model_response(
        {
            "content": RUNTIME_CTX,
            "model": "m",
            "tool_calls": [{"id": "t1", "name": "calc", "arguments": {"a": 1}}],
        },
        provider_name="mock",
        default_model="m",
    )
    assert out.content == ""
    assert out.tool_calls[0].name == "calc"


def test_empty_provider_response():
    out = normalize_model_response(
        {"content": "", "model": "m"},
        provider_name="mock",
        default_model="m",
    )
    assert out.content == ""
    out2 = normalize_model_response(
        {"content": None, "model": "m"},
        provider_name="mock",
        default_model="m",
    )
    assert out2.content == ""


def test_serialized_runtime_string_is_not_field_filtered():
    """Post-hoc dump scrubbing was masking — strings stay as returned by provider."""
    dumped = json.dumps(RUNTIME_CTX, ensure_ascii=False)
    out = normalize_model_response(
        {"content": dumped, "model": "m"},
        provider_name="mock",
        default_model="m",
    )
    assert out.content == dumped


# --- Streaming: no empty delta spam ---


def _make_stream_provider(client):
    from aihub.llm.providers.deepinfra_provider import DeepInfraProvider

    return DeepInfraProvider(
        api_key="k",
        base_url="http://example.test",
        default_model="m",
        timeout_seconds=5.0,
        max_retries=0,
        default_temperature=0.2,
        tool_calling_enabled=True,
        streaming_enabled=True,
        client=client,
    )


@pytest.mark.asyncio
async def test_streaming_skips_empty_deltas_and_rejects_empty_final():
    class _FakeResp:
        status_code = 200

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":""}}]}'
            yield 'data: {"choices":[{"delta":{}}]}'
            yield "data: [DONE]"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _FakeClient:
        def stream(self, *args, **kwargs):
            return _FakeResp()

    emitted: list[dict] = []

    async def _capture(event):
        emitted.append(event)

    prov = _make_stream_provider(_FakeClient())
    with (
        patch("aihub.llm.providers.deepinfra_provider.chat_stream_emit", new=_capture),
        patch(
            "aihub.llm.providers.deepinfra_provider.current_stream_session",
            return_value=None,
        ),
    ):
        from aihub.llm.provider_types import ProviderChatRequest, ProviderError

        with pytest.raises(ProviderError) as ei:
            await prov.generate(
                ProviderChatRequest(
                    messages=[ChatMessage(role="user", content="hi")],
                    model="m",
                    stream=True,
                )
            )
        assert ei.value.code == "empty_response"
    assert emitted == []


@pytest.mark.asyncio
async def test_streaming_emits_non_empty_text_only():
    from aihub.llm.provider_types import ProviderChatRequest

    class _FakeResp:
        status_code = 200

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":""}}]}'
            yield 'data: {"choices":[{"delta":{"content":"Cześć"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"!"},"finish_reason":"stop"}]}'
            yield "data: [DONE]"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _FakeClient:
        def stream(self, *args, **kwargs):
            return _FakeResp()

    emitted: list[dict] = []

    async def _capture(event):
        emitted.append(event)

    prov = _make_stream_provider(_FakeClient())
    with (
        patch("aihub.llm.providers.deepinfra_provider.chat_stream_emit", new=_capture),
        patch(
            "aihub.llm.providers.deepinfra_provider.current_stream_session",
            return_value=None,
        ),
    ):
        out = await prov.generate(
            ProviderChatRequest(
                messages=[ChatMessage(role="user", content="hi")],
                model="m",
                stream=True,
            )
        )
    assert out.content == "Cześć!"
    assert [e.get("content") for e in emitted] == ["Cześć", "!"]


# --- Failover after rejected structured content ---


@pytest.mark.asyncio
async def test_failover_after_non_text_content():
    class Primary:
        provider_name = "deepinfra"
        calls = 0

        async def generate(self, _request):
            self.calls += 1
            return CASE1_INPUT

    class Reserve:
        provider_name = "groq"
        calls = 0

        async def generate(self, _request):
            self.calls += 1
            return ModelResponse(
                provider="groq",
                model="g",
                content="Rezerwa OK",
                usage=ProviderUsage(),
            )

    primary = Primary()
    reserve = Reserve()
    svc = ProviderExecutionService(primary=primary, reserve=reserve)
    res = await svc.execute(
        messages=[ChatMessage(role="user", content="hi")],
        environment=RuntimeEnvironment(provider_max_attempts=1),
    )
    assert res.ok is True
    assert res.content == "Rezerwa OK"
    assert res.provider == "groq"
    assert primary.calls == 1
    assert reserve.calls == 1


# --- History poison is not scrubbed; current turn still returns prose ---


@pytest.mark.asyncio
async def test_history_runtime_string_not_scrubbed_but_turn_uses_provider_text(monkeypatch):
    from aihub.chat_runtime import ChatRuntime

    class MockProv:
        provider_name = "mock"

        async def generate(self, request):
            # History may still contain prior dump string — we do not rewrite it.
            hist = [m for m in request.messages if m.role == "assistant"]
            assert any("system_context" in (m.content or "") for m in hist)
            return ModelResponse(
                provider="mock",
                model="m",
                content="Normalna odpowiedź asystenta.",
                usage=ProviderUsage(),
            )

    poison = json.dumps(RUNTIME_CTX, ensure_ascii=False)
    rt = ChatRuntime()
    rt._provider_service = ProviderExecutionService(primary=MockProv())

    turn = ChatTurnInput(
        user_id="runtime_leak_user",
        session_id="runtime_leak_sess",
        message="Podsumuj preferencje.",
        include_debug=True,
        history=[
            {"role": "user", "content": "wcześniej"},
            {"role": "assistant", "content": poison},
        ],
    )
    res = await rt.run_turn(turn)
    assert res.response_text == "Normalna odpowiedź asystenta."
    assert res.debug is not None
    ctx = (res.debug or {}).get("context") or {}
    assert isinstance(ctx.get("system_context"), dict)
