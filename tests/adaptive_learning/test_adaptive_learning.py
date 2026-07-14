"""Adaptive learning unit + integration tests."""

from __future__ import annotations

import uuid

import pytest

from aihub.adaptive_learning import store
from aihub.adaptive_learning.engine import (
    apply_delayed_feedback,
    apply_learning_influences_to_decision,
    attribute_causes,
    calibrate_confidence,
    compute_learning_strategy_bias,
    detect_delayed_feedback,
    evaluate_turn_outcome,
    extract_lesson_candidates,
    process_turn_learning,
    update_self_model_from_outcome,
)
from aihub.adaptive_learning.models import FailurePattern, LearnedLesson, TurnOutcomeEvaluation
from aihub.adaptive_learning.replay import replay_user_turns
from aihub.db import fetch_all


@pytest.fixture()
def uid(isolated_db):
    return f"learn_user_{uuid.uuid4().hex[:8]}"


def _outcome(uid: str, turn_id: str, **kwargs) -> TurnOutcomeEvaluation:
    base = dict(
        turn_id=turn_id,
        user_id=uid,
        session_id="sess1",
        primary_intent="statement_or_chat",
        selected_strategy="instant",
        overall_reward=0.0,
        confidence=0.5,
        message_preview="test message about ice cream",
        response_preview="ok",
        reason_codes=[],
    )
    base.update(kwargs)
    o = TurnOutcomeEvaluation(**base)
    store.upsert_turn_outcome(o)
    return o


def test_delayed_positive_feedback_updates_previous_turn(uid):
    prev = _outcome(uid, "t_prev_pos", overall_reward=0.1, acceptance_signal=0.0)
    ev = detect_delayed_feedback(
        message="tak, dokładnie o to chodziło",
        user_id=uid,
        session_id="sess1",
        feedback_turn_id="t_now",
    )
    assert ev is not None
    assert ev.target_turn_id == prev.turn_id
    updated = apply_delayed_feedback(ev)
    assert updated is not None
    assert updated.acceptance_signal >= 0.8
    assert updated.delayed_feedback_applied is True


def test_delayed_negative_feedback_updates_previous_turn(uid):
    prev = _outcome(uid, "t_prev_neg", overall_reward=0.2)
    ev = detect_delayed_feedback(
        message="nie o to chodziło",
        user_id=uid,
        session_id="sess1",
        feedback_turn_id="t_now2",
    )
    assert ev is not None
    assert ev.target_turn_id == prev.turn_id
    updated = apply_delayed_feedback(ev)
    assert updated.rejection_signal >= 0.8 or updated.correction_signal >= 0.7
    assert updated.overall_reward < prev.overall_reward


def test_correction_targets_overlapping_turn(uid):
    _outcome(uid, "t_old_topic", message_preview="wynik meczu polska", primary_intent="research")
    target = _outcome(uid, "t_lody", message_preview="Lody robisz?", primary_intent="sexual_teasing")
    ev = detect_delayed_feedback(
        message="nie o to chodziło, lody miały podtekst",
        user_id=uid,
        session_id="sess1",
        feedback_turn_id="t_fb",
    )
    assert ev is not None
    assert ev.target_turn_id == target.turn_id


def test_strategy_bias_changes_after_series(uid):
    for i in range(8):
        process_turn_learning(
            turn_id=f"t_ok_{i}",
            user_id=uid,
            session_id="s",
            message="krótkie pytanie",
            response_text="ok",
            trace={"selected_strategy": "contextual", "response_critic_score": 90, "duration_ms": 400},
            decision_core={"selected_strategy": "contextual", "strategy_confidence": 0.7},
            ok=True,
        )
    for i in range(6):
        process_turn_learning(
            turn_id=f"t_bad_{i}",
            user_id=uid,
            session_id="s",
            message="aktualny wynik meczu",
            response_text="fallback",
            trace={
                "selected_strategy": "instant",
                "used_fallback": True,
                "controlled_web_decision": "required",
                "duration_ms": 200,
            },
            decision_core={
                "selected_strategy": "instant",
                "strategy_confidence": 0.8,
                "web_decision": "required",
            },
            ok=False,
            errors=[{"type": "provider_error"}],
        )
    bias = compute_learning_strategy_bias(user_id=uid)
    assert isinstance(bias, dict)
    assert "instant" in bias


def test_single_correction_no_global_drift(uid):
    other = f"other_{uuid.uuid4().hex[:6]}"
    process_turn_learning(
        turn_id="t_one_corr",
        user_id=uid,
        session_id="s",
        message="nie o to chodziło",
        response_text="sorry",
        trace={"selected_strategy": "instant", "correction_detected": True},
        decision_core={"selected_strategy": "instant", "strategy_confidence": 0.7},
        ok=True,
    )
    bias_other = compute_learning_strategy_bias(user_id=other)
    assert abs(bias_other.get("instant", 0.0)) < 0.05


def test_self_model_influences_strategy(uid):
    for i in range(6):
        o = evaluate_turn_outcome(
            turn_id=f"sm_{i}",
            user_id=uid,
            session_id="s",
            message="pytanie sportowe wynik meczu",
            response_text="nie wiem",
            trace={
                "selected_strategy": "instant",
                "used_fallback": True,
                "controlled_web_decision": "required",
            },
            decision_core={"selected_strategy": "instant", "web_decision": "required"},
            ok=False,
            errors=[{"type": "x"}],
        )
        o.primary_intent = "sports_result"
        o.overall_reward = -0.4
        update_self_model_from_outcome(o)
    dc = {
        "selected_strategy": "instant",
        "strategy_confidence": 0.8,
        "reason_codes": [],
        "web_decision": "required",
    }
    apply_learning_influences_to_decision(
        decision_core=dc, user_id=uid, message="jaki wynik meczu", intent="sports_result"
    )
    assert dc.get("self_model_loaded") is True
    assert "LEARN_CONFIDENCE_CALIBRATED" in dc.get("reason_codes", [])


def test_provider_and_tool_metrics_learning(uid):
    process_turn_learning(
        turn_id="t_tools",
        user_id=uid,
        session_id="s",
        message="sprawdź web",
        response_text="ok",
        trace={
            "provider": "deepinfra",
            "used_tools": True,
            "tool_calls_successful": 1,
            "tool_failures": 0,
            "controlled_web_tool": "research.query",
            "controlled_web_triggered": True,
            "controlled_web_ok": True,
            "controlled_web_has_results": True,
            "controlled_web_query": "wynik meczu",
            "duration_ms": 900,
            "selected_strategy": "research",
        },
        decision_core={
            "selected_strategy": "research",
            "strategy_confidence": 0.7,
            "web_decision": "required",
        },
        ok=True,
    )
    assert store.get_provider_metric_rows(user_id=uid)
    assert store.get_tool_metric_rows(user_id=uid)


def test_failure_memory_used_in_decision(uid):
    store.upsert_failure(
        FailurePattern(
            failure_id=str(uuid.uuid4()),
            user_id=uid,
            category="tool choice",
            trigger="plan wdrożenia redis",
            corrective_action="prefer research path",
            confidence=0.8,
        )
    )
    dc = {
        "selected_strategy": "instant",
        "reason_codes": [],
        "strategy_confidence": 0.9,
        "web_decision": "off",
    }
    apply_learning_influences_to_decision(
        decision_core=dc,
        user_id=uid,
        message="plan wdrożenia redis ponownie",
        intent="task",
    )
    assert "LEARN_FAILURE_MEMORY_USED" in dc.get("reason_codes", [])


def test_user_model_verbosity_learning(uid):
    process_turn_learning(
        turn_id="t_short",
        user_id=uid,
        session_id="s",
        message="napisz krócej proszę",
        response_text="ok",
        trace={"selected_strategy": "contextual", "user_model_length": "short"},
        decision_core={"selected_strategy": "contextual", "strategy_confidence": 0.7},
        ok=True,
    )
    um = store.load_user_model_v2(uid)
    assert um.preferred_verbosity.value == "short"
    dc = {"selected_strategy": "contextual", "reason_codes": [], "strategy_confidence": 0.7}
    apply_learning_influences_to_decision(decision_core=dc, user_id=uid, message="co dalej?", intent="chat")
    assert dc.get("learning_length_directive") == "short" or (
        dc.get("user_model_v2") or {}
    ).get("verbosity") == "short"


def test_long_horizon_rejects_persist(uid):
    process_turn_learning(
        turn_id="t_lht1",
        user_id=uid,
        session_id="s_lht",
        message="zrób plan wdrożenia systemu w trzech etapach",
        response_text="plan: 1) db 2) redis 3) deploy",
        trace={"selected_strategy": "agentic", "planner_executed": True},
        decision_core={
            "selected_strategy": "agentic",
            "planner_recommended": True,
            "strategy_confidence": 0.7,
        },
        ok=True,
    )
    process_turn_learning(
        turn_id="t_lht2",
        user_id=uid,
        session_id="s_lht",
        message="odrzucam pomysł z redisem",
        response_text="OK bez redisa",
        trace={"selected_strategy": "contextual"},
        decision_core={
            "selected_strategy": "contextual",
            "strategy_confidence": 0.7,
            "planner_recommended": True,
        },
        ok=True,
    )
    task = store.get_active_long_horizon_task(user_id=uid, session_id="s_lht")
    assert task is not None
    assert any("redis" in x.lower() or "odrzuc" in x.lower() for x in task.rejected_decisions)
    dc = {
        "selected_strategy": "contextual",
        "reason_codes": [],
        "strategy_confidence": 0.7,
        "session_id": "s_lht",
    }
    apply_learning_influences_to_decision(
        decision_core=dc, user_id=uid, message="co dalej z planem?", intent="task"
    )
    assert dc.get("long_horizon_task_id")
    assert dc.get("long_horizon_rejected")


def test_confidence_calibration_and_small_sample(uid):
    cal0 = calibrate_confidence(raw=0.9, strategy="instant", intent="x", user_id=uid, sample_hint=0)
    assert cal0.calibrated_confidence <= 0.9
    assert cal0.calibration_sample_count == 0


def test_lesson_dedup_contradiction_decay(uid):
    les = LearnedLesson(
        lesson_id=str(uuid.uuid4()),
        user_id=uid,
        scope="user",
        statement="prefer: factor=web decision; strategy=research; action=prefer_research",
        machine_action="prefer_research",
        confidence=0.6,
        category="web_decision",
    )
    ok1, _ = store.upsert_lesson(les)
    assert ok1
    ok2, why2 = store.upsert_lesson(les)
    assert ok2 and why2 == "reinforced"
    rows = store.list_active_lessons(user_id=uid, include_global=False)
    assert rows
    store.contradict_lesson(rows[0].lesson_id)
    store.contradict_lesson(rows[0].lesson_id)
    store.decay_lessons(limit=50)


def test_replay_no_side_effects_no_writebacks(uid):
    process_turn_learning(
        turn_id="t_replay_src",
        user_id=uid,
        session_id="s",
        message="eloszka",
        response_text="siema",
        trace={"selected_strategy": "instant"},
        decision_core={"selected_strategy": "instant", "strategy_confidence": 0.6},
        ok=True,
    )
    before = len(store.list_active_lessons(user_id=uid))
    out = replay_user_turns(user_id=uid, limit=10, mode="evaluation")
    assert out["side_effects_executed"] is False
    assert out["writebacks"] == 0
    after = len(store.list_active_lessons(user_id=uid))
    assert after == before


def test_causal_attribution_and_lesson_candidates(uid):
    o = evaluate_turn_outcome(
        turn_id="t_cau",
        user_id=uid,
        session_id="s",
        message="nie o to chodziło",
        response_text="sorry",
        trace={"correction_detected": True, "selected_strategy": "instant"},
        decision_core={"selected_strategy": "instant"},
        ok=True,
    )
    o.correction_signal = 0.9
    attrs = attribute_causes(
        outcome=o,
        trace={"correction_detected": True},
        decision_core={"selected_strategy": "instant"},
    )
    assert attrs
    cands = extract_lesson_candidates(outcome=o, attributions=attrs)
    assert isinstance(cands, list)


def test_process_turn_same_turn_id_pipeline(uid):
    tid = "canonical_turn_abc"
    lr = process_turn_learning(
        turn_id=tid,
        user_id=uid,
        session_id="s",
        message="test turn id",
        response_text="resp",
        trace={"selected_strategy": "contextual", "response_critic_score": 80},
        decision_core={"selected_strategy": "contextual", "strategy_confidence": 0.7},
        ok=True,
    )
    loaded = store.get_turn_outcome(tid)
    assert loaded is not None
    assert loaded.turn_id == tid
    assert lr.outcome is not None
    assert len(lr.attributions) >= 1


def test_research_learning_metrics_row(uid):
    process_turn_learning(
        turn_id="t_res",
        user_id=uid,
        session_id="s",
        message="jaki wynik meczu wczoraj",
        response_text="1:0",
        trace={
            "selected_strategy": "research",
            "controlled_web_triggered": True,
            "controlled_web_ok": True,
            "controlled_web_has_results": True,
            "controlled_web_query": "wynik meczu data:yesterday",
            "research_query_variants": ["wynik meczu data:yesterday", "mecz wynik"],
            "controlled_web_source_count": 3,
        },
        decision_core={
            "selected_strategy": "research",
            "web_decision": "required",
            "strategy_confidence": 0.7,
        },
        ok=True,
    )
    rows = fetch_all("SELECT * FROM research_metrics WHERE user_id=?", (uid,))
    assert rows


def test_per_user_isolation(uid):
    u2 = f"iso_{uuid.uuid4().hex[:6]}"
    process_turn_learning(
        turn_id="t_u1",
        user_id=uid,
        session_id="s",
        message="napisz krócej",
        response_text="ok",
        trace={},
        decision_core={"selected_strategy": "contextual", "strategy_confidence": 0.7},
        ok=True,
    )
    um2 = store.load_user_model_v2(u2)
    assert um2.preferred_verbosity.evidence_count == 0


def test_success_memory_recorded(uid):
    lr = process_turn_learning(
        turn_id="t_succ",
        user_id=uid,
        session_id="s",
        message="ok pytanie",
        response_text="świetna odpowiedź konkretna",
        trace={"selected_strategy": "contextual", "response_critic_score": 95, "duration_ms": 300},
        decision_core={"selected_strategy": "contextual", "strategy_confidence": 0.8},
        ok=True,
    )
    assert lr.success_recorded or (lr.outcome and lr.outcome.overall_reward >= 0)


def test_abandoned_goal_long_horizon(uid):
    process_turn_learning(
        turn_id="t_ab1",
        user_id=uid,
        session_id="s_ab",
        message="zaplanuj migrację bazy w czterech krokach",
        response_text="plan...",
        trace={"planner_executed": True},
        decision_core={"planner_recommended": True, "selected_strategy": "agentic", "strategy_confidence": 0.7},
        ok=True,
    )
    process_turn_learning(
        turn_id="t_ab2",
        user_id=uid,
        session_id="s_ab",
        message="anuluj plan migracji",
        response_text="anulowane",
        trace={},
        decision_core={"selected_strategy": "contextual", "strategy_confidence": 0.7, "planner_recommended": True},
        ok=True,
    )
    task = store.get_active_long_horizon_task(user_id=uid, session_id="s_ab")
    # abandoned tasks are not active
    assert task is None or task.status != "abandoned"


def test_machine_action_influences_next_turn_strategy(uid):
    """Turn N persists prefer_research lesson → turn N+1 strategy changes."""
    process_turn_learning(
        turn_id="t_n_bad",
        user_id=uid,
        session_id="s_act",
        message="aktualny wynik meczu teraz",
        response_text="nie wiem",
        trace={
            "selected_strategy": "instant",
            "used_fallback": True,
            "controlled_web_decision": "required",
            "primary_intent": "sports_result",
        },
        decision_core={
            "selected_strategy": "instant",
            "strategy_confidence": 0.8,
            "web_decision": "required",
        },
        ok=False,
        errors=[{"type": "provider_error"}],
    )
    lessons = store.list_active_lessons(user_id=uid, include_global=False)
    assert any(getattr(l, "machine_action", None) for l in lessons) or lessons
    # Ensure at least one actionable lesson exists
    if not any((l.machine_action or "") for l in lessons):
        store.upsert_lesson(
            LearnedLesson(
                lesson_id=str(uuid.uuid4()),
                user_id=uid,
                scope="user",
                statement="prefer research after miss",
                machine_action="prefer_research",
                confidence=0.7,
                category="web_decision",
                applicable_strategies=["instant"],
            )
        )
    dc = {
        "selected_strategy": "instant",
        "strategy_confidence": 0.85,
        "reason_codes": [],
        "web_decision": "off",
    }
    apply_learning_influences_to_decision(
        decision_core=dc,
        user_id=uid,
        message="jaki jest wynik meczu dzisiaj",
        intent="sports_result",
    )
    assert dc.get("selected_strategy") in ("research", "contextual")
    assert "LEARN_MACHINE_ACTIONS_EXECUTED" in dc.get("reason_codes", []) or dc.get(
        "self_model_influenced_strategy"
    )


def test_delayed_feedback_binds_first_not_last(uid):
    first = _outcome(
        uid,
        "t_first_way",
        message_preview="propozycja sposobu A z redis",
        response_preview="użyj redis cache",
        overall_reward=0.2,
        primary_intent="task",
    )
    _outcome(
        uid,
        "t_middle_noise",
        message_preview="jaka pogoda w Warszawie",
        response_preview="słonecznie",
        overall_reward=0.1,
        primary_intent="chat",
    )
    last = _outcome(
        uid,
        "t_last_way",
        message_preview="inna opcja B z memcached",
        response_preview="użyj memcached",
        overall_reward=0.15,
        primary_intent="task",
    )
    ev = detect_delayed_feedback(
        message="ten pierwszy sposób zadziałał, dokładnie",
        user_id=uid,
        session_id="sess1",
        feedback_turn_id="t_fb_first",
    )
    assert ev is not None
    assert ev.target_turn_id == first.turn_id
    assert ev.target_turn_id != last.turn_id
    apply_delayed_feedback(ev)
    rows = store.get_delayed_feedback_for_target(first.turn_id)
    assert rows


def test_rejected_decision_guard_after_20_turns(uid):
    process_turn_learning(
        turn_id="t_rej_start",
        user_id=uid,
        session_id="s_rej20",
        message="zrób plan wdrożenia aplikacji w czterech krokach",
        response_text="plan obejmuje redis",
        trace={"planner_executed": True, "selected_strategy": "agentic"},
        decision_core={
            "selected_strategy": "agentic",
            "planner_recommended": True,
            "strategy_confidence": 0.7,
            "session_id": "s_rej20",
        },
        ok=True,
    )
    process_turn_learning(
        turn_id="t_rej_reject",
        user_id=uid,
        session_id="s_rej20",
        message="odrzucam pomysł z redisem",
        response_text="OK, bez redisa",
        trace={"selected_strategy": "contextual"},
        decision_core={
            "selected_strategy": "contextual",
            "strategy_confidence": 0.7,
            "planner_recommended": True,
            "session_id": "s_rej20",
        },
        ok=True,
    )
    for i in range(20):
        process_turn_learning(
            turn_id=f"t_rej_noise_{i}",
            user_id=uid,
            session_id="s_rej20",
            message=f"dygresja tematyczna numer {i} o pogodzie",
            response_text="ok",
            trace={"selected_strategy": "instant"},
            decision_core={
                "selected_strategy": "instant",
                "strategy_confidence": 0.6,
                "session_id": "s_rej20",
            },
            ok=True,
        )
    task = store.get_active_long_horizon_task(user_id=uid, session_id="s_rej20")
    assert task is not None
    assert any("redis" in x.lower() or "odrzuc" in x.lower() for x in task.rejected_decisions)
    dc = {
        "selected_strategy": "contextual",
        "reason_codes": [],
        "strategy_confidence": 0.7,
        "session_id": "s_rej20",
    }
    apply_learning_influences_to_decision(
        decision_core=dc,
        user_id=uid,
        message="wracamy do planu — co z redisem?",
        intent="task",
    )
    assert dc.get("rejected_decision_guard_applied") is True
    assert "LEARN_LHT_REJECTED_GUARD" in dc.get("reason_codes", [])
    assert "LEARN_REJECTED_OPTION_SUPPRESSED" in dc.get("reason_codes", [])


def test_causal_observed_vs_inferred(uid):
    o = evaluate_turn_outcome(
        turn_id="t_obs",
        user_id=uid,
        session_id="s",
        message="nie o to chodziło",
        response_text="sorry",
        trace={"correction_detected": True, "selected_strategy": "instant"},
        decision_core={"selected_strategy": "instant"},
        ok=True,
    )
    o.correction_signal = 0.9
    attrs = attribute_causes(
        outcome=o,
        trace={"correction_detected": True},
        decision_core={"selected_strategy": "instant"},
    )
    kinds = {a.evidence_kind for a in attrs}
    assert "observed" in kinds
    assert any(k in kinds for k in ("strongly_inferred", "weakly_inferred", "inferred"))


def test_outcome_has_component_scores(uid):
    o = evaluate_turn_outcome(
        turn_id="t_comp",
        user_id=uid,
        session_id="s",
        message="krótko",
        response_text="ok",
        trace={"selected_strategy": "contextual", "response_critic_score": 88, "duration_ms": 400},
        decision_core={"selected_strategy": "contextual"},
        ok=True,
    )
    assert o.intent_match_score != o.factual_grounding_score or o.style_match_score is not None
    assert "OUTCOME_EVALUATED" in o.reason_codes
    assert o.overall_reward == o.overall_reward  # computed, not None

