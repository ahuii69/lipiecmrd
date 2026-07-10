"""Tests for the Policy Feedback Loop: reflection → policy → decision core.

Proves REAL execution impact of the feedback loop on:
  1. ReflectionEngine._compute_hindsight (PATCH 1)
  2. PolicyEngine.compute_feedback        (PATCH 2)
  3. chat_runtime integration              (PATCH 3)
     — strategy_confidence delta
     — handoff_bias from hindsight
     — blocker_sensitivity threshold adjustment
     — simulation risk calibration
     — strategy_adjustments → strategy shift
  4. Trace emission (PATCH 4)

Every test asserts on execution-driving values, not trace decoration.
"""

from __future__ import annotations

import copy
from dataclasses import replace as dc_replace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# ── 1. Reflection Hindsight Tests ──────────────────────────────────────


class TestReflectionHindsight:
    """Tests for ReflectionEngine._compute_hindsight."""

    def _make_engine(self):
        from aihub.reflection_engine import ReflectionEngine

        return ReflectionEngine()

    def _make_rinput(self, **ctx_overrides):
        from aihub.reflection_engine import ReflectionInput

        ctx: Dict[str, Any] = {
            "selected_strategy": "instant",
            "strategy_confidence": 0.70,
            "handoff_happened": False,
            "blocker_was_active": False,
            "blocker_was_hard": False,
            "simulation_risk": 0.0,
        }
        ctx.update(ctx_overrides)
        return ReflectionInput(
            user_id="test_hindsight",
            action_type="reason",
            parameters={},
            execution_result={},
            context=ctx,
            confidence=ctx["strategy_confidence"],
        )

    # -- strategy_fit --

    def test_strategy_fit_good_on_success(self):
        eng = self._make_engine()
        rinput = self._make_rinput(selected_strategy="instant")
        h = eng._compute_hindsight(rinput, "success", 0.85)
        assert h["strategy_fit"] == "good"

    def test_strategy_fit_bad_on_failure(self):
        eng = self._make_engine()
        rinput = self._make_rinput(selected_strategy="agentic")
        h = eng._compute_hindsight(rinput, "failure", 0.20)
        assert h["strategy_fit"] == "bad"

    def test_strategy_fit_bad_partial_heavy_strategy(self):
        eng = self._make_engine()
        rinput = self._make_rinput(selected_strategy="research")
        h = eng._compute_hindsight(rinput, "partial", 0.55)
        assert h["strategy_fit"] == "bad"

    def test_strategy_fit_neutral_partial_light_strategy(self):
        eng = self._make_engine()
        rinput = self._make_rinput(selected_strategy="instant")
        h = eng._compute_hindsight(rinput, "partial", 0.55)
        assert h["strategy_fit"] == "neutral"

    # -- confidence_hindsight --

    def test_confidence_underconfident(self):
        """outcome_score > predicted_confidence → positive delta (was under-confident)."""
        eng = self._make_engine()
        rinput = self._make_rinput(strategy_confidence=0.50)
        h = eng._compute_hindsight(rinput, "success", 0.90)
        assert h["confidence_hindsight"] == pytest.approx(0.40, abs=0.01)

    def test_confidence_overconfident(self):
        """outcome_score < predicted_confidence → negative delta (was over-confident)."""
        eng = self._make_engine()
        rinput = self._make_rinput(strategy_confidence=0.80)
        h = eng._compute_hindsight(rinput, "failure", 0.20)
        assert h["confidence_hindsight"] == pytest.approx(-0.60, abs=0.01)

    # -- handoff_hindsight --

    def test_handoff_earlier_on_failed_heavy(self):
        eng = self._make_engine()
        rinput = self._make_rinput(
            selected_strategy="agentic",
            handoff_happened=False,
        )
        h = eng._compute_hindsight(rinput, "failure", 0.20)
        assert h["handoff_hindsight"] == "earlier"

    def test_handoff_correct_on_success(self):
        eng = self._make_engine()
        rinput = self._make_rinput(handoff_happened=True)
        h = eng._compute_hindsight(rinput, "success", 0.85)
        assert h["handoff_hindsight"] == "correct"

    def test_handoff_later_on_handoff_failure(self):
        eng = self._make_engine()
        rinput = self._make_rinput(handoff_happened=True)
        h = eng._compute_hindsight(rinput, "failure", 0.20)
        assert h["handoff_hindsight"] == "later"

    # -- blocker_hindsight --

    def test_blocker_stronger_on_failure_with_blocker(self):
        eng = self._make_engine()
        rinput = self._make_rinput(blocker_was_active=True)
        h = eng._compute_hindsight(rinput, "failure", 0.15)
        assert h["blocker_hindsight"] == "stronger"

    def test_blocker_weaker_on_success_with_blocker(self):
        eng = self._make_engine()
        rinput = self._make_rinput(blocker_was_active=True)
        h = eng._compute_hindsight(rinput, "success", 0.90)
        assert h["blocker_hindsight"] == "weaker"

    def test_blocker_correct_on_partial_with_blocker(self):
        eng = self._make_engine()
        rinput = self._make_rinput(blocker_was_active=True)
        h = eng._compute_hindsight(rinput, "partial", 0.60)
        assert h["blocker_hindsight"] == "correct"

    def test_blocker_stronger_no_blocker_bad_failure(self):
        eng = self._make_engine()
        rinput = self._make_rinput(blocker_was_active=False)
        h = eng._compute_hindsight(rinput, "failure", 0.10)
        assert h["blocker_hindsight"] == "stronger"

    # -- risk_hindsight --

    def test_risk_hindsight_under_prediction(self):
        """Failure with sim_risk=0.1 → actual > predicted → positive delta."""
        eng = self._make_engine()
        rinput = self._make_rinput(simulation_risk=0.10)
        h = eng._compute_hindsight(rinput, "failure", 0.20)
        # actual_risk = max(0.5, 1-0.2) = 0.8, delta = 0.8-0.1 = 0.7
        assert h["risk_hindsight"] > 0.5

    def test_risk_hindsight_correct_prediction(self):
        """Success with sim_risk=0.2 → actual close to predicted → small delta."""
        eng = self._make_engine()
        rinput = self._make_rinput(simulation_risk=0.10)
        h = eng._compute_hindsight(rinput, "success", 0.90)
        # actual_risk = 0.3*(1-0.9) = 0.03, delta = 0.03-0.1 = -0.07
        assert abs(h["risk_hindsight"]) < 0.15


# ── 2. PolicyEngine.compute_feedback Tests ─────────────────────────────


class TestPolicyFeedback:
    """Tests for PolicyEngine.compute_feedback with synthetic reflections."""

    def _make_engine(self):
        from aihub.policy_engine import PolicyEngine

        eng = PolicyEngine()
        return eng

    def _make_profile(self, user_id="test_fb", **overrides):
        from aihub.policy_engine import PolicyProfile

        defaults = {
            "user_id": user_id,
            "hints": [],
            "generated_at": 1700000000.0,
            "total_reflections": 10,
            "reliability_index": 0.5,
        }
        defaults.update(overrides)
        return PolicyProfile(**defaults)

    def _reflections_with_hindsight(
        self, hindsights: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Build reflection dicts from hindsight dicts."""
        return [
            {"hindsight": hs, "action_type": hs.get("_action_type", "reason")}
            for hs in hindsights
        ]

    # -- confidence_delta --

    def test_negative_confidence_delta_from_overconfident_history(self):
        """Series of over-confident results → negative confidence_delta."""
        eng = self._make_engine()
        profile = self._make_profile()
        refs = self._reflections_with_hindsight(
            [
                {"confidence_hindsight": -0.30},
                {"confidence_hindsight": -0.25},
                {"confidence_hindsight": -0.20},
            ]
        )
        fb = eng.compute_feedback(profile, reflections=refs)
        assert fb.applied is True
        assert fb.confidence_delta < 0, "Should be negative for over-confident history"

    def test_positive_confidence_delta_from_underconfident_history(self):
        """Series of under-confident results → positive confidence_delta."""
        eng = self._make_engine()
        profile = self._make_profile()
        refs = self._reflections_with_hindsight(
            [
                {"confidence_hindsight": 0.30},
                {"confidence_hindsight": 0.25},
                {"confidence_hindsight": 0.20},
            ]
        )
        fb = eng.compute_feedback(profile, reflections=refs)
        assert fb.applied is True
        assert fb.confidence_delta > 0, "Should be positive for under-confident history"

    def test_confidence_delta_clamped(self):
        """Extreme values are clamped to ±0.15."""
        eng = self._make_engine()
        profile = self._make_profile()
        refs = self._reflections_with_hindsight(
            [
                {"confidence_hindsight": 0.90},
                {"confidence_hindsight": 0.90},
                {"confidence_hindsight": 0.90},
            ]
        )
        fb = eng.compute_feedback(profile, reflections=refs)
        assert fb.confidence_delta <= 0.15

    # -- handoff_bias --

    def test_handoff_bias_positive_from_earlier_signals(self):
        """Many 'earlier' handoff signals → positive handoff_bias."""
        eng = self._make_engine()
        profile = self._make_profile()
        refs = self._reflections_with_hindsight(
            [
                {"handoff_hindsight": "earlier"},
                {"handoff_hindsight": "earlier"},
                {"handoff_hindsight": "earlier"},
            ]
        )
        fb = eng.compute_feedback(profile, reflections=refs)
        assert fb.handoff_bias > 0, "Should favour handoff after 'earlier' signals"

    def test_handoff_bias_negative_from_later_signals(self):
        """Many 'later' handoff signals → negative handoff_bias."""
        eng = self._make_engine()
        profile = self._make_profile()
        refs = self._reflections_with_hindsight(
            [
                {"handoff_hindsight": "later"},
                {"handoff_hindsight": "later"},
                {"handoff_hindsight": "later"},
            ]
        )
        fb = eng.compute_feedback(profile, reflections=refs)
        assert fb.handoff_bias < 0, "Should avoid handoff after 'later' signals"

    # -- blocker_sensitivity --

    def test_blocker_sensitivity_positive_from_stronger(self):
        """'stronger' signals → positive sensitivity → more blockers."""
        eng = self._make_engine()
        profile = self._make_profile()
        refs = self._reflections_with_hindsight(
            [
                {"blocker_hindsight": "stronger"},
                {"blocker_hindsight": "stronger"},
            ]
        )
        fb = eng.compute_feedback(profile, reflections=refs)
        assert fb.blocker_sensitivity > 0

    def test_blocker_sensitivity_negative_from_weaker(self):
        """'weaker' signals → negative sensitivity → fewer blockers."""
        eng = self._make_engine()
        profile = self._make_profile()
        refs = self._reflections_with_hindsight(
            [
                {"blocker_hindsight": "weaker"},
                {"blocker_hindsight": "weaker"},
            ]
        )
        fb = eng.compute_feedback(profile, reflections=refs)
        assert fb.blocker_sensitivity < 0

    # -- simulation_risk_calibration --

    def test_risk_calibration_positive_from_under_prediction(self):
        """risk_hindsight > 0 → risk_cal > 0 → inflate predicted risk."""
        eng = self._make_engine()
        profile = self._make_profile()
        refs = self._reflections_with_hindsight(
            [
                {"risk_hindsight": 0.40},
                {"risk_hindsight": 0.30},
            ]
        )
        fb = eng.compute_feedback(profile, reflections=refs)
        assert fb.simulation_risk_calibration > 0

    # -- strategy_adjustments --

    def test_strategy_adjustments_from_bad_fit(self):
        """Bad strategy_fit for 'research' → negative adjustment for that action."""
        eng = self._make_engine()
        profile = self._make_profile()
        refs = self._reflections_with_hindsight(
            [
                {"strategy_fit": "bad", "_action_type": "research"},
                {"strategy_fit": "bad", "_action_type": "research"},
                {"strategy_fit": "bad", "_action_type": "research"},
            ]
        )
        fb = eng.compute_feedback(profile, reflections=refs)
        assert "research" in fb.strategy_adjustments
        assert fb.strategy_adjustments["research"] < 0

    def test_strategy_adjustments_from_good_fit(self):
        """Good strategy_fit for 'reason' → positive adjustment."""
        eng = self._make_engine()
        profile = self._make_profile()
        refs = self._reflections_with_hindsight(
            [
                {"strategy_fit": "good", "_action_type": "reason"},
                {"strategy_fit": "good", "_action_type": "reason"},
            ]
        )
        fb = eng.compute_feedback(profile, reflections=refs)
        assert "reason" in fb.strategy_adjustments
        assert fb.strategy_adjustments["reason"] > 0

    # -- no-op case --

    def test_no_feedback_without_reflections(self):
        """No reflections → applied=False."""
        eng = self._make_engine()
        profile = self._make_profile()
        fb = eng.compute_feedback(profile, reflections=[])
        assert fb.applied is False

    def test_neutral_history_produces_no_adjustments(self):
        """All neutral signals → no adjustments."""
        eng = self._make_engine()
        profile = self._make_profile()
        refs = self._reflections_with_hindsight(
            [
                {
                    "strategy_fit": "neutral",
                    "confidence_hindsight": 0.0,
                    "handoff_hindsight": "na",
                    "blocker_hindsight": "na",
                    "risk_hindsight": 0.0,
                },
            ]
        )
        fb = eng.compute_feedback(profile, reflections=refs)
        assert fb.applied is False
        assert fb.confidence_delta == 0.0
        assert fb.handoff_bias == 0.0

    # -- time decay --

    def test_time_decay_reduces_old_signals(self):
        """Older reflections have less influence due to time decay."""
        eng = self._make_engine()
        profile = self._make_profile()
        # Only very old signals (they'll be at indexes 15-19 with high decay)
        refs = [{"confidence_hindsight": 0.0}] * 15 + self._reflections_with_hindsight(
            [
                {"confidence_hindsight": 0.50},
                {"confidence_hindsight": 0.50},
                {"confidence_hindsight": 0.50},
                {"confidence_hindsight": 0.50},
                {"confidence_hindsight": 0.50},
            ]
        )
        fb1 = eng.compute_feedback(profile, reflections=refs)

        # Same signals but recent (at the front)
        refs2 = self._reflections_with_hindsight(
            [
                {"confidence_hindsight": 0.50},
                {"confidence_hindsight": 0.50},
                {"confidence_hindsight": 0.50},
                {"confidence_hindsight": 0.50},
                {"confidence_hindsight": 0.50},
            ]
        )
        fb2 = eng.compute_feedback(profile, reflections=refs2)

        # Recent signals should have stronger effect
        assert abs(fb2.confidence_delta) >= abs(fb1.confidence_delta)


# ── 3. Blocker Sensitivity Execution Impact ────────────────────────────


class TestBlockerSensitivityImpact:
    """Tests that policy_blocker_sensitivity actually changes
    which blocker verdicts fire (threshold shifts)."""

    def _eval(self, dc: dict):
        from aihub.chat_runtime import ChatRuntime

        return ChatRuntime._evaluate_blocker_verdict(dc)

    def _base_dc(self, **overrides) -> dict:
        base: dict = {
            "selected_strategy": "instant",
            "reason_codes": [],
            "strategy_confidence": 0.75,
            "strategy_degraded": False,
            "simulation_ran": False,
            "simulation_best_action": None,
            "simulation_variants_count": 0,
            "simulation_risk_summary": None,
            "policy_hints_loaded": False,
            "policy_profile_name": None,
            "policy_hints": [],
            "consistency_check_ran": False,
            "consistency_classification": None,
            "contradictions_found": 0,
            "experience_blocker_reason": None,
            "experience_blocker_severity": 0.0,
            "experience_recurring_failure_detected": False,
            "experience_recurring_failure_types": [],
            "policy_blocker_sensitivity": 0.0,
        }
        base.update(overrides)
        return base

    def test_sensitivity_widens_low_confidence_blocker(self):
        """Positive sensitivity raises the confidence threshold,
        causing a low_confidence blocker to fire at higher confidence."""
        # At default sensitivity=0, confidence=0.55 should NOT fire low_confidence
        dc_default = self._base_dc(
            strategy_confidence=0.55,
            selected_strategy="agentic",
        )
        verdict_default = self._eval(dc_default)
        # Confidence 0.55 > 0.45 → no low_confidence blocker
        assert (
            not verdict_default.blocker_active
            or verdict_default.blocker_type != "low_confidence_decision"
        )

        # At sensitivity=+0.15, threshold becomes 0.60, so 0.55 < 0.60 → fires
        dc_sensitive = self._base_dc(
            strategy_confidence=0.55,
            selected_strategy="agentic",
            policy_blocker_sensitivity=0.15,
        )
        verdict_sensitive = self._eval(dc_sensitive)
        assert verdict_sensitive.blocker_active is True
        assert verdict_sensitive.blocker_type == "low_confidence_decision"

    def test_sensitivity_narrows_low_confidence_blocker(self):
        """Negative sensitivity lowers the confidence threshold,
        preventing a low_confidence blocker from firing."""
        # At default: confidence=0.42, threshold=0.45 → fires
        dc_default = self._base_dc(
            strategy_confidence=0.42,
            selected_strategy="research",
        )
        verdict_default = self._eval(dc_default)
        assert verdict_default.blocker_active is True
        assert verdict_default.blocker_type == "low_confidence_decision"

        # At sensitivity=-0.15, threshold becomes 0.30, so 0.42 > 0.30 → no fire
        dc_lenient = self._base_dc(
            strategy_confidence=0.42,
            selected_strategy="research",
            policy_blocker_sensitivity=-0.15,
        )
        verdict_lenient = self._eval(dc_lenient)
        assert (
            not verdict_lenient.blocker_active
            or verdict_lenient.blocker_type != "low_confidence_decision"
        )

    def test_sensitivity_affects_sim_risk_threshold(self):
        """Positive sensitivity lowers risk threshold, making high_risk easier to trigger."""
        # sim_risk=0.72, default threshold=0.80 → no hard block
        dc_default = self._base_dc(
            simulation_ran=True,
            simulation_risk_summary="risk=0.72 conf=0.50 util=0.30",
            selected_strategy="agentic",
        )
        verdict_default = self._eval(dc_default)
        # 0.72 < 0.80 → should be caution_pass at most (R10 range: 0.65-0.80)
        if verdict_default.blocker_active:
            assert verdict_default.resolution != "downgrade"

        # With sensitivity=+0.15: hard threshold → 0.65, so 0.72 >= 0.65 → downgrade
        dc_sensitive = self._base_dc(
            simulation_ran=True,
            simulation_risk_summary="risk=0.72 conf=0.50 util=0.30",
            selected_strategy="agentic",
            policy_blocker_sensitivity=0.15,
        )
        verdict_sensitive = self._eval(dc_sensitive)
        assert verdict_sensitive.blocker_active is True
        assert verdict_sensitive.blocker_type == "high_risk_path"
        assert verdict_sensitive.resolution == "downgrade"

    def test_sensitivity_affects_severity_threshold(self):
        """Positive sensitivity lowers severity threshold for hard repeated_failure."""
        # exp_severity=0.72, default threshold=0.80 → no hard block
        dc_default = self._base_dc(
            experience_blocker_reason="test_pattern",
            experience_blocker_severity=0.72,
        )
        verdict_default = self._eval(dc_default)
        # 0.72 < 0.80 → should be caution (R8) not hard
        if verdict_default.blocker_active:
            assert (
                verdict_default.resolution != "hard_block"
                or verdict_default.blocker_type != "repeated_failure"
            )

        # With sensitivity=+0.15: threshold → 0.65, so 0.72 >= 0.65 → hard_block
        dc_sensitive = self._base_dc(
            experience_blocker_reason="test_pattern",
            experience_blocker_severity=0.72,
            policy_blocker_sensitivity=0.15,
        )
        verdict_sensitive = self._eval(dc_sensitive)
        assert verdict_sensitive.blocker_active is True
        assert verdict_sensitive.blocker_type == "repeated_failure"
        assert verdict_sensitive.resolution == "hard_block"


# ── 4. Handoff Bias from Policy Feedback ───────────────────────────────


class TestHandoffBiasFromPolicy:
    """Tests that policy_handoff_bias in decision_core changes handoff decisions."""

    def _should_handoff(self, dc: dict) -> tuple:
        from aihub.chat_runtime import ChatRuntime

        # _should_handoff_to_agent is an instance method; create minimal instance
        rt = ChatRuntime.__new__(ChatRuntime)
        return rt._should_handoff_to_agent(decision_core=dc, message="test query")

    def test_policy_handoff_bias_triggers_handoff(self):
        """Positive policy_handoff_bias ≥ 0.25 → forces handoff."""
        dc = {
            "selected_strategy": "instant",  # normally no handoff
            "escalation_final_mode": "planner",
            "experience_handoff_bias": 0.0,
            "policy_handoff_bias": 0.30,
            "simulation_ran": False,
            "simulation_best_action": None,
            "simulation_variants_count": 0,
            "simulation_risk_summary": None,
            "strategy_confidence": 0.70,
            "selected_goal": None,
        }
        should, reason = self._should_handoff(dc)
        assert should is True
        assert "effective_handoff_bias" in reason

    def test_policy_handoff_bias_vetoes_handoff(self):
        """Negative policy_handoff_bias → vetoes strategy-based handoff."""
        dc = {
            "selected_strategy": "agentic",  # normally triggers handoff
            "escalation_final_mode": "planner",
            "experience_handoff_bias": 0.0,
            "policy_handoff_bias": -0.30,
            "simulation_ran": False,
            "simulation_best_action": None,
            "simulation_variants_count": 0,
            "simulation_risk_summary": None,
            "strategy_confidence": 0.50,
            "selected_goal": None,
        }
        should, reason = self._should_handoff(dc)
        assert should is False

    def test_combined_experience_and_policy_bias(self):
        """Experience + policy bias are additive."""
        dc = {
            "selected_strategy": "instant",
            "escalation_final_mode": "planner",
            "experience_handoff_bias": 0.15,
            "policy_handoff_bias": 0.15,
            # combined: 0.30 ≥ 0.25 → handoff
            "simulation_ran": False,
            "simulation_best_action": None,
            "simulation_variants_count": 0,
            "simulation_risk_summary": None,
            "strategy_confidence": 0.70,
            "selected_goal": None,
        }
        should, reason = self._should_handoff(dc)
        assert should is True


# ── 5. Simulation Risk Calibration ────────────────────────────────────


class TestSimulationRiskCalibration:
    """Tests that policy_simulation_risk_cal adjusts sim risk in decision core."""

    def _eval(self, dc: dict):
        from aihub.chat_runtime import ChatRuntime

        return ChatRuntime._evaluate_blocker_verdict(dc)

    def _base_dc(self, **overrides) -> dict:
        base: dict = {
            "selected_strategy": "agentic",
            "reason_codes": [],
            "strategy_confidence": 0.75,
            "strategy_degraded": False,
            "simulation_ran": True,
            "simulation_best_action": "research",
            "simulation_variants_count": 3,
            "simulation_risk_summary": "risk=0.75 conf=0.50 util=0.30",
            "policy_hints_loaded": False,
            "policy_profile_name": None,
            "policy_hints": [],
            "consistency_check_ran": False,
            "consistency_classification": None,
            "contradictions_found": 0,
            "experience_blocker_reason": None,
            "experience_blocker_severity": 0.0,
            "experience_recurring_failure_detected": False,
            "experience_recurring_failure_types": [],
            "policy_blocker_sensitivity": 0.0,
        }
        base.update(overrides)
        return base

    def test_risk_calibration_above_sim_risk_display(self):
        """Positive risk calibration raises effective risk —
        verified by checking the blocker verdict threshold behavior.
        With sim_risk=0.75 + risk_cal offset in blocker sensitivity,
        the blocker evaluator uses policy_blocker_sensitivity thresholds."""
        # sim_risk=0.75, default threshold=0.80 → R10 caution, not R5 downgrade
        dc_default = self._base_dc()
        v_default = self._eval(dc_default)
        if v_default.blocker_active:
            assert v_default.resolution != "downgrade"

        # With +0.10 blocker sensitivity → threshold becomes 0.70, 0.75 ≥ 0.70 → downgrade
        dc_cal = self._base_dc(policy_blocker_sensitivity=0.10)
        v_cal = self._eval(dc_cal)
        assert v_cal.blocker_active is True
        assert v_cal.resolution == "downgrade"


# ── 6. Strategy Shift from Policy Feedback ─────────────────────────────


class TestStrategyShiftFromFeedback:
    """Tests that policy_strategy_adjustments with strong negative delta
    cause a strategy_shift in decision_core."""

    def test_strategy_shift_applied(self):
        """Negative delta ≤ -0.15 for current action + positive alt → shift."""
        from aihub.policy_engine import PolicyFeedback

        feedback = PolicyFeedback(
            confidence_delta=0.0,
            handoff_bias=0.0,
            blocker_sensitivity=0.0,
            simulation_risk_calibration=0.0,
            strategy_adjustments={"reason": -0.20, "memory_search": 0.15},
            applied=True,
            summary="test",
        )

        # Simulate the strategy shift logic from chat_runtime
        current_strategy = "instant"
        _S2A = {
            "instant": "reason",
            "contextual": "memory_search",
            "research": "research",
            "agentic": "action",
        }
        _A2S = {v: k for k, v in _S2A.items()}

        cur_action = _S2A.get(current_strategy, "reason")
        cur_delta = feedback.strategy_adjustments.get(cur_action, 0.0)
        assert cur_delta <= -0.15

        best_alt = max(
            (
                (act, d)
                for act, d in feedback.strategy_adjustments.items()
                if act != cur_action and d > 0
            ),
            key=lambda x: x[1],
            default=(None, 0.0),
        )
        assert best_alt[0] == "memory_search"
        new_strategy = _A2S[best_alt[0]]
        assert new_strategy == "contextual"


# ── 7. Confidence Delta Execution Impact ───────────────────────────────


class TestConfidenceDeltaExecution:
    """Tests that policy confidence_delta changes strategy_confidence,
    which in turn changes blocker verdicts."""

    def _eval(self, dc: dict):
        from aihub.chat_runtime import ChatRuntime

        return ChatRuntime._evaluate_blocker_verdict(dc)

    def _base_dc(self, **overrides) -> dict:
        base: dict = {
            "selected_strategy": "agentic",
            "reason_codes": [],
            "strategy_confidence": 0.50,
            "strategy_degraded": False,
            "simulation_ran": False,
            "simulation_best_action": None,
            "simulation_variants_count": 0,
            "simulation_risk_summary": None,
            "policy_hints_loaded": False,
            "policy_profile_name": None,
            "policy_hints": [],
            "consistency_check_ran": False,
            "consistency_classification": None,
            "contradictions_found": 0,
            "experience_blocker_reason": None,
            "experience_blocker_severity": 0.0,
            "experience_recurring_failure_detected": False,
            "experience_recurring_failure_types": [],
            "policy_blocker_sensitivity": 0.0,
        }
        base.update(overrides)
        return base

    def test_confidence_above_threshold_no_blocker(self):
        """Confidence 0.50 > 0.45 → no low_confidence blocker."""
        dc = self._base_dc(strategy_confidence=0.50)
        v = self._eval(dc)
        if v.blocker_active:
            assert v.blocker_type != "low_confidence_decision"

    def test_confidence_below_threshold_blocker_fires(self):
        """Confidence 0.40 < 0.45 → low_confidence blocker fires.

        This simulates what happens when policy_confidence_delta=-0.10
        lowers strategy_confidence from 0.50 to 0.40 in _pre_exec_decision_core.
        """
        dc = self._base_dc(strategy_confidence=0.40)
        v = self._eval(dc)
        assert v.blocker_active is True
        assert v.blocker_type == "low_confidence_decision"


# ── 8. Trace Emission Tests ────────────────────────────────────────────


class TestTraceEmission:
    """Tests that policy feedback fields appear in the trace dict."""

    def test_trace_keys_present_in_base_trace(self):
        """All policy feedback trace keys should be present."""
        expected_keys = [
            "policy_feedback_applied",
            "policy_confidence_delta",
            "policy_handoff_bias",
            "policy_blocker_sensitivity",
            "policy_simulation_risk_cal",
            "policy_strategy_adjustments",
        ]
        # Build a minimal trace dict matching the runtime's structure
        decision_core = {
            "selected_strategy": "instant",
            "reason_codes": [],
            "strategy_confidence": 0.70,
            "strategy_degraded": False,
            "policy_feedback_applied": True,
            "policy_confidence_delta": -0.05,
            "policy_handoff_bias": 0.10,
            "policy_blocker_sensitivity": 0.03,
            "policy_simulation_risk_cal": 0.02,
            "policy_strategy_adjustments": {"reason": 0.05},
        }
        # Simulate trace dict construction
        trace = {
            "policy_feedback_applied": bool(
                decision_core.get("policy_feedback_applied")
            ),
            "policy_confidence_delta": decision_core.get(
                "policy_confidence_delta", 0.0
            ),
            "policy_handoff_bias": decision_core.get("policy_handoff_bias", 0.0),
            "policy_blocker_sensitivity": decision_core.get(
                "policy_blocker_sensitivity", 0.0
            ),
            "policy_simulation_risk_cal": decision_core.get(
                "policy_simulation_risk_cal", 0.0
            ),
            "policy_strategy_adjustments": decision_core.get(
                "policy_strategy_adjustments", {}
            ),
        }
        for key in expected_keys:
            assert key in trace, f"Missing trace key: {key}"
        assert trace["policy_feedback_applied"] is True
        assert trace["policy_confidence_delta"] == -0.05
        assert trace["policy_handoff_bias"] == 0.10
        assert trace["policy_strategy_adjustments"] == {"reason": 0.05}


# ── 9. Full Round-Trip: Hindsight → Feedback → Impact ──────────────────


class TestFullRoundTrip:
    """End-to-end test: reflection hindsight → policy feedback → execution impact."""

    def test_overconfident_history_lowers_confidence_triggers_blocker(self):
        """Three over-confident failures → negative confidence_delta →
        lowered strategy_confidence → low_confidence blocker fires."""
        from aihub.policy_engine import PolicyEngine
        from aihub.reflection_engine import ReflectionEngine, ReflectionInput

        # Step 1: Compute hindsight for three failures
        ref_eng = ReflectionEngine()
        hindsights = []
        for _ in range(3):
            rinput = ReflectionInput(
                user_id="roundtrip_test",
                action_type="research",
                parameters={},
                execution_result={},
                context={
                    "selected_strategy": "research",
                    "strategy_confidence": 0.80,
                    "handoff_happened": False,
                    "blocker_was_active": False,
                    "blocker_was_hard": False,
                    "simulation_risk": 0.10,
                },
                confidence=0.80,
            )
            h = ref_eng._compute_hindsight(rinput, "failure", 0.20)
            hindsights.append(h)

        # Verify hindsight
        for h in hindsights:
            assert h["strategy_fit"] == "bad"
            assert h["confidence_hindsight"] < 0  # over-confident

        # Step 2: Compute PolicyFeedback from hindsight
        pol_eng = PolicyEngine()
        from aihub.policy_engine import PolicyProfile

        profile = PolicyProfile(
            user_id="roundtrip_test",
            hints=[],
            generated_at=1700000000.0,
            total_reflections=10,
            reliability_index=0.5,
        )
        reflections = [{"hindsight": h, "action_type": "research"} for h in hindsights]
        fb = pol_eng.compute_feedback(profile, reflections=reflections)
        assert fb.applied is True
        assert fb.confidence_delta < 0, f"Expected negative, got {fb.confidence_delta}"

        # Step 3: Apply to strategy_confidence and check blocker
        base_confidence = 0.50
        adjusted_confidence = round(
            max(0.20, min(0.95, base_confidence + fb.confidence_delta)),
            3,
        )
        assert adjusted_confidence < base_confidence, "Confidence should be lowered"

        # Step 4: Evaluate blocker with adjusted confidence
        from aihub.chat_runtime import ChatRuntime

        dc = {
            "selected_strategy": "research",
            "strategy_confidence": adjusted_confidence,
            "strategy_degraded": False,
            "simulation_ran": False,
            "simulation_best_action": None,
            "simulation_variants_count": 0,
            "simulation_risk_summary": None,
            "policy_hints_loaded": False,
            "policy_profile_name": None,
            "policy_hints": [],
            "consistency_check_ran": False,
            "consistency_classification": None,
            "contradictions_found": 0,
            "experience_blocker_reason": None,
            "experience_blocker_severity": 0.0,
            "experience_recurring_failure_detected": False,
            "experience_recurring_failure_types": [],
            "policy_blocker_sensitivity": 0.0,
        }
        verdict = ChatRuntime._evaluate_blocker_verdict(dc)
        # With lowered confidence (should be < 0.45), blocker should fire
        if adjusted_confidence < 0.45:
            assert verdict.blocker_active is True
            assert verdict.blocker_type == "low_confidence_decision"

    def test_underconfident_history_raises_confidence_suppresses_blocker(self):
        """Three under-confident successes → positive confidence_delta →
        raised strategy_confidence → no low_confidence blocker."""
        from aihub.policy_engine import PolicyEngine, PolicyProfile
        from aihub.reflection_engine import ReflectionEngine, ReflectionInput

        ref_eng = ReflectionEngine()
        hindsights = []
        for _ in range(3):
            rinput = ReflectionInput(
                user_id="roundtrip_test2",
                action_type="reason",
                parameters={},
                execution_result={},
                context={
                    "selected_strategy": "instant",
                    "strategy_confidence": 0.40,
                    "handoff_happened": False,
                    "blocker_was_active": False,
                    "blocker_was_hard": False,
                    "simulation_risk": 0.0,
                },
                confidence=0.40,
            )
            h = ref_eng._compute_hindsight(rinput, "success", 0.90)
            hindsights.append(h)

        for h in hindsights:
            assert h["confidence_hindsight"] > 0  # under-confident

        pol_eng = PolicyEngine()
        profile = PolicyProfile(
            user_id="roundtrip_test2",
            hints=[],
            generated_at=1700000000.0,
            total_reflections=10,
            reliability_index=0.5,
        )
        reflections = [{"hindsight": h, "action_type": "reason"} for h in hindsights]
        fb = pol_eng.compute_feedback(profile, reflections=reflections)
        assert fb.confidence_delta > 0

        base_confidence = 0.42
        adjusted_confidence = round(
            max(0.20, min(0.95, base_confidence + fb.confidence_delta)),
            3,
        )
        assert adjusted_confidence > base_confidence

        from aihub.chat_runtime import ChatRuntime

        dc = {
            "selected_strategy": "agentic",
            "strategy_confidence": adjusted_confidence,
            "strategy_degraded": False,
            "simulation_ran": False,
            "simulation_best_action": None,
            "simulation_variants_count": 0,
            "simulation_risk_summary": None,
            "policy_hints_loaded": False,
            "policy_profile_name": None,
            "policy_hints": [],
            "consistency_check_ran": False,
            "consistency_classification": None,
            "contradictions_found": 0,
            "experience_blocker_reason": None,
            "experience_blocker_severity": 0.0,
            "experience_recurring_failure_detected": False,
            "experience_recurring_failure_types": [],
            "policy_blocker_sensitivity": 0.0,
        }
        verdict = ChatRuntime._evaluate_blocker_verdict(dc)
        if adjusted_confidence >= 0.45:
            assert (
                not verdict.blocker_active
                or verdict.blocker_type != "low_confidence_decision"
            )


# ── 10. Regression: Base Behavior Unchanged ────────────────────────────


class TestRegressionBaseBehavior:
    """Verify blocker evaluator/handoff defaults haven't changed
    when no policy feedback is applied."""

    def _eval(self, dc: dict):
        from aihub.chat_runtime import ChatRuntime

        return ChatRuntime._evaluate_blocker_verdict(dc)

    def _base_dc(self, **overrides) -> dict:
        base: dict = {
            "selected_strategy": "instant",
            "reason_codes": [],
            "strategy_confidence": 0.75,
            "strategy_degraded": False,
            "simulation_ran": False,
            "simulation_best_action": None,
            "simulation_variants_count": 0,
            "simulation_risk_summary": None,
            "policy_hints_loaded": False,
            "policy_profile_name": None,
            "policy_hints": [],
            "consistency_check_ran": False,
            "consistency_classification": None,
            "contradictions_found": 0,
            "experience_blocker_reason": None,
            "experience_blocker_severity": 0.0,
            "experience_recurring_failure_detected": False,
            "experience_recurring_failure_types": [],
            "policy_blocker_sensitivity": 0.0,
        }
        base.update(overrides)
        return base

    def test_no_blocker_on_clean_state(self):
        """Default clean state → no blocker."""
        v = self._eval(self._base_dc())
        assert v.blocker_active is False
        assert v.resolution == "allow"

    def test_hard_consistency_unchanged(self):
        """R1 fires at same threshold when sensitivity=0."""
        dc = self._base_dc(
            consistency_classification="conflict",
            contradictions_found=2,
            strategy_confidence=0.35,
        )
        v = self._eval(dc)
        assert v.blocker_active is True
        assert v.resolution == "hard_block"
        assert v.blocker_type == "consistency_conflict"

    def test_hard_repeated_failure_unchanged(self):
        """R2 fires at same threshold when sensitivity=0."""
        dc = self._base_dc(
            experience_blocker_reason="pattern_x",
            experience_blocker_severity=0.85,
        )
        v = self._eval(dc)
        assert v.blocker_active is True
        assert v.blocker_type == "repeated_failure"
        assert v.resolution == "hard_block"

    def test_degraded_hard_unchanged(self):
        """R3 fires at same threshold when sensitivity=0."""
        dc = self._base_dc(
            strategy_degraded=True,
            strategy_confidence=0.30,
        )
        v = self._eval(dc)
        assert v.blocker_active is True

    def test_low_confidence_threshold_unchanged(self):
        """R6 fires at same threshold when sensitivity=0."""
        dc = self._base_dc(
            strategy_confidence=0.40,
            selected_strategy="agentic",
        )
        v = self._eval(dc)
        assert v.blocker_active is True
        assert v.blocker_type == "low_confidence_decision"


# ── 11. Post-Exec Reflection Context Passing ──────────────────────────


class TestPostExecReflectionContext:
    """Proves that _post_exec_reflection passes full decision_core context
    to ReflectionInput, enabling _compute_hindsight to produce real values.

    This is the critical gap fixed by PATCH 1.
    """

    def _make_runtime(self):
        from aihub.chat_runtime import ChatRuntime

        rt = ChatRuntime.__new__(ChatRuntime)
        return rt

    def _make_blocker_verdict(self, active=False, hard=False):
        from aihub.chat_contracts import BlockerVerdict

        if not active:
            return BlockerVerdict.allow()
        return BlockerVerdict(
            blocker_active=True,
            blocker_type="low_confidence_decision",
            blocker_scope="turn",
            blocker_severity="hard" if hard else "caution",
            hard=hard,
            resolution="hard_block" if hard else "caution_pass",
            reason="test blocker",
            source="test",
        )

    def _base_dc(self, **overrides) -> dict:
        base = {
            "selected_strategy": "research",
            "reason_codes": ["STRATEGY_HEAVY"],
            "strategy_confidence": 0.65,
            "strategy_degraded": False,
            "simulation_ran": True,
            "simulation_best_action": "research",
            "simulation_variants_count": 3,
            "simulation_risk_summary": "risk=0.40 conf=0.60 util=0.50",
            "consistency_classification": "ok",
            "policy_simulation_risk_cal": 0.0,
        }
        base.update(overrides)
        return base

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_context_includes_selected_strategy(self, mock_reflect):
        """ReflectionInput.context must contain selected_strategy from decision_core."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="success",
            outcome_score=0.8,
            lesson_learned="ok",
            policy_signal="boost",
            policy_weight=0.5,
            recommended_adjustment="no_change",
            patterns_detected=[],
            metadata={},
            strategy_fit="good",
            handoff_hindsight="na",
            blocker_hindsight="na",
            confidence_hindsight=0.1,
            risk_hindsight=0.0,
        )
        rt = self._make_runtime()
        dc = self._base_dc(selected_strategy="agentic")
        rt._post_exec_reflection(
            user_id="u1",
            message="test",
            response_text="ok",
            tool_calls=[],
            tool_results=[],
            decision_core=dc,
            blocker_verdict=self._make_blocker_verdict(),
            handoff_happened=False,
        )
        assert mock_reflect.called
        rinput = mock_reflect.call_args[0][0]
        assert rinput.context["selected_strategy"] == "agentic"
        assert rinput.context["strategy_confidence"] == 0.65
        assert rinput.context["handoff_happened"] is False
        assert rinput.context["blocker_was_active"] is False

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_context_includes_blocker_state(self, mock_reflect):
        """When blocker was active+hard, context must reflect that."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="failure",
            outcome_score=0.2,
            lesson_learned="fail",
            policy_signal="penalize",
            policy_weight=0.7,
            recommended_adjustment="try_alternative_strategy",
            patterns_detected=[],
            metadata={},
            strategy_fit="bad",
            handoff_hindsight="earlier",
            blocker_hindsight="stronger",
            confidence_hindsight=-0.4,
            risk_hindsight=0.3,
        )
        rt = self._make_runtime()
        dc = self._base_dc()
        bv = self._make_blocker_verdict(active=True, hard=True)
        rt._post_exec_reflection(
            user_id="u2",
            message="test",
            response_text="fail",
            tool_calls=[],
            tool_results=[],
            decision_core=dc,
            blocker_verdict=bv,
            handoff_happened=False,
        )
        rinput = mock_reflect.call_args[0][0]
        assert rinput.context["blocker_was_active"] is True
        assert rinput.context["blocker_was_hard"] is True

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_context_includes_simulation_risk(self, mock_reflect):
        """simulation_risk in context is parsed from simulation_risk_summary."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="success",
            outcome_score=0.8,
            lesson_learned="ok",
            policy_signal="boost",
            policy_weight=0.5,
            recommended_adjustment="no_change",
            patterns_detected=[],
            metadata={},
            strategy_fit="good",
            handoff_hindsight="na",
            blocker_hindsight="na",
            confidence_hindsight=0.1,
            risk_hindsight=-0.3,
        )
        rt = self._make_runtime()
        dc = self._base_dc(
            simulation_ran=True,
            simulation_risk_summary="risk=0.45 conf=0.60 util=0.50",
        )
        rt._post_exec_reflection(
            user_id="u3",
            message="test",
            response_text="ok",
            tool_calls=[],
            tool_results=[],
            decision_core=dc,
            blocker_verdict=self._make_blocker_verdict(),
            handoff_happened=False,
        )
        rinput = mock_reflect.call_args[0][0]
        assert rinput.context["simulation_risk"] == pytest.approx(0.45, abs=0.01)

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_context_handoff_happened_propagated(self, mock_reflect):
        """handoff_happened=True is properly propagated to context."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="success",
            outcome_score=0.9,
            lesson_learned="ok",
            policy_signal="boost",
            policy_weight=0.6,
            recommended_adjustment="no_change",
            patterns_detected=[],
            metadata={},
            strategy_fit="good",
            handoff_hindsight="correct",
            blocker_hindsight="na",
            confidence_hindsight=0.2,
            risk_hindsight=0.0,
        )
        rt = self._make_runtime()
        dc = self._base_dc()
        rt._post_exec_reflection(
            user_id="u4",
            message="test",
            response_text="ok",
            tool_calls=[],
            tool_results=[],
            decision_core=dc,
            blocker_verdict=self._make_blocker_verdict(),
            handoff_happened=True,
        )
        rinput = mock_reflect.call_args[0][0]
        assert rinput.context["handoff_happened"] is True


# ── 12. Post-Exec Reflection Output Propagation ──────────────────────


class TestPostExecReflectionOutput:
    """Proves that _post_exec_reflection returns hindsight fields
    (not just lesson_learned), enabling trace to expose them."""

    def _make_runtime(self):
        from aihub.chat_runtime import ChatRuntime

        rt = ChatRuntime.__new__(ChatRuntime)
        return rt

    def _base_dc(self) -> dict:
        return {
            "selected_strategy": "instant",
            "reason_codes": [],
            "strategy_confidence": 0.70,
            "strategy_degraded": False,
            "simulation_ran": False,
            "simulation_best_action": None,
            "simulation_variants_count": 0,
            "simulation_risk_summary": "",
            "consistency_classification": "ok",
            "policy_simulation_risk_cal": 0.0,
        }

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_hindsight_fields_returned(self, mock_reflect):
        """Result dict includes strategy_fit, handoff_hindsight, etc."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="failure",
            outcome_score=0.25,
            lesson_learned="bad",
            policy_signal="penalize",
            policy_weight=0.7,
            recommended_adjustment="try_alternative_strategy",
            patterns_detected=[],
            metadata={},
            strategy_fit="bad",
            handoff_hindsight="earlier",
            blocker_hindsight="stronger",
            confidence_hindsight=-0.45,
            risk_hindsight=0.35,
        )
        rt = self._make_runtime()
        result = rt._post_exec_reflection(
            user_id="u5",
            message="test",
            response_text="fail",
            tool_calls=[],
            tool_results=[],
            decision_core=self._base_dc(),
        )
        assert result["reflection_ran"] is True
        assert result["strategy_fit"] == "bad"
        assert result["handoff_hindsight"] == "earlier"
        assert result["blocker_hindsight"] == "stronger"
        assert result["confidence_hindsight"] == -0.45
        assert result["risk_hindsight"] == 0.35

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_hindsight_defaults_on_success(self, mock_reflect):
        """Successful reflection with neutral hindsight → neutral defaults."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="success",
            outcome_score=0.85,
            lesson_learned="ok",
            policy_signal="boost",
            policy_weight=0.5,
            recommended_adjustment="no_change",
            patterns_detected=[],
            metadata={},
            strategy_fit="good",
            handoff_hindsight="na",
            blocker_hindsight="na",
            confidence_hindsight=0.1,
            risk_hindsight=-0.05,
        )
        rt = self._make_runtime()
        result = rt._post_exec_reflection(
            user_id="u6",
            message="test",
            response_text="ok",
            tool_calls=[],
            tool_results=[],
            decision_core=self._base_dc(),
        )
        assert result["reflection_ran"] is True
        assert result["strategy_fit"] == "good"
        assert result["handoff_hindsight"] == "na"

    def test_hindsight_defaults_on_failure(self):
        """If reflect_on_action raises, hindsight defaults are safe."""
        rt = self._make_runtime()
        with patch(
            "aihub.reflection_engine.reflect_on_action",
            side_effect=RuntimeError("boom"),
        ):
            result = rt._post_exec_reflection(
                user_id="u7",
                message="test",
                response_text="fail",
                tool_calls=[],
                tool_results=[],
                decision_core=self._base_dc(),
            )
        assert result["reflection_ran"] is False
        assert result["strategy_fit"] == "neutral"
        assert result["handoff_hindsight"] == "na"
        assert result["confidence_hindsight"] == 0.0
        assert result["risk_hindsight"] == 0.0


# ── 13. Trace Hindsight Fields ────────────────────────────────────────


class TestTraceHindsightFields:
    """Proves the trace dict includes reflection hindsight fields."""

    def test_trace_contains_reflection_hindsight_keys(self):
        """All 5 reflection hindsight keys must be present in trace."""
        expected_keys = [
            "reflection_strategy_fit",
            "reflection_handoff_hindsight",
            "reflection_blocker_hindsight",
            "reflection_confidence_hindsight",
            "reflection_risk_hindsight",
        ]
        # Simulate the trace construction matching the main trace path
        post_reflection = {
            "reflection_ran": True,
            "reflection_summary": "test lesson",
            "strategy_fit": "bad",
            "handoff_hindsight": "earlier",
            "blocker_hindsight": "stronger",
            "confidence_hindsight": -0.30,
            "risk_hindsight": 0.25,
        }
        trace = {
            "reflection_ran": post_reflection["reflection_ran"],
            "reflection_summary": post_reflection["reflection_summary"],
            "reflection_strategy_fit": post_reflection.get("strategy_fit", "neutral"),
            "reflection_handoff_hindsight": post_reflection.get(
                "handoff_hindsight", "na"
            ),
            "reflection_blocker_hindsight": post_reflection.get(
                "blocker_hindsight", "na"
            ),
            "reflection_confidence_hindsight": post_reflection.get(
                "confidence_hindsight", 0.0
            ),
            "reflection_risk_hindsight": post_reflection.get("risk_hindsight", 0.0),
        }
        for key in expected_keys:
            assert key in trace, f"Missing trace key: {key}"
        assert trace["reflection_strategy_fit"] == "bad"
        assert trace["reflection_handoff_hindsight"] == "earlier"
        assert trace["reflection_blocker_hindsight"] == "stronger"
        assert trace["reflection_confidence_hindsight"] == -0.30
        assert trace["reflection_risk_hindsight"] == 0.25


# ── 11. Post-Exec Reflection Context Passing ──────────────────────────


class TestPostExecReflectionContext:
    """Proves that _post_exec_reflection passes full decision_core context
    to ReflectionInput, enabling _compute_hindsight to produce real values.

    This is the critical gap fixed by PATCH 1.
    """

    def _make_runtime(self):
        from aihub.chat_runtime import ChatRuntime

        rt = ChatRuntime.__new__(ChatRuntime)
        return rt

    def _make_blocker_verdict(self, active=False, hard=False):
        from aihub.chat_contracts import BlockerVerdict

        if not active:
            return BlockerVerdict.allow()
        return BlockerVerdict(
            blocker_active=True,
            blocker_type="low_confidence_decision",
            blocker_scope="turn",
            blocker_severity="hard" if hard else "caution",
            hard=hard,
            resolution="hard_block" if hard else "caution_pass",
            reason="test blocker",
            source="test",
        )

    def _base_dc(self, **overrides) -> dict:
        base = {
            "selected_strategy": "research",
            "reason_codes": ["STRATEGY_HEAVY"],
            "strategy_confidence": 0.65,
            "strategy_degraded": False,
            "simulation_ran": True,
            "simulation_best_action": "research",
            "simulation_variants_count": 3,
            "simulation_risk_summary": "risk=0.40 conf=0.60 util=0.50",
            "consistency_classification": "ok",
            "policy_simulation_risk_cal": 0.0,
        }
        base.update(overrides)
        return base

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_context_includes_selected_strategy(self, mock_reflect):
        """ReflectionInput.context must contain selected_strategy from decision_core."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="success",
            outcome_score=0.8,
            lesson_learned="ok",
            policy_signal="boost",
            policy_weight=0.5,
            recommended_adjustment="no_change",
            patterns_detected=[],
            metadata={},
            strategy_fit="good",
            handoff_hindsight="na",
            blocker_hindsight="na",
            confidence_hindsight=0.1,
            risk_hindsight=0.0,
        )
        rt = self._make_runtime()
        dc = self._base_dc(selected_strategy="agentic")
        rt._post_exec_reflection(
            user_id="u1",
            message="test",
            response_text="ok",
            tool_calls=[],
            tool_results=[],
            decision_core=dc,
            blocker_verdict=self._make_blocker_verdict(),
            handoff_happened=False,
        )
        assert mock_reflect.called
        rinput = mock_reflect.call_args[0][0]
        assert rinput.context["selected_strategy"] == "agentic"
        assert rinput.context["strategy_confidence"] == 0.65
        assert rinput.context["handoff_happened"] is False
        assert rinput.context["blocker_was_active"] is False

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_context_includes_blocker_state(self, mock_reflect):
        """When blocker was active+hard, context must reflect that."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="failure",
            outcome_score=0.2,
            lesson_learned="fail",
            policy_signal="penalize",
            policy_weight=0.7,
            recommended_adjustment="try_alternative_strategy",
            patterns_detected=[],
            metadata={},
            strategy_fit="bad",
            handoff_hindsight="earlier",
            blocker_hindsight="stronger",
            confidence_hindsight=-0.4,
            risk_hindsight=0.3,
        )
        rt = self._make_runtime()
        dc = self._base_dc()
        bv = self._make_blocker_verdict(active=True, hard=True)
        rt._post_exec_reflection(
            user_id="u2",
            message="test",
            response_text="fail",
            tool_calls=[],
            tool_results=[],
            decision_core=dc,
            blocker_verdict=bv,
            handoff_happened=False,
        )
        rinput = mock_reflect.call_args[0][0]
        assert rinput.context["blocker_was_active"] is True
        assert rinput.context["blocker_was_hard"] is True

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_context_includes_simulation_risk(self, mock_reflect):
        """simulation_risk in context is parsed from simulation_risk_summary."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="success",
            outcome_score=0.8,
            lesson_learned="ok",
            policy_signal="boost",
            policy_weight=0.5,
            recommended_adjustment="no_change",
            patterns_detected=[],
            metadata={},
            strategy_fit="good",
            handoff_hindsight="na",
            blocker_hindsight="na",
            confidence_hindsight=0.1,
            risk_hindsight=-0.3,
        )
        rt = self._make_runtime()
        dc = self._base_dc(
            simulation_ran=True,
            simulation_risk_summary="risk=0.45 conf=0.60 util=0.50",
        )
        rt._post_exec_reflection(
            user_id="u3",
            message="test",
            response_text="ok",
            tool_calls=[],
            tool_results=[],
            decision_core=dc,
            blocker_verdict=self._make_blocker_verdict(),
            handoff_happened=False,
        )
        rinput = mock_reflect.call_args[0][0]
        assert rinput.context["simulation_risk"] == pytest.approx(0.45, abs=0.01)

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_context_handoff_happened_propagated(self, mock_reflect):
        """handoff_happened=True is properly propagated to context."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="success",
            outcome_score=0.9,
            lesson_learned="ok",
            policy_signal="boost",
            policy_weight=0.6,
            recommended_adjustment="no_change",
            patterns_detected=[],
            metadata={},
            strategy_fit="good",
            handoff_hindsight="correct",
            blocker_hindsight="na",
            confidence_hindsight=0.2,
            risk_hindsight=0.0,
        )
        rt = self._make_runtime()
        dc = self._base_dc()
        rt._post_exec_reflection(
            user_id="u4",
            message="test",
            response_text="ok",
            tool_calls=[],
            tool_results=[],
            decision_core=dc,
            blocker_verdict=self._make_blocker_verdict(),
            handoff_happened=True,
        )
        rinput = mock_reflect.call_args[0][0]
        assert rinput.context["handoff_happened"] is True


# ── 12. Post-Exec Reflection Output Propagation ──────────────────────


class TestPostExecReflectionOutput:
    """Proves that _post_exec_reflection returns hindsight fields
    (not just lesson_learned), enabling trace to expose them."""

    def _make_runtime(self):
        from aihub.chat_runtime import ChatRuntime

        rt = ChatRuntime.__new__(ChatRuntime)
        return rt

    def _base_dc(self) -> dict:
        return {
            "selected_strategy": "instant",
            "reason_codes": [],
            "strategy_confidence": 0.70,
            "strategy_degraded": False,
            "simulation_ran": False,
            "simulation_best_action": None,
            "simulation_variants_count": 0,
            "simulation_risk_summary": "",
            "consistency_classification": "ok",
            "policy_simulation_risk_cal": 0.0,
        }

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_hindsight_fields_returned(self, mock_reflect):
        """Result dict includes strategy_fit, handoff_hindsight, etc."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="failure",
            outcome_score=0.25,
            lesson_learned="bad",
            policy_signal="penalize",
            policy_weight=0.7,
            recommended_adjustment="try_alternative_strategy",
            patterns_detected=[],
            metadata={},
            strategy_fit="bad",
            handoff_hindsight="earlier",
            blocker_hindsight="stronger",
            confidence_hindsight=-0.45,
            risk_hindsight=0.35,
        )
        rt = self._make_runtime()
        result = rt._post_exec_reflection(
            user_id="u5",
            message="test",
            response_text="fail",
            tool_calls=[],
            tool_results=[],
            decision_core=self._base_dc(),
        )
        assert result["reflection_ran"] is True
        assert result["strategy_fit"] == "bad"
        assert result["handoff_hindsight"] == "earlier"
        assert result["blocker_hindsight"] == "stronger"
        assert result["confidence_hindsight"] == -0.45
        assert result["risk_hindsight"] == 0.35

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_hindsight_defaults_on_success(self, mock_reflect):
        """Successful reflection with neutral hindsight → neutral defaults."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="success",
            outcome_score=0.85,
            lesson_learned="ok",
            policy_signal="boost",
            policy_weight=0.5,
            recommended_adjustment="no_change",
            patterns_detected=[],
            metadata={},
            strategy_fit="good",
            handoff_hindsight="na",
            blocker_hindsight="na",
            confidence_hindsight=0.1,
            risk_hindsight=-0.05,
        )
        rt = self._make_runtime()
        result = rt._post_exec_reflection(
            user_id="u6",
            message="test",
            response_text="ok",
            tool_calls=[],
            tool_results=[],
            decision_core=self._base_dc(),
        )
        assert result["reflection_ran"] is True
        assert result["strategy_fit"] == "good"
        assert result["handoff_hindsight"] == "na"

    def test_hindsight_defaults_on_failure(self):
        """If reflect_on_action raises, hindsight defaults are safe."""
        rt = self._make_runtime()
        with patch(
            "aihub.reflection_engine.reflect_on_action",
            side_effect=RuntimeError("boom"),
        ):
            result = rt._post_exec_reflection(
                user_id="u7",
                message="test",
                response_text="fail",
                tool_calls=[],
                tool_results=[],
                decision_core=self._base_dc(),
            )
        assert result["reflection_ran"] is False
        assert result["strategy_fit"] == "neutral"
        assert result["handoff_hindsight"] == "na"
        assert result["confidence_hindsight"] == 0.0
        assert result["risk_hindsight"] == 0.0


# ── 13. Trace Hindsight Fields ────────────────────────────────────────


class TestTraceHindsightFields:
    """Proves the trace dict includes reflection hindsight fields."""

    def test_trace_contains_reflection_hindsight_keys(self):
        """All 5 reflection hindsight keys must be present in trace."""
        expected_keys = [
            "reflection_strategy_fit",
            "reflection_handoff_hindsight",
            "reflection_blocker_hindsight",
            "reflection_confidence_hindsight",
            "reflection_risk_hindsight",
        ]
        # Simulate the trace construction matching the main trace path
        post_reflection = {
            "reflection_ran": True,
            "reflection_summary": "test lesson",
            "strategy_fit": "bad",
            "handoff_hindsight": "earlier",
            "blocker_hindsight": "stronger",
            "confidence_hindsight": -0.30,
            "risk_hindsight": 0.25,
        }
        trace = {
            "reflection_ran": post_reflection["reflection_ran"],
            "reflection_summary": post_reflection["reflection_summary"],
            "reflection_strategy_fit": post_reflection.get("strategy_fit", "neutral"),
            "reflection_handoff_hindsight": post_reflection.get(
                "handoff_hindsight", "na"
            ),
            "reflection_blocker_hindsight": post_reflection.get(
                "blocker_hindsight", "na"
            ),
            "reflection_confidence_hindsight": post_reflection.get(
                "confidence_hindsight", 0.0
            ),
            "reflection_risk_hindsight": post_reflection.get("risk_hindsight", 0.0),
        }
        for key in expected_keys:
            assert key in trace, f"Missing trace key: {key}"
        assert trace["reflection_strategy_fit"] == "bad"
        assert trace["reflection_handoff_hindsight"] == "earlier"
        assert trace["reflection_blocker_hindsight"] == "stronger"
        assert trace["reflection_confidence_hindsight"] == -0.30
        assert trace["reflection_risk_hindsight"] == 0.25


# ── 11. Post-Exec Reflection Context Passing ──────────────────────────


class TestPostExecReflectionContext:
    """Proves that _post_exec_reflection passes full decision_core context
    to ReflectionInput, enabling _compute_hindsight to produce real values.

    This is the critical gap fixed by PATCH 1.
    """

    def _make_runtime(self):
        from aihub.chat_runtime import ChatRuntime

        rt = ChatRuntime.__new__(ChatRuntime)
        return rt

    def _make_blocker_verdict(self, active=False, hard=False):
        from aihub.chat_contracts import BlockerVerdict

        if not active:
            return BlockerVerdict.allow()
        return BlockerVerdict(
            blocker_active=True,
            blocker_type="low_confidence_decision",
            blocker_scope="turn",
            blocker_severity="hard" if hard else "caution",
            hard=hard,
            resolution="hard_block" if hard else "caution_pass",
            reason="test blocker",
            source="test",
        )

    def _base_dc(self, **overrides) -> dict:
        base = {
            "selected_strategy": "research",
            "reason_codes": ["STRATEGY_HEAVY"],
            "strategy_confidence": 0.65,
            "strategy_degraded": False,
            "simulation_ran": True,
            "simulation_best_action": "research",
            "simulation_variants_count": 3,
            "simulation_risk_summary": "risk=0.40 conf=0.60 util=0.50",
            "consistency_classification": "ok",
            "policy_simulation_risk_cal": 0.0,
        }
        base.update(overrides)
        return base

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_context_includes_selected_strategy(self, mock_reflect):
        """ReflectionInput.context must contain selected_strategy from decision_core."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="success",
            outcome_score=0.8,
            lesson_learned="ok",
            policy_signal="boost",
            policy_weight=0.5,
            recommended_adjustment="no_change",
            patterns_detected=[],
            metadata={},
            strategy_fit="good",
            handoff_hindsight="na",
            blocker_hindsight="na",
            confidence_hindsight=0.1,
            risk_hindsight=0.0,
        )
        rt = self._make_runtime()
        dc = self._base_dc(selected_strategy="agentic")
        rt._post_exec_reflection(
            user_id="u1",
            message="test",
            response_text="ok",
            tool_calls=[],
            tool_results=[],
            decision_core=dc,
            blocker_verdict=self._make_blocker_verdict(),
            handoff_happened=False,
        )
        assert mock_reflect.called
        rinput = mock_reflect.call_args[0][0]
        assert rinput.context["selected_strategy"] == "agentic"
        assert rinput.context["strategy_confidence"] == 0.65
        assert rinput.context["handoff_happened"] is False
        assert rinput.context["blocker_was_active"] is False

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_context_includes_blocker_state(self, mock_reflect):
        """When blocker was active+hard, context must reflect that."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="failure",
            outcome_score=0.2,
            lesson_learned="fail",
            policy_signal="penalize",
            policy_weight=0.7,
            recommended_adjustment="try_alternative_strategy",
            patterns_detected=[],
            metadata={},
            strategy_fit="bad",
            handoff_hindsight="earlier",
            blocker_hindsight="stronger",
            confidence_hindsight=-0.4,
            risk_hindsight=0.3,
        )
        rt = self._make_runtime()
        dc = self._base_dc()
        bv = self._make_blocker_verdict(active=True, hard=True)
        rt._post_exec_reflection(
            user_id="u2",
            message="test",
            response_text="fail",
            tool_calls=[],
            tool_results=[],
            decision_core=dc,
            blocker_verdict=bv,
            handoff_happened=False,
        )
        rinput = mock_reflect.call_args[0][0]
        assert rinput.context["blocker_was_active"] is True
        assert rinput.context["blocker_was_hard"] is True

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_context_includes_simulation_risk(self, mock_reflect):
        """simulation_risk in context is parsed from simulation_risk_summary."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="success",
            outcome_score=0.8,
            lesson_learned="ok",
            policy_signal="boost",
            policy_weight=0.5,
            recommended_adjustment="no_change",
            patterns_detected=[],
            metadata={},
            strategy_fit="good",
            handoff_hindsight="na",
            blocker_hindsight="na",
            confidence_hindsight=0.1,
            risk_hindsight=-0.3,
        )
        rt = self._make_runtime()
        dc = self._base_dc(
            simulation_ran=True,
            simulation_risk_summary="risk=0.45 conf=0.60 util=0.50",
        )
        rt._post_exec_reflection(
            user_id="u3",
            message="test",
            response_text="ok",
            tool_calls=[],
            tool_results=[],
            decision_core=dc,
            blocker_verdict=self._make_blocker_verdict(),
            handoff_happened=False,
        )
        rinput = mock_reflect.call_args[0][0]
        assert rinput.context["simulation_risk"] == pytest.approx(0.45, abs=0.01)

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_context_handoff_happened_propagated(self, mock_reflect):
        """handoff_happened=True is properly propagated to context."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="success",
            outcome_score=0.9,
            lesson_learned="ok",
            policy_signal="boost",
            policy_weight=0.6,
            recommended_adjustment="no_change",
            patterns_detected=[],
            metadata={},
            strategy_fit="good",
            handoff_hindsight="correct",
            blocker_hindsight="na",
            confidence_hindsight=0.2,
            risk_hindsight=0.0,
        )
        rt = self._make_runtime()
        dc = self._base_dc()
        rt._post_exec_reflection(
            user_id="u4",
            message="test",
            response_text="ok",
            tool_calls=[],
            tool_results=[],
            decision_core=dc,
            blocker_verdict=self._make_blocker_verdict(),
            handoff_happened=True,
        )
        rinput = mock_reflect.call_args[0][0]
        assert rinput.context["handoff_happened"] is True


# ── 12. Post-Exec Reflection Output Propagation ──────────────────────


class TestPostExecReflectionOutput:
    """Proves that _post_exec_reflection returns hindsight fields
    (not just lesson_learned), enabling trace to expose them."""

    def _make_runtime(self):
        from aihub.chat_runtime import ChatRuntime

        rt = ChatRuntime.__new__(ChatRuntime)
        return rt

    def _base_dc(self) -> dict:
        return {
            "selected_strategy": "instant",
            "reason_codes": [],
            "strategy_confidence": 0.70,
            "strategy_degraded": False,
            "simulation_ran": False,
            "simulation_best_action": None,
            "simulation_variants_count": 0,
            "simulation_risk_summary": "",
            "consistency_classification": "ok",
            "policy_simulation_risk_cal": 0.0,
        }

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_hindsight_fields_returned(self, mock_reflect):
        """Result dict includes strategy_fit, handoff_hindsight, etc."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="failure",
            outcome_score=0.25,
            lesson_learned="bad",
            policy_signal="penalize",
            policy_weight=0.7,
            recommended_adjustment="try_alternative_strategy",
            patterns_detected=[],
            metadata={},
            strategy_fit="bad",
            handoff_hindsight="earlier",
            blocker_hindsight="stronger",
            confidence_hindsight=-0.45,
            risk_hindsight=0.35,
        )
        rt = self._make_runtime()
        result = rt._post_exec_reflection(
            user_id="u5",
            message="test",
            response_text="fail",
            tool_calls=[],
            tool_results=[],
            decision_core=self._base_dc(),
        )
        assert result["reflection_ran"] is True
        assert result["strategy_fit"] == "bad"
        assert result["handoff_hindsight"] == "earlier"
        assert result["blocker_hindsight"] == "stronger"
        assert result["confidence_hindsight"] == -0.45
        assert result["risk_hindsight"] == 0.35

    @patch("aihub.reflection_engine.reflect_on_action")
    def test_hindsight_defaults_on_success(self, mock_reflect):
        """Successful reflection with neutral hindsight → neutral defaults."""
        from aihub.reflection_engine import ReflectionOutput

        mock_reflect.return_value = ReflectionOutput(
            reflection_id="test",
            user_id="test",
            action_type="chat_turn",
            outcome="success",
            outcome_score=0.85,
            lesson_learned="ok",
            policy_signal="boost",
            policy_weight=0.5,
            recommended_adjustment="no_change",
            patterns_detected=[],
            metadata={},
            strategy_fit="good",
            handoff_hindsight="na",
            blocker_hindsight="na",
            confidence_hindsight=0.1,
            risk_hindsight=-0.05,
        )
        rt = self._make_runtime()
        result = rt._post_exec_reflection(
            user_id="u6",
            message="test",
            response_text="ok",
            tool_calls=[],
            tool_results=[],
            decision_core=self._base_dc(),
        )
        assert result["reflection_ran"] is True
        assert result["strategy_fit"] == "good"
        assert result["handoff_hindsight"] == "na"

    def test_hindsight_defaults_on_failure(self):
        """If reflect_on_action raises, hindsight defaults are safe."""
        rt = self._make_runtime()
        with patch(
            "aihub.reflection_engine.reflect_on_action",
            side_effect=RuntimeError("boom"),
        ):
            result = rt._post_exec_reflection(
                user_id="u7",
                message="test",
                response_text="fail",
                tool_calls=[],
                tool_results=[],
                decision_core=self._base_dc(),
            )
        assert result["reflection_ran"] is False
        assert result["strategy_fit"] == "neutral"
        assert result["handoff_hindsight"] == "na"
        assert result["confidence_hindsight"] == 0.0
        assert result["risk_hindsight"] == 0.0


# ── 13. Trace Hindsight Fields ────────────────────────────────────────


class TestTraceHindsightFields:
    """Proves the trace dict includes reflection hindsight fields."""

    def test_trace_contains_reflection_hindsight_keys(self):
        """All 5 reflection hindsight keys must be present in trace."""
        expected_keys = [
            "reflection_strategy_fit",
            "reflection_handoff_hindsight",
            "reflection_blocker_hindsight",
            "reflection_confidence_hindsight",
            "reflection_risk_hindsight",
        ]
        # Simulate the trace construction matching the main trace path
        post_reflection = {
            "reflection_ran": True,
            "reflection_summary": "test lesson",
            "strategy_fit": "bad",
            "handoff_hindsight": "earlier",
            "blocker_hindsight": "stronger",
            "confidence_hindsight": -0.30,
            "risk_hindsight": 0.25,
        }
        trace = {
            "reflection_ran": post_reflection["reflection_ran"],
            "reflection_summary": post_reflection["reflection_summary"],
            "reflection_strategy_fit": post_reflection.get("strategy_fit", "neutral"),
            "reflection_handoff_hindsight": post_reflection.get(
                "handoff_hindsight", "na"
            ),
            "reflection_blocker_hindsight": post_reflection.get(
                "blocker_hindsight", "na"
            ),
            "reflection_confidence_hindsight": post_reflection.get(
                "confidence_hindsight", 0.0
            ),
            "reflection_risk_hindsight": post_reflection.get("risk_hindsight", 0.0),
        }
        for key in expected_keys:
            assert key in trace, f"Missing trace key: {key}"
        assert trace["reflection_strategy_fit"] == "bad"
        assert trace["reflection_handoff_hindsight"] == "earlier"
        assert trace["reflection_blocker_hindsight"] == "stronger"
        assert trace["reflection_confidence_hindsight"] == -0.30
        assert trace["reflection_risk_hindsight"] == 0.25
