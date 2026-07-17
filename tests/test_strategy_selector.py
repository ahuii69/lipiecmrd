#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""StrategySelector + decision_engine + escalation trace contracts."""

from __future__ import annotations

from aihub.chat_runtime import ChatRuntime
from aihub.decision_engine import decide_execution_path
from aihub.strategy_selector import (
    MemoryRoutingSummary,
    PsycheRoutingSummary,
    StrategySelector,
    _apply_psyche_modulation_select_dict,
    listing_copy_no_web_intent,
    select_strategy,
    short_followup_no_web_intent,
)


class TestStrategySelectorClass:
    def test_instant_arithmetic_question(self) -> None:
        ctx = {
            "memory_has_relevant": False,
            "memory_task_continuation": False,
            "history_turns": 0,
            "active_goals_count": 0,
            "goal_max_urgency": 0.0,
            "has_url": False,
        }
        out = StrategySelector().select_strategy("ile to 2+2", ctx)
        assert out["strategy"] == "instant"
        assert out["requires_research"] is False
        assert out["requires_planning"] is False

    def test_contextual_prior_reference(self) -> None:
        ctx = {
            "memory_has_relevant": False,
            "memory_task_continuation": False,
            "history_turns": 0,
            "active_goals_count": 0,
            "goal_max_urgency": 0.0,
            "has_url": False,
        }
        out = StrategySelector().select_strategy(
            "co mówiłem wcześniej o bazarku",
            ctx,
        )
        assert out["strategy"] == "contextual"
        assert out["requires_memory"] is True

    def test_research_oil_prices(self) -> None:
        ctx = {
            "memory_has_relevant": False,
            "memory_task_continuation": False,
            "history_turns": 0,
            "active_goals_count": 0,
            "goal_max_urgency": 0.0,
            "has_url": False,
        }
        out = StrategySelector().select_strategy(
            "znajdź aktualne ceny ropy",
            ctx,
        )
        assert out["strategy"] == "research"
        assert out["requires_research"] is True

    def test_agentic_heat_pump_analysis(self) -> None:
        ctx = {
            "memory_has_relevant": False,
            "memory_task_continuation": False,
            "history_turns": 0,
            "active_goals_count": 0,
            "goal_max_urgency": 0.0,
            "has_url": False,
        }
        out = StrategySelector().select_strategy(
            "przeanalizuj rynek pomp ciepła i daj wnioski",
            ctx,
        )
        assert out["strategy"] == "agentic"
        assert out["requires_planning"] is True


class TestEscalationMapping:
    def test_mapping_table(self) -> None:
        assert decide_execution_path({"strategy": "instant"})["final_mode"] == "direct"
        assert decide_execution_path({"strategy": "instant"})["use_reasoning"] is False
        assert (
            decide_execution_path({"strategy": "contextual"})["final_mode"]
            == "memory_augmented"
        )
        assert (
            decide_execution_path({"strategy": "research"})["final_mode"] == "research"
        )
        assert decide_execution_path({"strategy": "agentic"})["final_mode"] == "planner"
        assert decide_execution_path({"strategy": "agentic"})["use_reasoning"] is True
        assert decide_execution_path({"strategy": "agentic"})["use_tools"] is True


class TestSelectStrategyModule:
    def test_module_select_includes_trace_payload_selector_output(self) -> None:
        selection = select_strategy(
            user_id="strategy_test_user",
            user_text="krótka wiadomość testowa",
            mode="chat",
            active_goals_summary=None,
            history=[],
        )
        assert selection.trace_payload.get("selector_output") is not None
        assert selection.selector_output.get("strategy") == selection.selected_strategy

    def test_history_does_not_force_override_when_empty_override_context(self) -> None:
        """Empty history list behaves like zero prior turns for classification."""
        selection = select_strategy(
            user_id="strategy_hist_user",
            user_text="hello",
            mode="chat",
            history=[],
        )
        assert selection.selected_strategy in ("instant", "contextual", "research")

    def test_listing_copy_downgrades_research_to_instant_and_web_off(self) -> None:
        """Ogłoszenie/Vinted: nie trzymaj web_decision=required przez słowo „znajdź”."""
        selection = select_strategy(
            user_id="strategy_listing_user",
            user_text="znajdź mi słowa kluczowe pod ogłoszenie na vinted",
            mode="chat",
            history=[],
        )
        assert selection.selected_strategy == "instant"
        assert selection.trace_payload.get("web_decision") == "off"


class TestListingCopyIntent:
    def test_intent_detects_vinted_without_url(self) -> None:
        assert listing_copy_no_web_intent("zrób opis na Vinted")
        assert listing_copy_no_web_intent("zrób opis mieszkania")
        assert listing_copy_no_web_intent("opisz ten produkt")
        assert not listing_copy_no_web_intent("https://vinted.pl/item/1")

    def test_intent_false_for_generic(self) -> None:
        assert not listing_copy_no_web_intent("co to jest Python")


class TestShortFollowupIntent:
    def test_edit_followup_forces_local_no_web(self) -> None:
        selection = select_strategy(
            user_id="strategy_followup_user",
            user_text="Popraw",
            mode="chat",
            history=[
                {"role": "user", "content": "Napisz opis sprzedaży Volvo v70"},
                {"role": "assistant", "content": "Jasne, propozycja opisu..."},
            ],
        )
        assert selection.selected_strategy in ("instant", "contextual")
        assert selection.trace_payload.get("web_decision") == "off"
        assert "short_followup" in str(selection.short_explanation)

    def test_short_followup_intent_helper(self) -> None:
        assert short_followup_no_web_intent(
            "Krócej",
            history=[
                {"role": "user", "content": "Napisz opis"},
                {"role": "assistant", "content": "Opis..."},
            ],
        )
        assert short_followup_no_web_intent("Kurwo", history=[])
        assert not short_followup_no_web_intent("sprawdź w internecie", history=[])

    def test_missing_parameter_followup_stays_off_web(self) -> None:
        selection = select_strategy(
            user_id="strategy_followup_missing_param",
            user_text="Skąd wiesz z którego roku? nie podałem roku",
            mode="chat",
            history=[
                {"role": "user", "content": "Napisz opis sprzedaży Volvo v70"},
                {"role": "assistant", "content": "Opis sprzedażowy..."},
            ],
        )
        assert selection.trace_payload.get("web_decision") == "off"

    def test_listing_with_active_goals_stays_local(self) -> None:
        selection = select_strategy(
            user_id="strategy_listing_goal_user",
            user_text="Zrób opis mieszkania",
            mode="chat",
            active_goals_summary={"active_count": 3, "max_urgency": 0.95},
            history=[],
        )
        assert selection.selected_strategy in ("instant", "contextual")
        assert selection.trace_payload.get("web_decision") == "off"

    def test_followup_with_active_goals_stays_local(self) -> None:
        selection = select_strategy(
            user_id="strategy_followup_goal_user",
            user_text="Popraw",
            mode="chat",
            active_goals_summary={"active_count": 3, "max_urgency": 0.95},
            history=[
                {"role": "user", "content": "Napisz opis sprzedaży Volvo v70"},
                {"role": "assistant", "content": "Opis..."},
            ],
        )
        assert selection.selected_strategy in ("instant", "contextual")
        assert selection.trace_payload.get("web_decision") == "off"

    def test_explicit_current_prices_beats_active_goal_agentic(self) -> None:
        selection = select_strategy(
            user_id="strategy_current_prices_goal_user",
            user_text="Jakie są dziś ceny mieszkań w Warszawie?",
            mode="chat",
            active_goals_summary={"active_count": 3, "max_urgency": 0.95},
            history=[],
        )
        assert selection.selected_strategy == "research"
        assert selection.trace_payload.get("web_decision") == "required"


class TestPsycheModulation:
    def test_psyche_does_not_hijack_agentic_into_research(self) -> None:
        raw = {
            "strategy": "agentic",
            "confidence": 0.82,
            "requires_memory": True,
            "requires_research": False,
            "requires_planning": True,
            "reason": "Multi-step task",
        }
        psyche = PsycheRoutingSummary(
            energy=0.25,
            focus=0.45,
            tension_signal=0.72,
            frustration_signal=0.4,
        )
        memory = MemoryRoutingSummary(has_relevant_memory=False)
        _apply_psyche_modulation_select_dict(raw, psyche, memory)
        # Tone/confidence only — never demote agentic → contextual/research.
        assert raw["strategy"] == "agentic"
        assert raw["requires_research"] is False
        assert raw["requires_planning"] is True
        assert "psyche:" in raw["reason"]
        assert float(raw["confidence"]) < 0.82


class TestFinalizeEscalationTrace:
    def test_finalize_sets_execution_fields(self) -> None:
        rt = object.__new__(ChatRuntime)
        dc = {
            "selected_strategy": "agentic",
            "strategy_confidence": 0.9,
            "selector_output_snapshot": {
                "strategy": "instant",
                "confidence": 0.5,
                "requires_memory": False,
                "requires_research": False,
                "requires_planning": False,
                "reason": "ignored after merge",
            },
            "strategy_short_explanation": "merged path",
            "strategy_hints": "",
        }
        ChatRuntime._finalize_escalation(rt, dc)
        assert dc["execution_mode"] == "planner"
        assert dc["escalation_path"]["final_mode"] == "planner"
        assert dc["escalation_path"]["use_reasoning"] is True
        assert dc["escalation_use_reasoning"] is True
        assert dc["escalation_use_tools"] is True
        assert dc["strategy_selected"]["strategy"] == "agentic"
        assert dc["escalation_final_mode"] == "planner"

    def test_trace_escalation_slice_matches_decision_core(self) -> None:
        dc = {
            "selected_strategy": "contextual",
            "strategy_selected": {"strategy": "contextual", "confidence": 0.8},
            "execution_mode": "memory_augmented",
            "escalation_path": {
                "final_mode": "memory_augmented",
                "use_reasoning": False,
                "use_tools": True,
            },
            "escalation_use_reasoning": False,
            "escalation_use_tools": True,
            "selector_output_snapshot": {"strategy": "contextual"},
        }
        sl = ChatRuntime._decision_core_trace_escalation(dc)
        assert sl["execution_mode"] == "memory_augmented"
        assert sl["escalation_path"]["final_mode"] == "memory_augmented"
        assert sl["escalation_use_tools"] is True
        assert sl["selector_output_snapshot"]["strategy"] == "contextual"


class TestInstantBlocklistRouting:
    def test_sprawdz_not_instant(self) -> None:
        ctx = {
            "memory_has_relevant": False,
            "memory_task_continuation": False,
            "history_turns": 0,
            "active_goals_count": 0,
            "goal_max_urgency": 0.0,
            "has_url": False,
        }
        out = StrategySelector().select_strategy("sprawdź to krótko", ctx)
        assert out["strategy"] == "research"
        assert out["requires_research"] is True


class TestTimeSensitiveResearchRouting:
    """06.07 sprint: time-sensitive / sports / news queries must NOT go contextual/instant-only."""

    _CTX = {
        "memory_has_relevant": False,
        "memory_task_continuation": False,
        "history_turns": 0,
        "active_goals_count": 0,
        "goal_max_urgency": 0.0,
        "has_url": False,
    }

    def test_france_morocco_match_yesterday(self) -> None:
        q = "sprawdź wynik meczu mistrzostw świata z wczoraj francji maroko"
        out = StrategySelector().select_strategy(q, self._CTX)
        sel = select_strategy(user_id="web_sports", user_text=q, mode="chat", history=[])
        assert out["strategy"] == "research"
        assert out["requires_research"] is True
        assert sel.web_decision == "required"
        assert "RESEARCH_NEEDED" in sel.reason_codes
        assert "EXPLICIT_CHECK_REQUEST" in sel.reason_codes
        assert "SPORTS_RESULT_QUERY" in sel.reason_codes

    def test_generic_yesterday_match_result(self) -> None:
        q = "jaki był wynik meczu wczoraj"
        out = StrategySelector().select_strategy(q, self._CTX)
        sel = select_strategy(user_id="web_yday", user_text=q, mode="chat", history=[])
        assert out["strategy"] == "research"
        assert out["requires_research"] is True
        assert sel.web_decision == "required"
        assert "TIME_SENSITIVE_QUERY" in sel.reason_codes
        assert "SPORTS_RESULT_QUERY" in sel.reason_codes

    def test_latest_openai_news(self) -> None:
        q = "najświeższe newsy o OpenAI"
        out = StrategySelector().select_strategy(q, self._CTX)
        sel = select_strategy(user_id="web_news", user_text=q, mode="chat", history=[])
        assert out["strategy"] == "research"
        assert sel.web_decision == "required"

    def test_bitcoin_price_now(self) -> None:
        q = "ile kosztuje teraz bitcoin"
        out = StrategySelector().select_strategy(q, self._CTX)
        sel = select_strategy(user_id="web_btc", user_text=q, mode="chat", history=[])
        assert out["strategy"] == "research"
        assert sel.web_decision == "required"
        assert "TIME_SENSITIVE_QUERY" in sel.reason_codes

    def test_general_football_knowledge_stays_local(self) -> None:
        q = "opowiedz mi czym jest spalony w piłce"
        out = StrategySelector().select_strategy(q, self._CTX)
        sel = select_strategy(user_id="web_general", user_text=q, mode="chat", history=[])
        assert out["strategy"] == "instant"
        assert out["requires_research"] is False
        assert sel.web_decision == "off"
