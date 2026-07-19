"""World-class upgrades: long-horizon recall, procedures, plan-only planner."""

from __future__ import annotations

import time
import uuid

from aihub.adaptive_learning.models import LongHorizonTask
from aihub.adaptive_learning import store as learn_store
from aihub.adaptive_learning.engine import maybe_update_long_horizon, apply_learning_influences_to_decision
from aihub.adaptive_learning.models import TurnOutcomeEvaluation
from aihub.memory_v2_procedural import upsert_user_declared_procedure
from aihub.turn.mixins.decision import DecisionMixin
from aihub.turn.prompt_budget import build_agentic_bounded_system_prompt, select_prompt_budget


def test_long_horizon_cross_session_lookup(tmp_path, monkeypatch, isolated_db):
    uid = f"lht-{uuid.uuid4().hex[:8]}"
    task = LongHorizonTask(
        task_id=str(uuid.uuid4()),
        user_id=uid,
        session_id="sess-old",
        title="Profile26-abc12345: migracja PostgreSQL",
        objective="Migracja PG na nowy VPS",
        pending_steps=["Backup", "Restore", "Verify"],
        next_best_action="Backup",
        current_stage="tracked",
        status="active",
        confidence=0.8,
        created_at=time.time(),
        updated_at=time.time(),
    )
    learn_store.save_long_horizon_task(task)

    found = learn_store.get_active_long_horizon_task(user_id=uid, session_id="sess-new")
    assert found is not None
    assert found.task_id == task.task_id

    by_marker = learn_store.find_long_horizon_task_by_marker(user_id=uid, marker="Profile26-abc12345")
    assert by_marker is not None
    brief = learn_store.format_long_horizon_brief(found)
    assert "Backup" in brief
    assert "Profile26" in brief


def test_track_intent_creates_structured_long_horizon(isolated_db):
    uid = f"lht-track-{uuid.uuid4().hex[:8]}"
    outcome = TurnOutcomeEvaluation(
        turn_id=str(uuid.uuid4()),
        user_id=uid,
        session_id="s1",
        message_preview="Śledź",
        selected_strategy="agentic",
        overall_reward=0.0,
    )
    tid, created = maybe_update_long_horizon(
        user_id=uid,
        session_id="s1",
        turn_id=outcome.turn_id,
        message="Śledź ten plan jako zadanie długoterminowe Profile26-xyz99999.",
        outcome=outcome,
        decision_core={"planner_recommended": True},
    )
    assert created is True
    assert tid
    task = learn_store.get_active_long_horizon_task(user_id=uid, session_id="s2")
    assert task is not None
    assert "Profile26" in task.title
    assert task.pending_steps


def test_lht_status_escalates_and_injects_brief(isolated_db):
    uid = f"lht-status-{uuid.uuid4().hex[:8]}"
    task = LongHorizonTask(
        task_id=str(uuid.uuid4()),
        user_id=uid,
        session_id="s0",
        title="Profile26-stat: plan",
        objective="Migracja",
        pending_steps=["Sprawdź porty"],
        next_best_action="Sprawdź porty",
        status="active",
        created_at=time.time(),
        updated_at=time.time(),
    )
    learn_store.save_long_horizon_task(task)
    dc = {
        "selected_strategy": "instant",
        "reason_codes": [],
        "session_id": "s-new",
        "web_decision": "off",
        "strategy_confidence": 0.7,
    }
    apply_learning_influences_to_decision(
        decision_core=dc,
        user_id=uid,
        message="Jaki jest aktualny stan zadania Profile26-stat i co jest następnym krokiem?",
        intent="goal",
    )
    assert dc.get("long_horizon_task_id") == task.task_id
    assert "Sprawdź porty" in (dc.get("long_horizon_brief") or "")
    assert dc.get("selected_strategy") == "agentic"


def test_procedure_questions_do_not_upsert(isolated_db):
    uid = f"proc-q-{uuid.uuid4().hex[:8]}"
    assert upsert_user_declared_procedure(uid, "Podaj procedurę debugowania 502.") is None
    stored = upsert_user_declared_procedure(
        uid,
        "Dla testów Profile26 zapamiętaj procedurę: najpierw logi, potem diagnoza.",
    )
    assert stored is not None


def test_plan_only_skips_handoff_but_agentic_budget():
    class T(DecisionMixin):
        pass

    t = T()
    dc = {
        "selected_strategy": "agentic",
        "web_decision": "off",
        "escalation_final_mode": "planner",
        "experience_handoff_bias": 0,
    }
    should, reason = t._should_handoff_to_agent(
        decision_core=dc,
        message="Napisz plan migracji PostgreSQL. Niczego nie wykonuj.",
    )
    assert should is False
    assert reason == "plan_only_chat_path"
    budget = select_prompt_budget(
        user_text="Napisz plan migracji PostgreSQL. Niczego nie wykonuj.",
        selected_strategy="agentic",
        web_decision="off",
    )
    assert budget.profile == "agentic"
    text = build_agentic_bounded_system_prompt(
        planner_brief="PLANER:\n1. backup",
        long_horizon_brief="ZADANIE: next=backup",
    )
    assert "PLANER" in text
    assert "NIE deklaruj wykonania" in text
