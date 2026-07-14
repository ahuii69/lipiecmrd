"""Integration: pragmatics influence on full chat turn (mocked provider only)."""

from __future__ import annotations

from typing import List

import pytest

from aihub.chat_contracts import ChatTurnInput, ModelResponse, ProviderUsage, ToolCallRequest, ToolCallResult


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
                model="test",
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
async def test_turn_lody_robisz_blocks_instant_and_traces(monkeypatch):
    from aihub import chat_runtime as cr
    from aihub.response_variants_engine import ResponseVariantsEngine

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="t",
                content="Ha, dwuznacznie — o deser czy o coś innego?",
                usage=ProviderUsage(total_tokens=8, reporting_mode="provider"),
            )
        ]
    )
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    monkeypatch.setattr(ResponseVariantsEngine, "run_deliberation", _no_delib)

    runtime = cr.ChatRuntime()
    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="prag_int_lody",
            session_id="s_lody",
            message="Lody robisz?",
            mode="chat",
        )
    )
    assert out.ok is True
    trace = out.trace or {}
    assert trace.get("pragmatics_analysis_happened") is True
    assert trace.get("sexual_innuendo_detected") is True
    assert trace.get("teasing_detected") is True
    assert float(trace.get("ambiguity_score") or 0) >= 0.55
    strategy = trace.get("selected_strategy") or trace.get("strategy_after_pragmatics")
    assert strategy != "instant"
    low = (out.response_text or "").lower()
    assert "przepis" not in low
    assert "składnik" not in low and "skladnik" not in low
    assert trace.get("primary_intent") == "sexual_teasing"


@pytest.mark.anyio
async def test_turn_critic_revises_literal_recipe_once(monkeypatch):
    from aihub import chat_runtime as cr
    from aihub.response_variants_engine import ResponseVariantsEngine

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="t",
                content="Oczywiście! Przepis na lody waniliowe: składniki — mleko, cukier…",
                usage=ProviderUsage(total_tokens=20, reporting_mode="provider"),
            ),
            ModelResponse(
                provider="deepinfra",
                model="t",
                content="Chodziło Ci raczej o zaczepkę — mów śmiało.",
                usage=ProviderUsage(total_tokens=10, reporting_mode="provider"),
            ),
        ]
    )
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    monkeypatch.setattr(ResponseVariantsEngine, "run_deliberation", _no_delib)

    runtime = cr.ChatRuntime()
    # Keep recipe wording through shape so critic can fire deterministically.
    monkeypatch.setattr(
        runtime,
        "_shape_response_text",
        lambda **kwargs: kwargs.get("response_text") or "",
    )

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="prag_int_critic",
            session_id="s_critic",
            message="Lody robisz?",
            mode="chat",
        )
    )
    assert out.ok is True
    assert provider.calls >= 2  # original + one revision
    assert "przepis" not in (out.response_text or "").lower()
    assert out.trace.get("response_revision_happened") is True
    assert (out.trace.get("response_critic_score") or 100) < 70


@pytest.mark.anyio
async def test_turn_world_cup_web_query_rewritten(monkeypatch):
    from aihub import chat_runtime as cr
    from aihub.response_variants_engine import ResponseVariantsEngine

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="t",
                content="Sprawdziłem wynik meczu mistrzostw — BRAK pewnych źródeł na tę datę.",
                usage=ProviderUsage(total_tokens=12, reporting_mode="provider"),
            )
        ]
    )
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    monkeypatch.setattr(ResponseVariantsEngine, "run_deliberation", _no_delib)

    captured: dict = {}

    async def _fake_prefetch(*, turn, ctx, web_decision="off", **_kw):
        pa = (ctx.system_context or {}).get("pragmatics") or {}
        q = str(pa.get("rewritten_query_for_tools") or turn.message)
        captured["query"] = q
        captured["web_decision"] = web_decision
        call = ToolCallRequest(
            tool_call_id="cw1",
            name="research.query",
            arguments={"query": q, "research_type": "general"},
        )
        result = ToolCallResult(
            tool_call_id="cw1",
            name="research.query",
            ok=True,
            output={
                "ok": True,
                "total_results": 1,
                "total_facts": 1,
                "results": [{"title": "Match", "content": "Poland vs X final score 2-1"}],
            },
            latency_ms=1.0,
        )
        return {
            "triggered": True,
            "reason": "web_decision_required",
            "tool_name": "research.query",
            "tool_call": call,
            "tool_result": result,
            "messages": [],
        }

    runtime = cr.ChatRuntime()
    monkeypatch.setattr(runtime, "_run_controlled_web_prefetch", _fake_prefetch)

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="prag_int_wc",
            session_id="s_wc",
            message="mistrzostwa świata 2026 mecz gramy przed wczoraj",
            mode="chat",
        )
    )
    assert out.ok is True
    assert out.trace.get("web_enabled_by_pragmatics") or out.trace.get("web_query_rewritten")
    assert captured.get("web_decision") == "required"
    assert "przed wczoraj" not in (captured.get("query") or "")
    assert "przed wczoraj" not in str(out.trace.get("rewritten_query_for_tools") or "")
