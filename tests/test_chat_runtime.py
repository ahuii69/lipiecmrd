"""Behavior tests for chat runtime orchestration loops."""

from __future__ import annotations

from typing import List

import pytest

from aihub.chat_contracts import (
    ChatTurnContext,
    ChatTurnInput,
    ModelResponse,
    ProviderUsage,
    ToolCallRequest,
    ToolCallResult,
)


class _FakeProvider:
    def __init__(self, responses: List[ModelResponse]):
        self.provider_name = "deepinfra"
        self._responses = list(responses)
        self.calls = 0

    async def generate(self, _request):
        self.calls += 1
        return self._responses.pop(0)


async def _no_deliberation(**kwargs):
    return kwargs.get("original_response", ""), {
        "response_variants_triggered": False,
        "response_variants_count": 0,
        "response_variants_reason_codes": [],
        "response_variants_error": False,
    }


@pytest.mark.anyio
async def test_chat_runtime_no_tool_turn(monkeypatch):
    from aihub import chat_runtime as cr

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="Cześć!",
                usage=ProviderUsage(total_tokens=3, reporting_mode="provider"),
            )
        ]
    )

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    runtime = cr.ChatRuntime()

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_no_tool", session_id="s1", message="hej", mode="chat"
        )
    )

    assert out.ok is True
    assert out.response_text == "Cześć!"
    assert out.tool_results == []
    assert out.trace.get("chat_thread_first_turn") is True
    assert out.trace.get("chat_history_message_count") == 0
    assert out.trace.get("response_grounding_mode") == "model_only"
    assert out.trace.get("used_tools") is False
    assert out.usage.reporting_mode == "provider"
    assert provider.calls == 1


@pytest.mark.anyio
async def test_chat_runtime_clamps_volvo_specs_without_user_input(monkeypatch):
    from aihub import chat_runtime as cr

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content=(
                    "Volvo XC90 z 2018 roku, 190 KM, diesel 2.0 l, "
                    "przebieg 180 tys. km, cena 120000 zł."
                ),
                usage=ProviderUsage(total_tokens=40, reporting_mode="provider"),
            )
        ]
    )

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    runtime = cr.ChatRuntime()

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_volvo_clamp",
            session_id="s_volvo",
            message="Opisz mi krótko Volvo.",
            mode="chat",
        )
    )

    assert out.ok is True
    assert out.response_text == (
        "Nie mam tych danych — podaj szczegóły."
    )
    assert out.trace.get("anti_hallucination_clamp_applied") is True
    assert out.trace.get("anti_hallucination_clamp_reason") == (
        "ungrounded_specs_or_numbers"
    )


@pytest.mark.anyio
async def test_chat_runtime_clamps_followup_without_invented_engine_specs(
    monkeypatch,
):
    from aihub import chat_runtime as cr
    from aihub.chat_contracts import ChatMessage

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="Zwykle jest to 2.0 T5 benzyna, ok. 250 KM i 350 Nm.",
                usage=ProviderUsage(total_tokens=30, reporting_mode="provider"),
            )
        ]
    )

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    runtime = cr.ChatRuntime()

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_volvo_followup",
            session_id="s_fu",
            message="A jaki silnik?",
            mode="chat",
            history=[
                ChatMessage(role="user", content="Opisz Volvo."),
                ChatMessage(
                    role="assistant",
                    content="Volvo — szwedzka marka, ogólnie znana z bezpieczeństwa.",
                ),
            ],
        )
    )

    assert out.ok is True
    assert "250" not in out.response_text
    assert out.trace.get("anti_hallucination_clamp_applied") is True


@pytest.mark.anyio
async def test_chat_runtime_model_only_rewrites_false_tool_claims(monkeypatch):
    from aihub import chat_runtime as cr

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="Sprawdziłem i pobrałem dane z narzędzi runtime.",
                usage=ProviderUsage(total_tokens=4),
            )
        ]
    )

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    runtime = cr.ChatRuntime()

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_truth_model_only",
            session_id="s_model_only",
            message="co o tym myślisz?",
            mode="chat",
        )
    )

    assert out.ok is True
    assert out.trace.get("response_grounding_mode") == "model_only"
    assert "nie uruchamiałem narzędzi" in out.response_text.lower()
    assert "sprawdziłem" not in out.response_text.lower()


@pytest.mark.anyio
async def test_chat_runtime_single_tool_turn(monkeypatch):
    from aihub import chat_runtime as cr

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="",
                tool_calls=[
                    ToolCallRequest(
                        tool_call_id="tc1",
                        name="memory.search",
                        arguments={"query": "python", "limit": 3},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="Znalazłem kontekst.",
            ),
        ]
    )

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    runtime = cr.ChatRuntime()
    # Bypass handoff gate — this test covers the provider→tool execution path.
    # Handoff gate is tested separately in test_variant_b_integration.py.
    monkeypatch.setattr(
        runtime, "_should_handoff_to_agent", lambda **kw: (False, "test_no_handoff")
    )

    async def _no_controlled_web(**_kw):
        return {
            "triggered": False,
            "reason": "test_disabled",
            "tool_name": None,
            "tool_call": None,
            "tool_result": None,
            "messages": [],
        }

    monkeypatch.setattr(
        runtime,
        "_run_controlled_web_prefetch",
        _no_controlled_web,
    )

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_single_tool",
            session_id="s2",
            message="na początku rozmawialiśmy o Pythonie — przypomnij z pamięci",
            mode="chat",
        )
    )

    assert out.ok is True
    assert out.response_text == "Znalazłem kontekst."
    assert len(out.tool_calls) == 1
    assert len(out.tool_results) == 1
    assert out.tool_results[0].ok is True
    assert out.trace.get("response_grounding_mode") == "tool_verified"
    assert out.trace.get("used_tools") is True
    assert provider.calls == 2


@pytest.mark.anyio
async def test_chat_runtime_tool_verified_keeps_execution_claims(monkeypatch):
    from aihub import chat_runtime as cr

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="",
                tool_calls=[
                    ToolCallRequest(
                        tool_call_id="tc_exec",
                        name="memory.search",
                        arguments={"query": "python", "limit": 1},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="Sprawdziłem to narzędziem i mam wynik.",
            ),
        ]
    )

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    runtime = cr.ChatRuntime()
    # Bypass handoff gate — this test covers tool_verified grounding, not handoff.
    monkeypatch.setattr(
        runtime, "_should_handoff_to_agent", lambda **kw: (False, "test_no_handoff")
    )

    async def _no_controlled_web(**_kw):
        return {
            "triggered": False,
            "reason": "test_disabled",
            "tool_name": None,
            "tool_call": None,
            "tool_result": None,
            "messages": [],
        }

    monkeypatch.setattr(
        runtime,
        "_run_controlled_web_prefetch",
        _no_controlled_web,
    )

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_tool_verified_claim",
            session_id="s_verified",
            message="wcześniej omawialiśmy Pythona — potwierdź z pamięci",
            mode="chat",
        )
    )

    assert out.ok is True
    assert out.trace.get("response_grounding_mode") == "tool_verified"
    assert "sprawdziłem" in out.response_text.lower()


@pytest.mark.anyio
async def test_chat_runtime_capability_question_without_tool_execution(monkeypatch):
    from aihub import chat_runtime as cr

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="Użyłem narzędzi i wszystko sprawdziłem.",
                usage=ProviderUsage(total_tokens=5),
            )
        ]
    )

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    runtime = cr.ChatRuntime()

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_caps_question",
            session_id="s_caps",
            message="Jakie masz capabilities i narzędzia?",
            mode="chat",
        )
    )

    assert out.ok is True
    assert out.trace.get("response_grounding_mode") == "model_only"
    assert "mam dostęp do capability" in out.response_text.lower()
    assert "nie uruchomi" in out.response_text.lower()
    assert "użyłem" not in out.response_text.lower()


@pytest.mark.anyio
async def test_chat_runtime_multi_tool_with_partial_failure(monkeypatch):
    from aihub import chat_runtime as cr

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="",
                tool_calls=[
                    ToolCallRequest(
                        tool_call_id="tc1",
                        name="memory.search",
                        arguments={"query": "python", "limit": 3},
                    ),
                    ToolCallRequest(
                        tool_call_id="tc2",
                        name="fs.write_file",
                        arguments={"path": "x.txt", "content": "x", "overwrite": True},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="Zrobione częściowo.",
            ),
        ]
    )

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    runtime = cr.ChatRuntime()
    # Bypass handoff gate — this test covers multi-tool partial failure, not handoff.
    monkeypatch.setattr(
        runtime, "_should_handoff_to_agent", lambda **kw: (False, "test_no_handoff")
    )

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_multi_tool",
            session_id="s3",
            message="zrób dwie rzeczy",
            mode="chat",
        )
    )

    assert out.ok is True
    assert len(out.tool_results) == 2
    assert any(not r.ok for r in out.tool_results)


@pytest.mark.anyio
async def test_chat_runtime_agent_tool_delegates_canonical_controller(monkeypatch):
    import aihub.tools.registry as tr
    from aihub import chat_runtime as cr

    class _FakeController:
        async def run_cycle(self, *_args, **_kwargs):
            return {
                "ok": True,
                "mode": "run",
                "strategy": "planned_reasoning",
                "strategy_reason": "test",
                "planning_used": True,
                "reasoning_used": True,
                "execution_result": {"errors": [], "payload": {"steps_executed": 1}},
                "reflection": {"duration_ms": 1.0},
            }

    def _controller_factory():
        return _FakeController()

    monkeypatch.setattr(tr, "get_executive_controller", _controller_factory)

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="",
                tool_calls=[
                    ToolCallRequest(
                        tool_call_id="tc1",
                        name="agent.run_cycle",
                        arguments={"mode": "agent", "input_event": {"text": "x"}},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="Delegacja wykonana.",
            ),
        ]
    )

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    runtime = cr.ChatRuntime()

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_agent_tool", session_id="s4", message="run", mode="agent"
        )
    )

    assert out.ok is True
    assert len(out.tool_results) == 1
    assert out.tool_results[0].ok is True
    assert out.response_text == "Delegacja wykonana."


def test_build_memory_brief_includes_stm_when_graph_buckets_empty():
    from aihub.chat_runtime import ChatRuntime

    rt = ChatRuntime()
    brief = rt._build_memory_brief(
        {
            "total": 0,
            "stm": [
                {"role": "user", "content": "STM_UNIQUE_MARKER_X9"},
            ],
            "episodic": [],
            "semantic": [],
            "dense_hits": [],
            "graph_hits": [],
        }
    )
    assert "STM (ostatnia sesja" in brief
    assert "STM_UNIQUE_MARKER_X9" in brief


def test_build_system_prompt_first_turn_vs_continuation():
    from aihub.chat_runtime import ChatRuntime

    rt = ChatRuntime()
    ctx = ChatTurnContext(user_id="u", session_id="s", mode="chat")
    first = rt._build_system_prompt(
        ctx,
        memory_brief="(brak)",
        psyche_brief="BRAK DANYCH",
        first_turn_in_thread=True,
    )
    cont = rt._build_system_prompt(
        ctx,
        memory_brief="(brak)",
        psyche_brief="BRAK DANYCH",
        first_turn_in_thread=False,
    )
    assert "pierwsza odpowiedź" in first
    assert "kontynuacja" in cont
    assert "nie otwieraj od nowego przywitania" in cont.lower()
    assert "mordzix" in first.lower()
    # Conversation-layer persona: intelligent partner, not helpdesk / not fake-human "ziomek".
    assert "rozgarnięty ziomek" not in first.lower()
    assert "inteligentny partner" in first.lower()
    assert "nie udajesz człowieka" in first.lower() or "bez udawania człowieka" in first.lower() or "nie udawaj człowieka" in first.lower()
    low_first = first.lower()
    assert "kontrakt persony" in low_first
    assert "zakaz fałszywej biografii" in low_first
    assert "zakaz helpdesk" in low_first or "zakaz fraz helpdesk" in low_first
    assert "w czym mogę pomóc" in low_first  # banned phrase must be listed
    assert "proaktywny" in low_first


def test_build_system_prompt_listing_sales_has_no_hallucination_guard():
    from aihub.chat_runtime import ChatRuntime

    rt = ChatRuntime()
    ctx = ChatTurnContext(user_id="u", session_id="s", mode="chat")
    prompt = rt._build_system_prompt(
        ctx,
        memory_brief="(brak)",
        psyche_brief="BRAK DANYCH",
        first_turn_in_thread=False,
        listing_sales_boost=True,
    )
    low = prompt.lower()
    assert "nie wymyślaj twardych parametrów oferty" in low
    assert "brak danych" in low
    assert "nie wpisuj też „stan dobry”" in low


def test_build_system_prompt_general_truth_and_psyche_boundaries() -> None:
    from aihub.chat_runtime import ChatRuntime

    rt = ChatRuntime()
    ctx = ChatTurnContext(user_id="u", session_id="s", mode="chat")
    prompt = rt._build_system_prompt(
        ctx,
        memory_brief="(brak)",
        psyche_brief="BRAK DANYCH",
        first_turn_in_thread=False,
    )
    low = prompt.lower()
    assert "global" in low
    assert "copy" in low and "rewrite" in low
    assert "nie wolno" in low
    assert "nie wymyślaj brakujących konkretów" in low
    assert "jeśli użytkownik nie podał danych" in low
    assert "psyche ma rolę pomocniczą" in low
    assert "bez pseudo-terapii" in low


def test_local_non_research_guardrails_keep_followup_local_and_prices_web() -> None:
    from aihub.chat_contracts import ChatTurnInput
    from aihub.chat_runtime import ChatRuntime

    rt = ChatRuntime()
    dc = {
        "selected_strategy": "agentic",
        "web_decision": "optional",
        "web_decision_reason": "agentic_may_need_web",
        "reason_codes": [],
    }
    rt._local_non_research_guardrails(
        ChatTurnInput(
            user_id="u",
            session_id="s",
            message="Popraw",
            mode="chat",
            history=[
                {"role": "user", "content": "Napisz opis sprzedaży Volvo v70"},
                {"role": "assistant", "content": "Opis..."},
            ],
        ),
        dc,
    )
    assert dc["selected_strategy"] == "contextual"
    assert dc["web_decision"] == "off"

    dc2 = {
        "selected_strategy": "contextual",
        "web_decision": "off",
        "web_decision_reason": "not_required",
        "reason_codes": [],
    }
    rt._local_non_research_guardrails(
        ChatTurnInput(
            user_id="u",
            session_id="s",
            message="Jakie są dziś ceny mieszkań w Warszawie?",
            mode="chat",
            history=[],
        ),
        dc2,
    )
    assert dc2["selected_strategy"] == "research"
    assert dc2["web_decision"] == "required"
    assert "CURRENT_INFO_REQUIRED" in dc2["reason_codes"]


def test_local_guardrail_ceny_mieszkan_requires_web_without_dzis() -> None:
    """Samo słowo „ceny” w zapytaniu wymusza web (bez „dziś”)."""
    from aihub.chat_contracts import ChatTurnInput
    from aihub.chat_runtime import ChatRuntime

    rt = ChatRuntime()
    dc = {
        "selected_strategy": "instant",
        "web_decision": "off",
        "web_decision_reason": "not_required",
        "reason_codes": [],
    }
    rt._local_non_research_guardrails(
        ChatTurnInput(
            user_id="u",
            session_id="s",
            message="Jakie są ceny mieszkań w Krakowie?",
            mode="chat",
            history=[],
        ),
        dc,
    )
    assert dc["selected_strategy"] == "research"
    assert dc["web_decision"] == "required"
    assert dc["web_decision_reason"] == "freshness_guardrail"


def test_local_guardrail_widzisz_does_not_false_trigger_web() -> None:
    """Word-boundary match: 'widzisz' must NOT match the 'dzis' freshness keyword."""
    from aihub.chat_contracts import ChatTurnInput
    from aihub.chat_runtime import ChatRuntime

    rt = ChatRuntime()
    dc = {
        "selected_strategy": "instant",
        "web_decision": "off",
        "web_decision_reason": "not_required",
        "reason_codes": [],
    }
    rt._local_non_research_guardrails(
        ChatTurnInput(
            user_id="u",
            session_id="s",
            message="Co widzisz na tym obrazku? Opisz krótko.",
            mode="chat",
            history=[],
        ),
        dc,
    )
    assert dc["web_decision"] == "off"
    assert "CURRENT_INFO_REQUIRED" not in dc["reason_codes"]


def test_local_guardrail_image_attachment_suppresses_web_forcing() -> None:
    """A turn with an image attachment must not be forced to web research even when
    the text contains a freshness keyword — the vision path takes priority."""
    from aihub.chat_contracts import ChatTurnInput
    from aihub.chat_runtime import ChatRuntime

    rt = ChatRuntime()
    dc = {
        "selected_strategy": "instant",
        "web_decision": "off",
        "web_decision_reason": "not_required",
        "reason_codes": [],
    }
    rt._local_non_research_guardrails(
        ChatTurnInput(
            user_id="u",
            session_id="s",
            message="Co dzisiaj widać na tym zdjęciu?",
            mode="chat",
            history=[],
            attached_file_ids=["cf_img1"],
        ),
        dc,
    )
    assert dc["web_decision"] == "off"


def test_web_required_ungrounded_message_is_explanatory_not_brak_danych_web() -> None:
    from aihub.chat_runtime import ChatRuntime

    rt = ChatRuntime()
    msg = rt._web_required_ungrounded_user_message(
        outcome="empty_results",
        controlled_web={"tool_name": "research.query", "query": "Francja Belgia wynik"},
        errors=[],
    )
    low = msg.lower()
    assert "brak danych" not in low
    assert "przeszukałem" in low or "źródł" in low
    assert "francja belgia" in low or "ponownie" in low


def test_chat_runtime_count_web_sources_unwraps_fetch_tool_envelope():
    from aihub.chat_runtime import ChatRuntime

    runtime = ChatRuntime()
    result = ToolCallResult(
        tool_call_id="cw1",
        name="web.fetch_url",
        ok=True,
        output={
            "ok": True,
            "result": {
                "url": "https://example.com",
                "status": 200,
                "bytes": 528,
                "text": "Example Domain " * 20,
            },
        },
    )

    assert runtime._count_web_sources(result) == 1
    assert runtime._assess_web_result_quality(result) is True


def test_chat_runtime_count_web_sources_unwraps_research_tool_envelope():
    from aihub.chat_runtime import ChatRuntime

    runtime = ChatRuntime()
    result = ToolCallResult(
        tool_call_id="cw2",
        name="research.query",
        ok=True,
        output={
            "ok": True,
            "result": {
                "total_results": 5,
                "total_facts": 2,
                "web_provider": "brave",
            },
        },
    )

    assert runtime._count_web_sources(result) == 5
    assert runtime._assess_web_result_quality(result) is True
    assert runtime._extract_web_provider_info(result) == "brave - 5 results"


def test_chat_runtime_research_results_ground_even_without_extracted_facts():
    """Real Brave results (content injected into the prompt) must count as grounding even
    when the brittle regex fact-extractor produced 0 facts (e.g. news queries)."""
    from aihub.chat_runtime import ChatRuntime

    runtime = ChatRuntime()
    result = ToolCallResult(
        tool_call_id="cw_facts0",
        name="research.query",
        ok=True,
        output={
            "ok": True,
            "result": {
                "total_results": 5,
                "total_facts": 0,
                "web_provider": "brave",
            },
        },
    )
    assert runtime._assess_web_result_quality(result) is True
    assert runtime._count_web_sources(result) == 5


def test_chat_runtime_zero_research_results_is_not_grounded():
    from aihub.chat_runtime import ChatRuntime

    runtime = ChatRuntime()
    result = ToolCallResult(
        tool_call_id="cw_zero",
        name="research.query",
        ok=True,
        output={"ok": True, "result": {"total_results": 0, "total_facts": 0}},
    )
    assert runtime._assess_web_result_quality(result) is False


@pytest.mark.anyio
async def test_chat_runtime_synthesizes_successful_research_results(monkeypatch):
    from aihub import chat_runtime as cr

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="",
                usage=ProviderUsage(total_tokens=9),
            )
        ]
    )

    async def _successful_controlled_web(**_kw):
        call = ToolCallRequest(
            tool_call_id="controlled_web_test_news",
            name="research.query",
            arguments={
                "query": "Jakie są najnowsze wiadomości o AI dzisiaj?",
                "research_type": "general",
            },
        )
        result = ToolCallResult(
            tool_call_id=call.tool_call_id,
            name=call.name,
            ok=True,
            output={
                "ok": True,
                "result": {
                    "query": "Jakie są najnowsze wiadomości o AI dzisiaj?",
                    "total_results": 3,
                    "total_facts": 2,
                    "results": [
                        {
                            "title": "OpenAI pokazało nowy model reasoning",
                            "source": "brave",
                            "relevance": 0.92,
                            "facts_extracted": 2,
                        },
                        {
                            "title": "Google rozszerza Gemini w Workspace",
                            "source": "brave",
                            "relevance": 0.81,
                            "facts_extracted": 1,
                        },
                        {
                            "title": "Anthropic publikuje nowe funkcje Claude",
                            "source": "duckduckgo",
                            "relevance": 0.73,
                            "facts_extracted": 0,
                        },
                    ],
                },
            },
        )
        return {
            "triggered": True,
            "reason": "web_decision_required",
            "tool_name": call.name,
            "tool_call": call,
            "tool_result": result,
            "messages": [],
        }

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    monkeypatch.setattr(cr.ResponseVariantsEngine, "run_deliberation", _no_deliberation)
    runtime = cr.ChatRuntime()
    monkeypatch.setattr(
        runtime, "_should_handoff_to_agent", lambda **kw: (False, "test_no_handoff")
    )
    monkeypatch.setattr(
        runtime, "_run_controlled_web_prefetch", _successful_controlled_web
    )

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_research_synthesis",
            session_id="s_news",
            message="Jakie są najnowsze wiadomości o AI dzisiaj?",
            mode="chat",
        )
    )

    assert out.ok is True
    assert out.trace.get("controlled_web_triggered") is True
    assert out.trace.get("controlled_web_ok") is True
    assert out.trace.get("controlled_web_source_count") == 3
    assert "Wykonałem narzędzia i mam wyniki" not in out.response_text
    assert "OpenAI pokazało nowy model reasoning" in out.response_text
    assert "Google rozszerza Gemini" in out.response_text


@pytest.mark.anyio
async def test_chat_runtime_synthesizes_successful_url_fetch(monkeypatch):
    from aihub import chat_runtime as cr

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="",
                usage=ProviderUsage(total_tokens=7),
            )
        ]
    )

    html = (
        "<!doctype html><html><head><title>Example Domain</title></head>"
        "<body><h1>Example Domain</h1><p>This domain is for use in illustrative examples in documents.</p></body></html>"
    )

    async def _successful_controlled_web(**_kw):
        call = ToolCallRequest(
            tool_call_id="controlled_web_test_url",
            name="web.fetch_url",
            arguments={"url": "https://example.com"},
        )
        result = ToolCallResult(
            tool_call_id=call.tool_call_id,
            name=call.name,
            ok=True,
            output={
                "ok": True,
                "result": {
                    "url": "https://example.com",
                    "status": 200,
                    "bytes": len(html),
                    "text": html,
                },
            },
        )
        return {
            "triggered": True,
            "reason": "explicit_url",
            "tool_name": call.name,
            "tool_call": call,
            "tool_result": result,
            "messages": [],
        }

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    monkeypatch.setattr(cr.ResponseVariantsEngine, "run_deliberation", _no_deliberation)
    runtime = cr.ChatRuntime()
    monkeypatch.setattr(
        runtime, "_should_handoff_to_agent", lambda **kw: (False, "test_no_handoff")
    )
    monkeypatch.setattr(
        runtime, "_run_controlled_web_prefetch", _successful_controlled_web
    )

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_url_synthesis",
            session_id="s_url",
            message="Sprawdź ten URL: https://example.com",
            mode="chat",
        )
    )

    assert out.ok is True
    assert out.trace.get("controlled_web_triggered") is True
    assert out.trace.get("controlled_web_ok") is True
    assert out.trace.get("controlled_web_source_count") == 1
    assert "Wykonałem narzędzia i mam wyniki" not in out.response_text
    assert "Example Domain" in out.response_text
    assert "illustrative examples" in out.response_text


@pytest.mark.anyio
async def test_chat_runtime_web_required_fails_explicitly_when_results_empty(
    monkeypatch,
):
    """web_decision=required + pusty research → jawny brak ugruntowania, bez LLM „na czuja”."""
    from aihub import chat_runtime as cr

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="",
                usage=ProviderUsage(total_tokens=5),
            )
        ]
    )

    async def _empty_controlled_web(**_kw):
        call = ToolCallRequest(
            tool_call_id="controlled_web_test_empty",
            name="research.query",
            arguments={"query": "news", "research_type": "general"},
        )
        result = ToolCallResult(
            tool_call_id=call.tool_call_id,
            name=call.name,
            ok=True,
            output={
                "ok": True,
                "result": {
                    "query": "news",
                    "total_results": 0,
                    "total_facts": 0,
                    "results": [],
                },
            },
        )
        return {
            "triggered": True,
            "reason": "web_decision_required",
            "tool_name": call.name,
            "tool_call": call,
            "tool_result": result,
            "messages": [],
        }

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    monkeypatch.setattr(cr.ResponseVariantsEngine, "run_deliberation", _no_deliberation)
    runtime = cr.ChatRuntime()
    monkeypatch.setattr(
        runtime, "_should_handoff_to_agent", lambda **kw: (False, "test_no_handoff")
    )
    monkeypatch.setattr(runtime, "_run_controlled_web_prefetch", _empty_controlled_web)

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_research_empty",
            session_id="s_empty",
            message="znajdź w internecie najnowsze newsy",
            mode="chat",
        )
    )

    assert out.ok is False
    assert out.trace.get("controlled_web_triggered") is True
    assert out.trace.get("controlled_web_ok") is True
    assert out.trace.get("controlled_web_source_count") == 0
    assert out.trace.get("response_grounding_mode") == "web_required_ungrounded"
    assert out.trace.get("selected_route") == "web_required_ungrounded"
    assert out.trace.get("web_subsystem_operation") == "research_query"
    assert out.trace.get("web_explicit_fail_applied") is True
    assert out.trace.get("web_prefetch_executed") is True
    assert out.trace.get("web_continued_after_required_without_prefetch") is False
    assert "prefetch_triggered_no_verified" in (out.trace.get("route_reason") or "")
    assert "brak danych" not in (out.response_text or "").lower()
    assert "przeszukałem" in (out.response_text or "").lower() or "źródł" in (
        out.response_text or ""
    ).lower()
    assert out.trace.get("web_used") is False
    assert int(out.trace.get("sources_count") or 0) == 0
    assert "0 wyników" in (out.trace.get("web_fail_detail") or "")


def test_web_explicit_fail_only_when_required_and_triggered_and_unverified():
    """Koniunkcja AND: samo required lub sam triggered bez braku wyniku nie wystarcza."""
    from aihub.chat_runtime import ChatRuntime

    rt = ChatRuntime
    dc_r = {"web_decision": "required"}
    assert rt._web_required_grounding_unsatisfied(dc_r, {"triggered": False}) is False
    assert (
        rt._web_required_grounding_unsatisfied(
            dc_r, {"triggered": True, "ok": True, "has_results": True}
        )
        is False
    )
    assert (
        rt._web_required_grounding_unsatisfied(
            dc_r, {"triggered": True, "ok": True, "has_results": False}
        )
        is True
    )
    assert (
        rt._web_required_grounding_unsatisfied(
            dc_r, {"triggered": True, "ok": False, "has_results": None}
        )
        is True
    )
    assert (
        rt._web_required_grounding_unsatisfied(
            {"web_decision": "off"}, {"triggered": True, "ok": False}
        )
        is False
    )


def test_web_stage_trace_required_without_prefetch_vs_explicit_fail():
    from aihub.chat_runtime import ChatRuntime

    rt = ChatRuntime()
    dc = {"web_decision": "required"}
    cw_skip = {"triggered": False}
    f1 = rt._web_stage_trace_fields(dc, cw_skip, explicit_fail_applied=False)
    assert f1["web_continued_after_required_without_prefetch"] is True
    assert f1["web_explicit_fail_applied"] is False
    assert f1["web_final_grounding_outcome"] == "required_prefetch_not_run_continuing"

    cw_ok = {"triggered": True, "ok": True, "has_results": True}
    f2 = rt._web_stage_trace_fields(dc, cw_ok, explicit_fail_applied=False)
    assert f2["web_prefetch_executed"] is True
    assert f2["web_final_grounding_outcome"] == "prefetch_verified_in_thread"

    f3 = rt._web_stage_trace_fields(dc, cw_ok, explicit_fail_applied=True)
    assert f3["web_explicit_fail_applied"] is True
    assert f3["web_final_grounding_outcome"] == "explicit_fail_after_prefetch"


@pytest.mark.anyio
async def test_chat_runtime_usage_is_truthful_when_provider_omits_usage(monkeypatch):
    from aihub import chat_runtime as cr

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="Bez usage od providera.",
                usage=ProviderUsage(),
            )
        ]
    )

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    runtime = cr.ChatRuntime()

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_usage_unavailable",
            session_id="s_usage_missing",
            message="hej",
            mode="chat",
        )
    )

    assert out.ok is True
    assert out.usage.total_tokens == 0
    assert out.usage.reporting_mode == "unavailable"
    assert out.trace.get("usage_reporting_mode") == "unavailable"


@pytest.mark.anyio
async def test_chat_runtime_usage_is_partial_when_some_provider_calls_omit_usage(
    monkeypatch,
):
    from aihub import chat_runtime as cr

    provider = _FakeProvider(
        [
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="",
                tool_calls=[
                    ToolCallRequest(
                        tool_call_id="tc_usage_partial",
                        name="memory.search",
                        arguments={"query": "python", "limit": 1},
                    )
                ],
                finish_reason="tool_calls",
                usage=ProviderUsage(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    reporting_mode="provider",
                ),
            ),
            ModelResponse(
                provider="deepinfra",
                model="openai/gpt-oss-120b",
                content="Mam częściową telemetrię usage.",
                usage=ProviderUsage(),
            ),
        ]
    )

    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    runtime = cr.ChatRuntime()

    async def _no_controlled_web(**_kw):
        return {
            "triggered": False,
            "reason": "test_disabled",
            "tool_name": None,
            "tool_call": None,
            "tool_result": None,
            "messages": [],
        }

    monkeypatch.setattr(
        runtime, "_should_handoff_to_agent", lambda **kw: (False, "test_no_handoff")
    )
    monkeypatch.setattr(runtime, "_run_controlled_web_prefetch", _no_controlled_web)

    out = await runtime.run_turn(
        ChatTurnInput(
            user_id="chat_usage_partial",
            session_id="s_usage_partial",
            message="wcześniej wspominaliśmy o Pythonie — przypomnij z pamięci",
            mode="chat",
        )
    )

    assert out.ok is True
    assert out.usage.total_tokens == 15
    assert out.usage.reporting_mode == "partial"
    assert out.trace.get("usage_reporting_mode") == "partial"
