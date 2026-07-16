#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""24.07 — meta routing, actionable goals, provider identity, cleanup."""
from __future__ import annotations

import time

import pytest


META_MSG = "Powiedz krótko, kim jesteś i jak działasz."
PRIOR_META = "A jak wcześniej mówiłeś, jak działasz?"
REAL_GOAL_MSG = (
    "Zaplanuj migrację PostgreSQL na nowy serwer, podziel ją na etapy i śledź postęp."
)
RECALL_MSG = "Jak nazywa się mój pies?"


# ── A. Routing ──────────────────────────────────────────────────────────────


def test_a_meta_ask_lightweight_routing(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    from aihub.strategy_selector import (
        StrategySelector,
        is_assistant_meta_ask,
        select_strategy,
    )

    assert is_assistant_meta_ask(META_MSG)
    out = StrategySelector().select_strategy(
        META_MSG,
        {"active_goals_count": 5, "goal_max_urgency": 0.95, "history_turns": 4},
    )
    assert out["strategy"] in ("instant", "direct")
    assert out["requires_memory"] is False
    assert out["requires_research"] is False
    assert out["requires_planning"] is False

    sel = select_strategy(
        user_id="meta_a_user",
        user_text=META_MSG,
        mode="chat",
        active_goals_summary={"active_count": 3, "max_urgency": 0.9},
        history=[],
    )
    assert sel.selected_strategy in ("instant", "direct")
    assert "META_ASK_LIGHTWEIGHT_PATH" in sel.reason_codes
    assert "META_ASK_MEMORY_SKIPPED" in sel.reason_codes
    assert "META_ASK_GOAL_SKIPPED" in sel.reason_codes
    assert "ACTIVE_GOAL_PRESENT" not in sel.reason_codes
    assert sel.web_decision == "off" or sel.selector_output.get("requires_research") is False


def test_a_decision_core_meta_no_goal_no_sim(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    from aihub.chat_contracts import ChatTurnContext, ChatTurnInput
    from aihub.chat_runtime import get_chat_runtime
    from aihub.goal_engine import GoalCandidate, GoalType, get_goal_engine

    # Plant junk meta goal — must NOT surface as selected_goal / ACTIVE_GOAL_PRESENT
    eng = get_goal_engine()
    junk = eng.create_goal(
        GoalCandidate(
            user_id="meta_a_dc",
            title=f"Uzupełnić brak kontekstu: {META_MSG}",
            description="Brak trafień w pamięci",
            goal_type=GoalType.INFORMATION_NEED.value,
            source="memory_gap",
            priority=0.7,
            urgency=0.8,
            importance=0.7,
            confidence=0.7,
            tags=["information_need"],
            success_criteria=["context_has_answer"],
            failure_criteria=["no_context_hits"],
            metadata={"query": META_MSG},
        )
    )
    eng.activate_goal("meta_a_dc", junk.goal_id)

    rt = get_chat_runtime()
    turn = ChatTurnInput(
        user_id="meta_a_dc",
        session_id="s1",
        message=META_MSG,
        mode="chat",
        history=[],
    )
    ctx = ChatTurnContext(
        user_id=turn.user_id,
        session_id=turn.session_id,
        mode="chat",
        system_context={},
    )
    dc = rt._pre_exec_decision_core(
        turn=turn,
        ctx=ctx,
        psyche_snapshot={},
        memory_v2_runtime_ctx=None,
        psyche_v2_behavior_ctx=None,
    )
    assert dc["selected_strategy"] in ("instant", "direct")
    assert dc.get("selected_goal") is None
    assert "ACTIVE_GOAL_PRESENT" not in (dc.get("reason_codes") or [])
    assert dc.get("simulation_ran") is False
    assert dc.get("experience_lookup_happened") is False
    assert dc.get("web_decision") == "off"
    assert dc.get("planner_recommended") in (False, None)
    assert "META_ASK_HEAVY_STAGES_SKIPPED" in (dc.get("reason_codes") or [])


# ── B. Prior-ref meta ───────────────────────────────────────────────────────


def test_b_meta_prior_ref_uses_direct_no_planner(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    from aihub.strategy_selector import (
        StrategySelector,
        meta_ask_refers_to_prior_conversation,
        select_strategy,
    )

    assert meta_ask_refers_to_prior_conversation(PRIOR_META)
    out = StrategySelector().select_strategy(
        PRIOR_META, {"active_goals_count": 0, "history_turns": 6}
    )
    assert out["strategy"] in ("instant", "direct")
    assert out["requires_planning"] is False
    assert out["requires_memory"] is False

    sel = select_strategy(
        user_id="meta_b",
        user_text=PRIOR_META,
        mode="chat",
        history=[
            {"role": "user", "content": "hej"},
            {"role": "assistant", "content": "siema"},
            {"role": "user", "content": "coś"},
            {"role": "assistant", "content": "ok"},
        ],
    )
    assert sel.selected_strategy in ("instant", "direct")
    assert "ACTIVE_GOAL_PRESENT" not in sel.reason_codes


# ── C. Real goal ────────────────────────────────────────────────────────────


def test_c_real_migration_goal_actionable(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    from aihub.goal_engine import get_goal_engine, is_actionable_goal
    from aihub.strategy_selector import StrategySelector

    eng = get_goal_engine()
    ctx = eng.build_goal_context(
        "goal_c_user",
        input_event={"text": REAL_GOAL_MSG},
        memory_context={"total": 0},
    )
    assert ctx.selected_goal is not None or ctx.created_goal_ids
    actionable = eng.get_actionable_goals("goal_c_user")
    assert actionable
    assert all(is_actionable_goal(g) for g in actionable)

    out = StrategySelector().select_strategy(
        REAL_GOAL_MSG,
        {
            "active_goals_count": len(actionable),
            "goal_max_urgency": max(g.urgency for g in actionable),
            "history_turns": 0,
        },
    )
    assert out["strategy"] in ("agentic", "contextual", "research")
    assert out["strategy"] != "instant"


# ── D. Simple recall — no goal ──────────────────────────────────────────────


def test_d_simple_recall_skips_goal(isolated_db):
    from aihub.goal_engine import GoalEngine

    skip, reason = GoalEngine._should_skip_goal_extraction(RECALL_MSG)
    assert skip is True
    assert reason == "GOAL_SKIPPED_MEMORY_RECALL"

    from aihub.goal_engine import get_goal_engine

    ctx = get_goal_engine().build_goal_context(
        "recall_d",
        input_event={"text": RECALL_MSG},
        memory_context={"total": 2},
    )
    assert ctx.created_goal_ids == []
    assert ctx.selected_goal is None
    assert ctx.selected_reason == "GOAL_SKIPPED_MEMORY_RECALL"


# ── E. Provider identity ────────────────────────────────────────────────────


def test_e_false_openai_claim_sanitized():
    from aihub.response_persona_guard import (
        contains_false_openai_provider_claim,
        sanitize_false_provider_identity,
    )

    text = (
        "Jestem asystentem działającym w oparciu o modele językowe OpenAI. "
        "Mogę pomóc w rozmowie."
    )
    assert contains_false_openai_provider_claim(text)
    cleaned, changed = sanitize_false_provider_identity(
        text,
        user_message=META_MSG,
        final_provider="groq",
        final_model="openai/gpt-oss-120b",
    )
    assert changed is True
    assert "openai api" not in cleaned.lower()
    assert "w oparciu o" not in cleaned.lower() or "openai" not in cleaned.lower()
    # Model family prefix alone must not imply OpenAI API claim remains
    assert "chatgpt" not in cleaned.lower()


def test_e_openai_word_in_other_context_preserved():
    from aihub.response_persona_guard import sanitize_false_provider_identity

    text = "Porównaj ceny API Anthropic i OpenAI na rynku chmurowym w 2024."
    cleaned, changed = sanitize_false_provider_identity(
        text, user_message="porównaj dostawców", final_provider="groq"
    )
    # No false "I run on OpenAI API" claim → unchanged
    assert changed is False
    assert "OpenAI" in cleaned


# ── F. Cleanup dry/apply/idempotent ─────────────────────────────────────────


def test_f_cleanup_dry_apply_idempotent(isolated_db):
    from aihub.goal_engine import (
        GoalCandidate,
        GoalType,
        get_goal_engine,
        is_actionable_goal,
    )

    eng = get_goal_engine()
    uid = "cleanup_f_user"
    junk = eng.create_goal(
        GoalCandidate(
            user_id=uid,
            title=f"Uzupełnić brak kontekstu: {META_MSG}",
            description="Brak trafień",
            goal_type=GoalType.INFORMATION_NEED.value,
            source="memory_gap",
            priority=0.6,
            urgency=0.7,
            importance=0.6,
            confidence=0.6,
            metadata={"query": META_MSG},
        )
    )
    eng.activate_goal(uid, junk.goal_id)

    real = eng.create_goal(
        GoalCandidate(
            user_id=uid,
            title="Migracja PostgreSQL — etapy",
            description=REAL_GOAL_MSG,
            goal_type=GoalType.TASK.value,
            source="user_input",
            priority=0.85,
            urgency=0.8,
            importance=0.9,
            confidence=0.85,
            tags=["migration", "plan"],
            success_criteria=["etapy_zdefiniowane", "postep_sledzony"],
            failure_criteria=["blocked"],
            metadata={"intent_text": REAL_GOAL_MSG, "long_horizon_task_id": "lh-1"},
        )
    )
    eng.activate_goal(uid, real.goal_id)
    assert is_actionable_goal(eng._get_goal(uid, real.goal_id))  # type: ignore[attr-defined]

    dry = eng.cleanup_non_actionable_goals(uid, dry_run=True)
    assert dry["matched"] >= 1
    assert dry["cancelled"] == 0
    assert eng._get_goal(uid, junk.goal_id).status == "active"  # type: ignore[union-attr]

    apply1 = eng.cleanup_non_actionable_goals(uid, dry_run=False)
    assert apply1["cancelled"] >= 1
    assert eng._get_goal(uid, junk.goal_id).status == "cancelled"  # type: ignore[union-attr]
    assert eng._get_goal(uid, real.goal_id).status == "active"  # type: ignore[union-attr]

    apply2 = eng.cleanup_non_actionable_goals(uid, dry_run=False)
    assert apply2["cancelled"] == 0
    assert eng._get_goal(uid, real.goal_id).status == "active"  # type: ignore[union-attr]


def test_meta_build_context_skips_retrieve(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("retrieve_context must not run for pure meta")

    monkeypatch.setattr("aihub.turn.mixins.prompt_context.retrieve_context", boom)
    from aihub.chat_contracts import ChatTurnInput
    from aihub.chat_runtime import get_chat_runtime

    rt = get_chat_runtime()
    turn = ChatTurnInput(
        user_id="meta_ctx",
        session_id="s",
        message=META_MSG,
        mode="chat",
        history=[],
    )
    ctx = rt._build_context(turn, correction_turn_trace={})
    assert calls["n"] == 0
    assert ctx.memory_context.get("memory_lookup_skipped") is True
    assert int(ctx.memory_context.get("total") or 0) == 0
