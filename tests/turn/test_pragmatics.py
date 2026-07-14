"""Unit tests for conversation pragmatics / intent understanding."""

from __future__ import annotations

from datetime import date

from aihub.turn.pragmatics import (
    analyze_pragmatics,
    apply_pragmatics_to_strategy,
    critique_response,
    pragmatics_prompt_block,
    pragmatics_trace_fields,
)


def test_literal_ice_cream_recipe_no_innuendo():
    pa = analyze_pragmatics(raw_text="Jak zrobić lody waniliowe?", history=[], user_id="")
    assert pa.primary_intent == "literal_food_or_recipe"
    assert pa.sexual_innuendo_detected is False
    assert pa.teasing_detected is False
    assert pa.ambiguity_score < 0.55
    assert pa.recommended_strategy in ("instant", "contextual")


def test_lody_robisz_ambiguity_blocks_instant():
    pa = analyze_pragmatics(raw_text="Lody robisz?", history=[], user_id="")
    assert pa.sexual_innuendo_detected is True
    assert pa.teasing_detected is True
    assert pa.ambiguity_score >= 0.55
    assert pa.recommended_strategy != "instant"
    assert "PRAGMATICS_SEXUAL_INNUENDO_DETECTED" in pa.reason_codes
    s, codes, _, _ = apply_pragmatics_to_strategy(
        selected_strategy="instant",
        reason_codes=[],
        web_decision="off",
        web_decision_reason="",
        pragmatics=pa,
    )
    assert s == "contextual"
    assert "PRAGMATICS_AMBIGUITY_BLOCKED_INSTANT" in codes


def test_lody_robisz_without_history_no_auto_recipe_bias():
    pa = analyze_pragmatics(raw_text="Lody robisz?", history=[], user_id="")
    assert pa.primary_intent == "sexual_teasing"
    assert pa.response_mode == "teasing_reply"
    block = pragmatics_prompt_block(pa)
    assert "przepis" in block.lower() or "zaczep" in block.lower()
    assert "NIE dawaj przepisu" in block or "dwuznaczność" in block


def test_sarcasm_after_failure_not_praise():
    pa = analyze_pragmatics(
        raw_text="No zajebiście to naprawiłeś",
        history=[{"role": "assistant", "content": "TypeError: fix applied"}],
        user_id="",
    )
    assert pa.sarcasm_detected is True
    assert pa.speech_act == "sarcasm"
    assert pa.primary_intent != "praise"
    assert pa.primary_intent == "sarcastic_complaint"


def test_genialnie_znowu_nie_dziala():
    pa = analyze_pragmatics(raw_text="Genialnie, znowu nie działa", history=[], user_id="")
    assert pa.sarcasm_detected is True
    assert pa.frustration_detected or pa.conversation_state in ("frustration", "argument")


def test_ten_wczorajszy_context_dependency():
    pa = analyze_pragmatics(raw_text="ten wczorajszy jeszcze działa?", history=[], user_id="")
    assert pa.context_dependency_score >= 0.55
    assert pa.needs_recent_history is True
    assert "PRAGMATICS_CONTEXT_REQUIRED" in pa.reason_codes


def test_mecz_przed_wczoraj_temporal_normalization():
    pa = analyze_pragmatics(
        raw_text="mecz gramy przed wczoraj",
        history=[],
        user_id="",
        today=date(2026, 7, 13),
    )
    assert pa.typo_or_grammar_noise is True
    assert pa.temporal_reference_detected is True
    assert pa.normalized_temporal_reference == "przedwczoraj"
    assert pa.relative_date == "2026-07-11"
    assert "PRAGMATICS_TEMPORAL_NORMALIZATION" in pa.reason_codes
    assert "przed wczoraj" not in (pa.rewritten_query_for_tools or "")


def test_world_cup_research_rewritten_query():
    pa = analyze_pragmatics(
        raw_text="mistrzostwa świata 2026 mecz gramy przed wczoraj",
        history=[],
        user_id="",
        today=date(2026, 7, 13),
    )
    assert pa.needs_web is True
    assert pa.recommended_strategy == "research"
    assert pa.primary_intent == "sports_result_research"
    assert pa.rewritten_query_for_tools
    assert "przed wczoraj" not in pa.rewritten_query_for_tools
    assert "2026-07-11" in pa.rewritten_query_for_tools
    s, codes, web, _ = apply_pragmatics_to_strategy(
        selected_strategy="instant",
        reason_codes=[],
        web_decision="off",
        web_decision_reason="",
        pragmatics=pa,
    )
    assert s == "research"
    assert web == "required"
    assert "PRAGMATICS_WEB_QUERY_REWRITE" in codes


def test_simple_tech_no_planner():
    pa = analyze_pragmatics(raw_text="napraw import json", history=[], user_id="")
    assert pa.primary_intent == "simple_technical_fix"
    assert pa.needs_planner is False
    assert pa.speech_act == "task_instruction"
    assert pa.recommended_strategy == "instant"


def test_elo_greeting_short_natural():
    pa = analyze_pragmatics(raw_text="elo", history=[], user_id="")
    assert pa.speech_act == "greeting"
    assert pa.recommended_strategy == "instant"
    assert pa.response_mode == "concise_direct"


def test_frustration_diagnostic():
    pa = analyze_pragmatics(raw_text="czemu to gówno nie działa", history=[], user_id="")
    assert pa.frustration_detected is True
    assert pa.response_mode == "diagnostic"
    assert pa.conversation_state == "frustration"


def test_correction_learning_writes_and_biases(isolated_db):
    hist = [
        {"role": "user", "content": "Lody robisz?"},
        {"role": "assistant", "content": "Przepis na lody waniliowe..."},
    ]
    pa = analyze_pragmatics(
        raw_text="Chodziło o obciąganie kutasa.",
        history=hist,
        user_id="prag_corr_user",
        session_id="sess1",
        turn_id="t-corr-1",
    )
    assert pa.speech_act == "correction"
    assert pa.correction_learned is True
    assert "PRAGMATICS_CORRECTION_LEARNED" in pa.reason_codes

    pa2 = analyze_pragmatics(
        raw_text="Lody robisz?",
        history=[],
        user_id="prag_corr_user",
        session_id="sess1",
        turn_id="t-corr-2",
    )
    assert pa2.sexual_innuendo_detected is True
    assert "PRAGMATICS_CORRECTION_BIAS" in pa2.reason_codes or pa2.primary_intent == "sexual_teasing"


def test_irony_without_swearing():
    pa = analyze_pragmatics(raw_text="No jasne, znowu nie działa", history=[], user_id="")
    assert pa.irony_detected is True
    assert pa.aggression_detected is False


def test_other_sexual_innuendo_words():
    pa = analyze_pragmatics(raw_text="dasz banańczyka?", history=[], user_id="")
    assert pa.sexual_innuendo_detected is True
    assert pa.teasing_detected is True
    assert pa.recommended_strategy != "instant"


def test_low_confidence_ambiguity_clarification_mode_allowed():
    # Short unknown elliptical tease → ambiguity high, teasing_reply or clarification ok
    pa = analyze_pragmatics(raw_text="a to?", history=[], user_id="")
    assert pa.ambiguity_score >= 0.55 or pa.context_dependency_score >= 0.55
    assert pa.recommended_strategy != "instant"


def test_high_confidence_with_flirty_history_no_clarification_needed():
    pa = analyze_pragmatics(
        raw_text="Lody robisz?",
        history=[
            {"role": "user", "content": "ej flircik hehe"},
            {"role": "assistant", "content": "co tam"},
        ],
        user_id="",
    )
    assert pa.sexual_innuendo_detected is True
    assert pa.response_mode == "teasing_reply"
    assert pa.speech_act == "teasing"


def test_meta_request():
    pa = analyze_pragmatics(raw_text="Pokaż wszystkie endpointy", history=[], user_id="")
    assert pa.speech_act == "meta_request"
    assert pa.meta_intent
    assert pa.primary_intent == "meta_audit_request"
    assert pa.recommended_strategy == "contextual"
    assert pa.needs_reasoning is True


def test_conversation_state_transition_teasing_to_correction(isolated_db):
    pa1 = analyze_pragmatics(raw_text="Lody robisz?", history=[], user_id="st_user", session_id="s")
    assert pa1.conversation_state == "teasing"
    pa2 = analyze_pragmatics(
        raw_text="Chodziło o something else sexual",
        history=[{"role": "user", "content": "Lody robisz?"}],
        user_id="st_user",
        session_id="s",
    )
    # "chodziło o" triggers correction path
    pa2 = analyze_pragmatics(
        raw_text="Chodziło o podtekst, nie deser.",
        history=[{"role": "user", "content": "Lody robisz?"}],
        user_id="st_user",
        session_id="s",
    )
    assert pa2.conversation_state == "correction"


def test_response_critic_detects_literal_misread():
    pa = analyze_pragmatics(raw_text="Lody robisz?", history=[], user_id="")
    cr = critique_response(
        response_text="Jasne! Przepis na lody: składniki… ubij śmietanę…",
        pragmatics=pa,
    )
    assert cr.passed is False
    assert cr.score < 70
    assert "CRITIC_LITERAL_MISREAD_INNUENDO" in cr.reason_codes
    assert cr.revision_instruction


def test_response_critic_passes_good_tease_reply():
    pa = analyze_pragmatics(raw_text="Lody robisz?", history=[], user_id="")
    cr = critique_response(
        response_text="Ha, łapiesz dwuznacznie — mów wprost, o co Ci chodzi.",
        pragmatics=pa,
    )
    assert cr.passed is True
    assert cr.score >= 70


def test_trace_fields_shape():
    pa = analyze_pragmatics(raw_text="Lody robisz?", history=[], user_id="")
    pa.strategy_before = "instant"
    pa.strategy_after = "contextual"
    tr = pragmatics_trace_fields(pa)
    assert tr["pragmatics_analysis_happened"] is True
    assert tr["sexual_innuendo_detected"] is True
    assert tr["instant_path_blocked_by_pragmatics"] is True
    assert "ambiguity_score" in tr
    assert "speech_act" in tr


def test_ten_wczorajszy_demotes_blind_research():
    from aihub.turn.pragmatics import analyze_pragmatics, apply_pragmatics_to_strategy

    pa = analyze_pragmatics(raw_text="ten wczorajszy jeszcze działa?", history=[], user_id="")
    s, codes, web, _ = apply_pragmatics_to_strategy(
        selected_strategy="research",
        reason_codes=["STRATEGY_RULE_RESEARCH_KEYWORD"],
        web_decision="required",
        web_decision_reason="keyword",
        pragmatics=pa,
    )
    assert s == "contextual"
    assert web == "off"
    assert "PRAGMATICS_CONTEXT_REQUIRED" in codes


def test_critic_rejects_image_prompt_for_innuendo():
    pa = analyze_pragmatics(raw_text="dasz banańczyka?", history=[], user_id="")
    cr = critique_response(
        response_text="Oto prompt do Midjourney na banana...",
        pragmatics=pa,
    )
    assert cr.passed is False
    assert "CRITIC_LITERAL_PRODUCT_FOR_INNUENDO" in cr.reason_codes


def test_critic_rejects_thanks_on_sarcasm():
    pa = analyze_pragmatics(raw_text="No zajebiście to naprawiłeś", history=[], user_id="")
    cr = critique_response(
        response_text="Dzięki, mordo! Cieszę się, że tak trafiło.",
        pragmatics=pa,
    )
    assert cr.passed is False
    assert "CRITIC_TOOK_SARCASM_AS_PRAISE" in cr.reason_codes
