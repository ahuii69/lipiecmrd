"""Budget profile integration tests (26.07)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aihub.turn.prompt_budget import (
    classify_turn_value_class,
    is_casual_smalltalk,
    resolve_writeback_plan,
    select_prompt_budget,
)


def test_profiles_for_utterance_classes():
    cases = [
        ("Powiedz krótko, kim jesteś i jak działasz.", "instant", "off", "meta_light", "trivial"),
        ("Elo Mordzix", "instant", "off", "casual_light", "trivial"),
        ("Dzięki", "instant", "off", "casual_light", "trivial"),
        ("Zapamiętaj, że mój pies nazywa się Borys.", "instant", "off", "contextual", "informative"),
        ("Nie, jednak lubi burzę.", "instant", "off", "contextual", "corrective"),
        ("Gdy proszę o debug, odpowiadaj zawsze: logi → diagnoza.", "instant", "off", "contextual", "procedural"),
        (
            "Zmień procedurę Profile26: najpierw sprawdzenie portów",
            "research",
            "required",
            "contextual",
            "corrective",
        ),
        ("Pisz mi odpowiedzi maksymalnie krótko.", "instant", "off", "casual_light", "feedback"),
        ("kurs EUR teraz", "research", "required", "research", "research"),
        ("Jaki jest najnowszy stabilny release FastAPI?", "research", "required", "research", "research"),
        ("Zaplanuj migrację PostgreSQL na nowy VPS.", "instant", "off", "agentic", "agentic"),
        (
            "Wykonaj teraz migrację PostgreSQL na moim serwerze.",
            "agentic",
            "optional",
            "agentic",
            "agentic",
        ),
    ]
    for text, strat, web, profile, tvc in cases:
        d = select_prompt_budget(user_text=text, selected_strategy=strat, web_decision=web)
        assert d.profile == profile, (text, d.profile, d.reason_codes)
        assert d.turn_value_class == tvc, (text, d.turn_value_class)


def test_local_howto_and_procedural_not_forced_research():
    from aihub.strategy_selector import local_howto_no_web_intent, select_strategy

    howto = "Jak sprawdzić, jaki proces słucha na porcie 8080 w Linuksie?"
    assert local_howto_no_web_intent(howto)
    sel = select_strategy(user_id="u", user_text=howto, mode="chat", history=[])
    assert sel.web_decision == "off"
    assert sel.selected_strategy != "research"

    proc = "Zmień procedurę Profile26: najpierw sprawdzenie portów, potem logi"
    sel2 = select_strategy(user_id="u", user_text=proc, mode="chat", history=[])
    # May be instant/contextual; must not require web for procedure edit.
    assert sel2.web_decision == "off" or sel2.selected_strategy != "research"
    d = select_prompt_budget(
        user_text=proc,
        selected_strategy=sel2.selected_strategy,
        web_decision=sel2.web_decision,
    )
    assert d.profile == "contextual"
    assert d.turn_value_class in {"procedural", "corrective"}



def test_light_profiles_skip_heavy_stages():
    meta = select_prompt_budget(user_text="kim jesteś?", selected_strategy="instant")
    assert meta.profile == "meta_light"
    assert not meta.allow_memory and not meta.allow_tools and not meta.allow_knowledge
    casual = select_prompt_budget(user_text="No i git XD", selected_strategy="instant")
    assert casual.profile == "casual_light"
    assert not casual.allow_memory and not casual.allow_tools
    assert casual.writeback_policy == "minimal"


def test_heavy_profiles_not_starved():
    ctx = select_prompt_budget(user_text="Jak nazywa się mój pies?", selected_strategy="contextual")
    assert ctx.profile == "contextual" and ctx.allow_memory
    res = select_prompt_budget(user_text="aktualna pogoda", selected_strategy="research", web_decision="required")
    assert res.profile == "research"
    ag = select_prompt_budget(user_text="Zaplanuj trzyetapową migrację", selected_strategy="instant")
    assert ag.profile == "agentic" and ag.allow_tools


def test_writeback_matrix_trivial_vs_feedback():
    plan = resolve_writeback_plan(policy="minimal", turn_value_class="trivial")
    assert "transcript" in plan["executed"]
    assert "memory_v2" in plan["skipped"]
    assert "learning" in plan["skipped"]
    fb = resolve_writeback_plan(policy="minimal", turn_value_class="feedback", has_user_feedback=True)
    assert "user_model" in fb["executed"]
    replay = resolve_writeback_plan(policy="full", turn_value_class="agentic", replay_mode=True)
    assert replay["executed"] == []
    assert replay["skipped"]


def test_turn_value_class_overrides():
    assert classify_turn_value_class(user_text="Nie, jednak lubi burzę.") == "corrective"
    assert classify_turn_value_class(user_text="Zapamiętaj, że X") == "informative"
    assert classify_turn_value_class(user_text="Elo") == "trivial"
    assert is_casual_smalltalk("Ale z ciebie debil")


def test_trace_validator_rules():
    from scripts.validate_turn_trace import validate_doc

    bad = {
        "ok": True,
        "response_text": "ok",
        "trace": {
            "memory_lookup_happened": False,
            "memory_hits": 3,
            "web_used": False,
            "sources_count": 2,
            "tool_iterations": 0,
            "tool_calls_executed": 1,
            "writeback_policy": "minimal",
            "writebacks_executed": ["memory_v2", "transcript"],
            "writebacks_skipped": ["memory_v2"],
            "provider_success_count": 3,
            "provider_attempt_count": 1,
        },
    }
    codes = {v["code"] for v in validate_doc(bad, artifact="t")}
    assert "TRACE001" in codes
    assert "TRACE002" in codes
    assert "TRACE004" in codes
    assert "TRACE007" in codes or "TRACE016" in codes
    assert "TRACE009" in codes

    good = {
        "ok": True,
        "response_text": "Jestem Mordzix",
        "trace": {
            "memory_lookup_happened": False,
            "memory_hits": 0,
            "web_used": False,
            "sources_count": 0,
            "tool_iterations": 0,
            "tool_calls_executed": 0,
            "writeback_policy": "minimal",
            "writebacks_executed": ["transcript", "provider_metrics"],
            "writebacks_skipped": ["memory_v2", "knowledge"],
            "budget_profile": "meta_light",
            "turn_value_class": "trivial",
            "provider_attempt_count": 2,
            "provider_success_count": 1,
            "provider_generation_count": 1,
            "provider_failover_happened": True,
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
        },
    }
    assert validate_doc(good, artifact="g") == []


def test_casual_decision_skips_heavy_memory(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    from aihub.chat_contracts import ChatTurnContext, ChatTurnInput
    from aihub.chat_runtime import get_chat_runtime
    from aihub.turn.prompt_budget import select_prompt_budget

    rt = get_chat_runtime()
    turn = ChatTurnInput(user_id="p26_casual", session_id="s1", message="Elo Mordzix", mode="chat", history=[])
    ctx = ChatTurnContext(user_id=turn.user_id, session_id=turn.session_id, mode="chat", system_context={})
    psyche = {"loaded": False}
    dc = rt._pre_exec_decision_core(turn=turn, ctx=ctx, psyche_snapshot=psyche)
    budget = select_prompt_budget(
        user_text=turn.message,
        selected_strategy=str(dc.get("selected_strategy") or "instant"),
        web_decision=str(dc.get("web_decision") or "off"),
    )
    assert budget.profile == "casual_light"
    assert budget.writeback_policy == "minimal"
    assert budget.allow_memory is False


def test_meta_decision_still_light(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    from aihub.chat_contracts import ChatTurnContext, ChatTurnInput
    from aihub.chat_runtime import get_chat_runtime
    from aihub.turn.prompt_budget import select_prompt_budget

    rt = get_chat_runtime()
    turn = ChatTurnInput(
        user_id="p26_meta",
        session_id="s1",
        message="Powiedz krótko, kim jesteś i jak działasz.",
        mode="chat",
        history=[],
    )
    ctx = ChatTurnContext(user_id=turn.user_id, session_id=turn.session_id, mode="chat", system_context={})
    dc = rt._pre_exec_decision_core(turn=turn, ctx=ctx, psyche_snapshot={"loaded": False})
    budget = select_prompt_budget(
        user_text=turn.message,
        selected_strategy=str(dc.get("selected_strategy") or "instant"),
        web_decision=str(dc.get("web_decision") or "off"),
    )
    assert budget.profile == "meta_light"
    assert budget.max_prompt_tokens <= 1200
    assert budget.writeback_policy == "minimal"
