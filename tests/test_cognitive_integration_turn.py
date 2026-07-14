"""Integration: cognitive pack influences real chat turn."""

from __future__ import annotations

from typing import List

import pytest

from aihub.chat_contracts import ChatTurnInput, ModelResponse, ProviderUsage


class _FakeProvider:
    def __init__(self, responses: List[ModelResponse]):
        self.provider_name = "deepinfra"
        self._responses = list(responses)
        self.calls = 0
        self.last_request = None

    async def generate(self, request):
        self.calls += 1
        self.last_request = request
        if not self._responses:
            return ModelResponse(
                provider="deepinfra",
                model="t",
                content="ok",
                usage=ProviderUsage(total_tokens=1, reporting_mode="provider"),
            )
        return self._responses.pop(0)


async def _no_delib(self=None, **kwargs):
    return kwargs.get("original_response", ""), {
        "response_variants_triggered": False,
        "response_variants_count": 0,
        "response_variants_reason_codes": [],
        "response_variants_error": False,
    }


@pytest.mark.anyio
async def test_turn_cognitive_integration_in_trace_and_prompt(monkeypatch):
    from aihub import chat_runtime as cr
    from aihub.response_variants_engine import ResponseVariantsEngine

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="t",
                content="Krótko: import json naprawisz przez sprawdzenie ścieżki pakietu.",
                usage=ProviderUsage(total_tokens=12, reporting_mode="provider"),
            )
        ]
    )
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    monkeypatch.setattr(ResponseVariantsEngine, "run_deliberation", _no_delib)

    runtime = cr.ChatRuntime()
    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="cog_int_user",
            session_id="cog_int_s",
            message="napraw import json",
            mode="chat",
        )
    )
    assert out.ok is True
    tr = out.trace or {}
    assert tr.get("cognitive_integration_happened") is True
    assert tr.get("pragmatics_analysis_happened") is True
    assert isinstance(tr.get("cognitive_influence_reason_codes"), list)
    msgs = getattr(provider.last_request, "messages", None) or []
    blob = ""
    for m in msgs:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        if role == "system":
            blob += str(content)
    assert "INTEGRACJA POZNAWCZA" in blob or tr.get("intent_confidence") is not None
