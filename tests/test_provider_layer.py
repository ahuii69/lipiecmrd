"""Tests for provider layer (DeepInfra adapter)."""

from __future__ import annotations

import importlib
import json
import os
import ssl
from typing import Any, cast

import httpx
import pytest

from aihub.chat_contracts import ChatMessage
from aihub.llm.provider_types import (
    ProviderChatRequest,
    ProviderError,
    ProviderToolSpec,
)
from aihub.llm.providers.deepinfra_provider import DeepInfraProvider


def test_provider_registry_resolves_llm_api_key_reference_from_env_file(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DEEPINFRA_TOKEN=resolved-token",
                "LLM_API_KEY=${DEEPINFRA_TOKEN}",
                "LLM_PROVIDER_NAME=deepinfra",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    original_env_file = os.environ.get("AIHUB_ENV_FILE")

    monkeypatch.setenv("AIHUB_ENV_FILE", str(env_file))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    monkeypatch.delenv("DEEPINFRA_TOKEN", raising=False)

    import aihub.config as cfg
    import aihub.llm.provider_registry as pr

    cfg = importlib.reload(cfg)
    pr = importlib.reload(pr)

    provider = pr.get_default_provider()
    assert getattr(provider, "_api_key", "") == "resolved-token"
    assert cfg.LLM_API_KEY == "resolved-token"

    if original_env_file is None:
        monkeypatch.delenv("AIHUB_ENV_FILE", raising=False)
    else:
        monkeypatch.setenv("AIHUB_ENV_FILE", original_env_file)

    importlib.reload(cfg)
    importlib.reload(pr)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def post(self, url, headers=None, **kwargs):
        payload = kwargs.get("json")
        self.calls.append({"url": url, "headers": headers, "json": payload})
        next_item = self.responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    async def aclose(self):
        return None


@pytest.mark.anyio
async def test_deepinfra_builds_request_and_normalizes_tool_calls():
    fake = _FakeAsyncClient(
        [
            _FakeResponse(
                200,
                {
                    "id": "resp_1",
                    "model": "openai/gpt-oss-120b",
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "memory.search",
                                            "arguments": '{"query":"python","limit":3}',
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 7,
                        "total_tokens": 18,
                    },
                },
            )
        ]
    )

    provider = DeepInfraProvider(
        api_key="k",
        base_url="https://api.deepinfra.com/v1/openai",
        default_model="openai/gpt-oss-120b",
        timeout_seconds=30,
        max_retries=0,
        default_temperature=0.2,
        tool_calling_enabled=True,
        streaming_enabled=False,
        client=cast(Any, fake),
    )

    req = ProviderChatRequest(
        messages=[ChatMessage(role="user", content="Find memory")],
        model="openai/gpt-oss-120b",
        tools=[
            ProviderToolSpec(
                name="memory.search",
                description="search memory",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            )
        ],
    )

    out = await provider.generate(req)

    assert out.provider == "deepinfra"
    assert out.finish_reason == "tool_calls"
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "memory.search"
    assert out.tool_calls[0].arguments["query"] == "python"
    assert out.usage.total_tokens == 18
    assert out.usage.reporting_mode == "provider"

    sent = fake.calls[0]["json"]
    assert sent["model"] == "openai/gpt-oss-120b"
    assert isinstance(sent.get("tools"), list)


@pytest.mark.anyio
async def test_deepinfra_normalizes_http_error():
    fake = _FakeAsyncClient(
        [
            _FakeResponse(
                401,
                {
                    "error": {
                        "code": "invalid_api_key",
                        "message": "bad key",
                    }
                },
            )
        ]
    )

    provider = DeepInfraProvider(
        api_key="k",
        base_url="https://api.deepinfra.com/v1/openai",
        default_model="openai/gpt-oss-120b",
        timeout_seconds=30,
        max_retries=0,
        default_temperature=0.2,
        tool_calling_enabled=True,
        streaming_enabled=False,
        client=cast(Any, fake),
    )

    req = ProviderChatRequest(
        messages=[ChatMessage(role="user", content="hello")],
        model="openai/gpt-oss-120b",
    )

    with pytest.raises(ProviderError) as exc:
        await provider.generate(req)

    assert exc.value.provider == "deepinfra"
    assert exc.value.code == "invalid_api_key"
    assert exc.value.status_code == 401
    assert exc.value.retryable is False


@pytest.mark.anyio
async def test_deepinfra_retries_timeout_then_succeeds():
    fake = _FakeAsyncClient(
        [
            httpx.ReadTimeout("timeout"),
            _FakeResponse(
                200,
                {
                    "id": "resp_2",
                    "model": "openai/gpt-oss-120b",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "ok"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            ),
        ]
    )

    provider = DeepInfraProvider(
        api_key="k",
        base_url="https://api.deepinfra.com/v1/openai",
        default_model="openai/gpt-oss-120b",
        timeout_seconds=30,
        max_retries=1,
        default_temperature=0.2,
        tool_calling_enabled=True,
        streaming_enabled=False,
        client=cast(Any, fake),
    )

    req = ProviderChatRequest(
        messages=[ChatMessage(role="user", content="hello")],
        model="openai/gpt-oss-120b",
    )

    out = await provider.generate(req)
    assert out.content == "ok"
    assert len(fake.calls) == 2


@pytest.mark.anyio
async def test_deepinfra_extracts_text_from_content_parts():
    fake = _FakeAsyncClient(
        [
            _FakeResponse(
                200,
                {
                    "id": "resp_parts",
                    "model": "openai/gpt-oss-120b",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": [
                                    {"type": "text", "text": "Pierwsza linia."},
                                    {"type": "output_text", "text": "Druga linia."},
                                ]
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 7,
                        "total_tokens": 12,
                    },
                },
            )
        ]
    )

    provider = DeepInfraProvider(
        api_key="k",
        base_url="https://api.deepinfra.com/v1/openai",
        default_model="openai/gpt-oss-120b",
        timeout_seconds=30,
        max_retries=0,
        default_temperature=0.2,
        tool_calling_enabled=True,
        streaming_enabled=False,
        client=cast(Any, fake),
    )

    req = ProviderChatRequest(
        messages=[ChatMessage(role="user", content="hello")],
        model="openai/gpt-oss-120b",
    )

    out = await provider.generate(req)

    assert out.content == "Pierwsza linia.\nDruga linia."


@pytest.mark.anyio
async def test_deepinfra_marks_usage_unavailable_when_provider_omits_usage():
    fake = _FakeAsyncClient(
        [
            _FakeResponse(
                200,
                {
                    "id": "resp_no_usage",
                    "model": "openai/gpt-oss-120b",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "ok"},
                        }
                    ],
                },
            )
        ]
    )

    provider = DeepInfraProvider(
        api_key="k",
        base_url="https://api.deepinfra.com/v1/openai",
        default_model="openai/gpt-oss-120b",
        timeout_seconds=30,
        max_retries=0,
        default_temperature=0.2,
        tool_calling_enabled=True,
        streaming_enabled=False,
        client=cast(Any, fake),
    )

    req = ProviderChatRequest(
        messages=[ChatMessage(role="user", content="hello")],
        model="openai/gpt-oss-120b",
    )

    out = await provider.generate(req)

    assert out.usage.total_tokens == 0
    assert out.usage.reporting_mode == "unavailable"


@pytest.mark.anyio
async def test_deepinfra_async_client_uses_explicit_verify_and_trust_env(monkeypatch):
    captured: dict[str, Any] = {}

    class _CapturedAsyncClient(_FakeAsyncClient):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            super().__init__(
                [
                    _FakeResponse(
                        200,
                        {
                            "id": "resp_3",
                            "model": "openai/gpt-oss-120b",
                            "choices": [
                                {
                                    "finish_reason": "stop",
                                    "message": {"content": "ok"},
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 1,
                                "completion_tokens": 1,
                                "total_tokens": 2,
                            },
                        },
                    )
                ]
            )

    monkeypatch.setattr(
        "aihub.llm.providers.deepinfra_provider.httpx.AsyncClient",
        _CapturedAsyncClient,
    )

    provider = DeepInfraProvider(
        api_key="k",
        base_url="https://api.deepinfra.com/v1/openai",
        default_model="openai/gpt-oss-120b",
        timeout_seconds=30,
        max_retries=0,
        default_temperature=0.2,
        tool_calling_enabled=True,
        streaming_enabled=False,
    )

    req = ProviderChatRequest(
        messages=[ChatMessage(role="user", content="hello")],
        model="openai/gpt-oss-120b",
    )

    out = await provider.generate(req)

    assert out.content == "ok"
    assert captured["trust_env"] is False
    assert isinstance(captured["verify"], ssl.SSLContext)
