"""Cognitive Integration V2 — unit + property tests."""

from __future__ import annotations

from aihub.turn.cognitive_integration import (
    apply_cognitive_to_decision,
    build_cognitive_influence_pack,
    calibrate_from_outcome,
    critique_response_v2,
    expand_research_queries,
    load_conversation_state,
    load_user_model,
    update_conversation_after_turn,
    update_user_model_from_turn,
)
from aihub.turn.pragmatics import analyze_pragmatics


def test_intent_ranking_and_escalation():
    pa = analyze_pragmatics(raw_text="Lody robisz?", history=[], user_id="")
    pack = build_cognitive_influence_pack(
        user_id="",
        session_id="s",
        message="Lody robisz?",
        history=[],
        pragmatics=pa,
    )
    assert len(pack.intent_ranking) >= 1
    assert pack.intent_ranking[0].label == pack.primary_intent
    dc = {
        "selected_strategy": "instant",
        "reason_codes": [],
        "strategy_confidence": 0.9,
        "web_decision": "off",
    }
    apply_cognitive_to_decision(decision_core=dc, pack=pack)
    assert dc["selected_strategy"] != "instant"
    assert dc.get("escalation_use_reasoning") is True


def test_research_multi_query_ranking():
    from aihub.turn.cognitive_integration import rank_research_queries

    variants = expand_research_queries(
        rewritten="wynik meczu mundial 2026 data:2026-07-11",
        raw="mistrzostwa świata 2026 mecz gramy przed wczoraj",
    )
    assert len(variants) >= 1
    assert "przed wczoraj" not in " ".join(variants)
    assert variants[0]
    scored = rank_research_queries(
        rewritten="wynik meczu mundial 2026 data:2026-07-11",
        raw="mistrzostwa świata 2026 mecz gramy przed wczoraj",
    )
    assert scored
    assert scored[0].score >= scored[-1].score
    assert scored[0].confidence > 0


def test_tool_order_hint_reorders_tools():
    from types import SimpleNamespace

    from aihub.turn.mixins.decision import DecisionMixin

    tools = [
        SimpleNamespace(name="psyche.get_state"),
        SimpleNamespace(name="research.query"),
        SimpleNamespace(name="memory.search"),
        SimpleNamespace(name="goal.list"),
    ]
    ordered = DecisionMixin._apply_strategy_to_tools(
        tools,
        "research",
        tool_order_hint=["memory", "planner", "research"],
    )
    names = [t.name for t in ordered]
    assert names.index("memory.search") < names.index("research.query")


def test_conversation_rejected_and_decided(isolated_db):
    uid = "cog_dec_user"
    sid = "sess_dec"
    pa = analyze_pragmatics(raw_text="odrzucam pomysł z redisem", history=[], user_id=uid)
    state = update_conversation_after_turn(
        user_id=uid,
        session_id=sid,
        message="odrzucam pomysł z redisem",
        response_text="OK, bez redisa. Decyzja: zostajemy przy sqlite.",
        pragmatics=pa,
        ok=True,
    )
    assert any("odrzuc" in x.lower() or "redis" in x.lower() for x in state.rejected)
    assert state.decided or state.rejected


def test_experience_caution_blocks_instant():
    pa = analyze_pragmatics(raw_text="co dalej z deploy?", history=[], user_id="")
    pack = build_cognitive_influence_pack(
        user_id="",
        session_id="s",
        message="co dalej z deploy?",
        history=[],
        pragmatics=pa,
        experience_signal_summary="prior_fail_similar_web_miss",
    )
    dc = {
        "selected_strategy": "instant",
        "reason_codes": [],
        "strategy_confidence": 0.9,
        "web_decision": "off",
    }
    apply_cognitive_to_decision(decision_core=dc, pack=pack)
    assert dc["selected_strategy"] != "instant"
    assert "COG_EXPERIENCE_BIAS" in pack.influence_reason_codes


def test_critic_v2_style_and_helpdesk():
    pa = analyze_pragmatics(raw_text="elo", history=[], user_id="")
    pack = build_cognitive_influence_pack(
        user_id="", session_id="s", message="elo", history=[], pragmatics=pa
    )
    cr = critique_response_v2(
        response_text="Cześć! Jak mogę pomóc?",
        pragmatics=pa,
        pack=pack,
    )
    assert cr.passed is False
    assert "CRITIC_MECHANICAL_HELPDESK" in cr.reason_codes


def test_user_model_learns_short_preference(isolated_db):
    uid = "cog_um_user"
    m = update_user_model_from_turn(
        user_id=uid,
        message="napisz krócej proszę",
        response_text="ok",
        pragmatics=None,
    )
    assert m.preferred_answer_length == "short"
    loaded = load_user_model(user_id=uid)
    assert loaded.preferred_answer_length == "short"
    assert loaded.sample_count >= 1


def test_conversation_state_survives_300_turns(isolated_db):
    uid = "cog_300_user"
    sid = "sess_300"
    state = None
    for i in range(300):
        msg = f"temat główny krok {i} planujemy system"
        if i == 50:
            msg = "odrzucam poprzedni pomysł z redisem"
        pa = analyze_pragmatics(raw_text=msg, history=[], user_id=uid)
        state = update_conversation_after_turn(
            user_id=uid,
            session_id=sid,
            message=msg,
            response_text=f"ok {i}",
            pragmatics=pa,
            ok=True,
        )
    assert state is not None
    assert state.turn_count == 300
    assert state.primary_topic
    loaded = load_conversation_state(user_id=uid, session_id=sid)
    assert loaded.turn_count == 300
    assert loaded.primary_topic


def test_calibration_updates_bias(isolated_db):
    uid = "cog_cal_user"
    out = calibrate_from_outcome(
        user_id=uid,
        decision_core={"selected_strategy": "instant"},
        ok=True,
        critic_score=90,
        revision_happened=False,
        web_used=False,
        web_required=False,
        tool_successes=0,
        tool_failures=0,
        correction_this_turn=False,
    )
    assert "CAL_SUCCESS_REINFORCE" in out["signals"]
    out2 = calibrate_from_outcome(
        user_id=uid,
        decision_core={"selected_strategy": "research"},
        ok=False,
        critic_score=40,
        revision_happened=True,
        web_used=False,
        web_required=True,
        tool_successes=0,
        tool_failures=1,
        correction_this_turn=True,
    )
    assert any(s.startswith("CAL_") for s in out2["signals"])


def test_memory_psyche_identity_influence_codes():
    class _Mem:
        loaded = True
        contradiction_alerts = ["a vs b"]
        top_facts = []
        top_preferences = []

    class _Psy:
        loaded = True
        mode = "cautious"
        directness_bias = 0.4
        verbosity_bias = 0.2
        friction = 0.6
        humour_bias = 0.1

    class _Id:
        top_preferences = [{"title": "lubię zwięźle"}]
        active_habits = [{"habit_name": "short"}]
        autobio_summary = "dev"
        relation_trust = 0.7

    pa = analyze_pragmatics(raw_text="napraw import json w całym module ops.py i testach", history=[], user_id="")
    pack = build_cognitive_influence_pack(
        user_id="",
        session_id="s",
        message="napraw import json w całym module ops.py i testach",
        history=[{"role": "user", "content": "x"}],
        pragmatics=pa,
        memory_v2_ctx=_Mem(),
        psyche_v2_ctx=_Psy(),
        identity_snapshot=_Id(),
        selected_goal={"title": "Ship release", "urgency": 0.95},
        experience_signal_summary="prior_fail_similar",
        correction_hints="pisz krócej",
    )
    codes = set(pack.influence_reason_codes)
    assert "COG_MEMORY_INFLUENCE" in codes
    assert "COG_PSYCHE_STYLE" in codes
    assert "COG_IDENTITY_INFLUENCE" in codes
    assert "COG_GOALS_INFLUENCE" in codes
    assert "COG_GOALS_PLANNER" in codes
    assert "COG_EXPERIENCE_BIAS" in codes
    assert "COG_CORRECTION_BINDING" in codes
    assert pack.force_planner_analysis is True
    assert pack.length_directive == "short"
