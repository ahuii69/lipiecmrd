#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""25.07 — lightweight turn cost: budget, tools-off, minimal writeback."""
from __future__ import annotations

import pytest

META = "Powiedz krótko, kim jesteś i jak działasz."
PRIOR = "A wcześniej jak opisywałeś swoje działanie?"
FEEDBACK = "Jesteś za rozwlekły, przedstaw się krócej."
PROVIDER_ASK = "Jaki provider i model obsłużył tę odpowiedź?"
RECALL = "Jak nazywa się mój pies?"
AGENTIC = "Zaplanuj migrację PostgreSQL na nowy serwer, podziel ją na etapy i śledź postęp."


def test_a_pure_meta_budget_and_prompt():
    from aihub.turn.prompt_budget import (
        build_meta_light_system_prompt,
        build_prompt_budget_trace,
        estimate_tokens,
        select_prompt_budget,
    )

    d = select_prompt_budget(user_text=META, selected_strategy="instant")
    assert d.profile == "meta_light"
    assert d.turn_value_class == "trivial"
    assert d.writeback_policy == "minimal"
    assert d.allow_tools is False
    assert d.allow_memory is False
    assert d.allow_critic_llm is False
    assert d.allow_response_variants is False
    sys_p = build_meta_light_system_prompt()
    tr = build_prompt_budget_trace(decision=d, system_text=sys_p, tool_schema_chars=0)
    assert tr["budget_profile"] == "meta_light"
    assert tr["system_estimated_tokens"] <= 1200
    assert estimate_tokens(sys_p) <= 400
    assert "persona_handbook" in tr["layers_skipped"]
    assert "tools" in tr["layers_skipped"]


def test_a_decision_core_meta_light(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    from aihub.chat_contracts import ChatTurnContext, ChatTurnInput
    from aihub.chat_runtime import get_chat_runtime
    from aihub.turn.prompt_budget import select_prompt_budget

    rt = get_chat_runtime()
    turn = ChatTurnInput(user_id="lw_a", session_id="s", message=META, mode="chat", history=[])
    ctx = ChatTurnContext(user_id=turn.user_id, session_id=turn.session_id, mode="chat", system_context={})
    dc = rt._pre_exec_decision_core(turn=turn, ctx=ctx, psyche_snapshot={}, memory_v2_runtime_ctx=None, psyche_v2_behavior_ctx=None)
    assert dc["selected_strategy"] in ("instant", "direct")
    assert dc.get("selected_goal") is None
    assert dc.get("simulation_ran") is False
    assert dc.get("experience_lookup_happened") is False
    budget = select_prompt_budget(
        user_text=META,
        selected_strategy=dc["selected_strategy"],
        web_decision=dc.get("web_decision"),
    )
    assert budget.profile == "meta_light"
    assert budget.turn_value_class == "trivial"


def test_b_meta_prior_ref_history_cap():
    from aihub.turn.prompt_budget import select_prompt_budget

    d = select_prompt_budget(user_text=PRIOR, selected_strategy="direct")
    assert d.profile == "meta_light"
    assert d.history_max_messages <= 2
    assert d.allow_memory is False
    assert d.allow_tools is False


def test_c_meta_feedback_not_pure_trivial():
    from aihub.turn.prompt_budget import select_prompt_budget

    d = select_prompt_budget(user_text=FEEDBACK, selected_strategy="instant")
    assert d.profile == "meta_light"
    assert d.turn_value_class == "feedback"
    assert d.writeback_policy == "minimal"
    assert "META_FEEDBACK_USER_MODEL_OK" in d.reason_codes


def test_d_provider_ask_budget():
    from aihub.turn.prompt_budget import select_prompt_budget
    from aihub.response_persona_guard import sanitize_false_provider_identity

    d = select_prompt_budget(user_text=PROVIDER_ASK, selected_strategy="instant")
    assert d.profile == "meta_light"
    cleaned, changed = sanitize_false_provider_identity(
        "Działam przez OpenAI API.",
        user_message=PROVIDER_ASK,
        final_provider="groq",
        final_model="openai/gpt-oss-120b",
    )
    assert "openai api" not in cleaned.lower()
    assert "groq" in cleaned.lower()


def test_e_recall_not_meta_light():
    from aihub.turn.prompt_budget import select_prompt_budget

    d = select_prompt_budget(user_text=RECALL, selected_strategy="contextual")
    assert d.profile != "meta_light"
    assert d.profile == "contextual"
    assert d.allow_memory is True


def test_f_agentic_not_meta_budget():
    from aihub.turn.prompt_budget import select_prompt_budget

    d = select_prompt_budget(user_text=AGENTIC, selected_strategy="agentic")
    assert d.profile == "agentic"
    # Short live control must not fall into casual_light when strategy was downgraded.
    short = "Zaplanuj trzyetapową migrację bazy danych PostgreSQL."
    d2 = select_prompt_budget(user_text=short, selected_strategy="instant")
    assert d2.profile == "agentic"
    d3 = select_prompt_budget(user_text="Jak nazywa się mój pies?", selected_strategy="instant")
    assert d3.profile == "contextual"
    assert d.allow_tools is True
    assert d.max_prompt_tokens > 1200


def test_g_tools_off_iterations_formula():
    # Mirror pipeline formula: empty tools → tool_iterations=0
    tools = []
    iteration = 0
    CHAT_MAX_TOOL_ITERATIONS = 4
    tool_iterations = (
        0
        if not tools
        else min(int(iteration or 0), max(1, int(CHAT_MAX_TOOL_ITERATIONS)))
    )
    assert tool_iterations == 0


def test_h_minimal_writeback_lists():
    from aihub.turn.prompt_budget import writebacks_for_policy

    allowed, skipped = writebacks_for_policy("minimal")
    assert "transcript" in allowed
    assert "provider_metrics" in allowed
    assert "memory_v2" in skipped
    assert "learning" in skipped
    assert "knowledge" in skipped
    assert "reflection" in skipped


def test_prompt_system_meta_light_short(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    from aihub.chat_contracts import ChatTurnContext
    from aihub.chat_runtime import get_chat_runtime
    from aihub.turn.prompt_budget import estimate_tokens, select_prompt_budget

    rt = get_chat_runtime()
    budget = select_prompt_budget(user_text=META, selected_strategy="instant")
    ctx = ChatTurnContext(
        user_id="u",
        session_id="s",
        mode="chat",
        system_context={
            "prompt_budget_decision": budget,
            "budget_profile": "meta_light",
            "assistant_meta_ask_pure": True,
            "user_turn_text": META,
        },
        capabilities=[],
    )
    prompt = rt._build_system_prompt(
        ctx,
        memory_brief="",
        psyche_brief="",
        decision_hints="",
        correction_hints="",
        first_turn_in_thread=True,
    )
    assert "Mordzix" in prompt
    assert "ADAPTIVE LEARNING" not in prompt
    assert "KONTEKST PAMIĘCI" not in prompt or "pominięty" in prompt.lower() or len(prompt) < 1200
    assert estimate_tokens(prompt) <= 500
    assert "openai/" not in prompt.lower() or "nie utożsamiaj" in prompt.lower()


def test_trivial_memory_filter():
    from aihub.turn.prompt_budget import is_trivial_meta_memory_content

    assert is_trivial_meta_memory_content(META, query=META)
    assert is_trivial_meta_memory_content("Jestem Mordzix, asystent AI-Hub.", query=META)
    assert not is_trivial_meta_memory_content(
        "User prefers short answers", query="jaka jest moja preferencja?"
    )
