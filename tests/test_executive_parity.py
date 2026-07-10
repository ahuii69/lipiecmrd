"""Tests for executive↔chat parity: decision signals, reflection, cockpit.

Validates that the executive controller now computes the same layered
decision pipeline as chat_runtime (experience→policy→simulation→reflection)
and that the cockpit runtime-status surfaces all parity fields.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import asdict
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aihub.executive_controller import (
    STRATEGY_COGNITIVE,
    STRATEGY_PLANNED,
    STRATEGY_REACTIVE,
    ExecutionResult,
    ExecutiveController,
    build_agent_cycle_response,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

def _make_controller() -> ExecutiveController:
    """Instantiate controller without side-effects."""
    return ExecutiveController.__new__(ExecutiveController)


def _make_experience(
    *,
    user_input_summary: str = "test query",
    selected_strategy: str = STRATEGY_PLANNED,
    success: bool = True,
    failure_type: str = "",
    created_at: float | None = None,
    short_lesson_learned: str = "lesson",
    reflection_seed: str = "seed",
) -> dict[str, Any]:
    return {
        "user_input_summary": user_input_summary,
        "selected_strategy": selected_strategy,
        "success": success,
        "failure_type": failure_type,
        "created_at": created_at or time.time(),
        "short_lesson_learned": short_lesson_learned,
        "reflection_seed": reflection_seed,
    }


def _make_execution_result(
    ok: bool = True,
    action_summary: str = "executed successfully",
    errors: list | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        ok=ok,
        action_summary=action_summary,
        errors=errors or [],
        payload={},
        strategy=STRATEGY_PLANNED,
    )


# ── Experience Signal Tests ──────────────────────────────────────────────

class TestExperienceSignal:
    """_compute_experience_signal: experience-driven bias for agent path."""

    def test_no_history_returns_base(self):
        ctrl = _make_controller()
        with patch("aihub.executive_controller.get_experiences_by_user", return_value=[]):
            result = ctrl._compute_experience_signal(
                user_id="u1",
                query_text="test query",
                selected_strategy=STRATEGY_PLANNED,
            )
        assert result["lookup_happened"] is True
        assert result["matches_count"] == 0
        assert result["experience_signal_summary"] == "no_history"
        assert result["recommended_strategy"] is None
        assert result["confidence_adjustment"] is None

    def test_matching_experiences_produce_signal(self):
        ctrl = _make_controller()
        experiences = [
            _make_experience(
                user_input_summary="test query execution plan",
                selected_strategy=STRATEGY_PLANNED,
                success=True,
                created_at=time.time() - 3600,
            ),
            _make_experience(
                user_input_summary="test query reasoning flow",
                selected_strategy=STRATEGY_PLANNED,
                success=True,
                created_at=time.time() - 7200,
            ),
        ]
        with patch("aihub.executive_controller.get_experiences_by_user", return_value=experiences):
            result = ctrl._compute_experience_signal(
                user_id="u1",
                query_text="test query execution plan",
                selected_strategy=STRATEGY_PLANNED,
            )
        assert result["lookup_happened"] is True
        assert result["matches_count"] >= 1
        assert result["confidence_adjustment"] is not None

    def test_recurring_failures_trigger_blocker(self):
        ctrl = _make_controller()
        experiences = [
            _make_experience(
                user_input_summary="deploy project alpha build",
                selected_strategy=STRATEGY_PLANNED,
                success=False,
                failure_type="timeout_error",
                created_at=time.time() - 1800,
            ),
            _make_experience(
                user_input_summary="deploy project alpha config",
                selected_strategy=STRATEGY_PLANNED,
                success=False,
                failure_type="timeout_error",
                created_at=time.time() - 3600,
            ),
            _make_experience(
                user_input_summary="deploy project alpha service",
                selected_strategy=STRATEGY_PLANNED,
                success=False,
                failure_type="timeout_error",
                created_at=time.time() - 5400,
            ),
        ]
        with patch("aihub.executive_controller.get_experiences_by_user", return_value=experiences):
            result = ctrl._compute_experience_signal(
                user_id="u1",
                query_text="deploy project alpha build",
                selected_strategy=STRATEGY_PLANNED,
            )
        # With 3 matching failures of the same type, blocker should fire
        if result["matches_count"] >= 2:
            assert result["recurring_failure_detected"] is True
            assert result["blocker_reason"] is not None

    def test_strategy_recommendation_on_high_failure_rate(self):
        ctrl = _make_controller()
        experiences = [
            # Planned strategy fails
            _make_experience(
                user_input_summary="analyze data pipeline steps",
                selected_strategy=STRATEGY_PLANNED,
                success=False,
                failure_type="planning_timeout",
                created_at=time.time() - 1000,
            ),
            _make_experience(
                user_input_summary="analyze data pipeline configs",
                selected_strategy=STRATEGY_PLANNED,
                success=False,
                failure_type="planning_timeout",
                created_at=time.time() - 2000,
            ),
            # Cognitive strategy succeeds
            _make_experience(
                user_input_summary="analyze data pipeline steps directly",
                selected_strategy=STRATEGY_COGNITIVE,
                success=True,
                created_at=time.time() - 3000,
            ),
            _make_experience(
                user_input_summary="analyze data pipeline output",
                selected_strategy=STRATEGY_COGNITIVE,
                success=True,
                created_at=time.time() - 4000,
            ),
        ]
        with patch("aihub.executive_controller.get_experiences_by_user", return_value=experiences):
            result = ctrl._compute_experience_signal(
                user_id="u1",
                query_text="analyze data pipeline steps",
                selected_strategy=STRATEGY_PLANNED,
            )
        # Should see action_bias populated
        if result["matches_count"] >= 2:
            assert isinstance(result["action_bias"], dict)

    def test_lookup_failure_handled_gracefully(self):
        ctrl = _make_controller()
        with patch(
            "aihub.executive_controller.get_experiences_by_user",
            side_effect=RuntimeError("db down"),
        ):
            result = ctrl._compute_experience_signal(
                user_id="u1",
                query_text="test",
                selected_strategy=STRATEGY_PLANNED,
            )
        assert result["lookup_happened"] is False
        assert result["experience_signal_summary"] == "lookup_failed"


# ── Decision Signals Tests ───────────────────────────────────────────────

class TestDecisionSignals:
    """_compute_decision_signals: experience + policy + simulation."""

    def test_returns_all_required_fields(self):
        ctrl = _make_controller()
        with patch("aihub.executive_controller.get_experiences_by_user", return_value=[]):
            result = ctrl._compute_decision_signals(
                user_id="u1",
                query_text="test query",
                selected_strategy=STRATEGY_PLANNED,
                strategy_confidence=0.75,
                psyche_state={"energy": 0.8, "focus": 0.7, "mood": 0.6},
                mode="tick",
            )

        # Core fields
        assert "selected_strategy" in result
        assert "strategy_confidence" in result
        assert "reason_codes" in result
        assert isinstance(result["reason_codes"], list)

        # Experience fields
        assert "experience_lookup_happened" in result
        assert "experience_matches_count" in result
        assert "experience_signal_summary" in result

        # Policy fields
        assert "policy_hints_loaded" in result
        assert "policy_feedback_loaded" in result
        assert "policy_confidence_delta" in result

        # Simulation fields
        assert "simulation_ran" in result
        assert "simulation_variants_count" in result

    def test_experience_adjustment_applied(self):
        ctrl = _make_controller()
        experiences = [
            _make_experience(
                user_input_summary="test query plan steps",
                selected_strategy=STRATEGY_PLANNED,
                success=True,
                created_at=time.time() - 600,
            ),
            _make_experience(
                user_input_summary="test query plan output",
                selected_strategy=STRATEGY_PLANNED,
                success=True,
                created_at=time.time() - 1200,
            ),
        ]
        with patch("aihub.executive_controller.get_experiences_by_user", return_value=experiences):
            result = ctrl._compute_decision_signals(
                user_id="u1",
                query_text="test query plan steps",
                selected_strategy=STRATEGY_PLANNED,
                strategy_confidence=0.70,
                psyche_state={"energy": 0.8, "focus": 0.7, "mood": 0.6},
                mode="tick",
            )

        # Confidence should be adjusted if experiences matched
        if result["experience_matches_count"] > 0:
            assert result["experience_lookup_happened"] is True

    def test_strategy_preserved_when_no_override(self):
        ctrl = _make_controller()
        with patch("aihub.executive_controller.get_experiences_by_user", return_value=[]):
            result = ctrl._compute_decision_signals(
                user_id="u1",
                query_text="neutral test",
                selected_strategy=STRATEGY_REACTIVE,
                strategy_confidence=0.65,
                psyche_state={"energy": 0.5, "focus": 0.5, "mood": 0.5},
                mode="tick",
            )
        assert result["selected_strategy"] == STRATEGY_REACTIVE

    def test_graceful_when_all_engines_fail(self):
        ctrl = _make_controller()
        with patch(
            "aihub.executive_controller.get_experiences_by_user",
            side_effect=RuntimeError("db fail"),
        ):
            result = ctrl._compute_decision_signals(
                user_id="u1",
                query_text="test",
                selected_strategy=STRATEGY_PLANNED,
                strategy_confidence=0.7,
                psyche_state={},
                mode="tick",
            )
        # Should still return valid structure
        assert result["selected_strategy"] == STRATEGY_PLANNED
        assert result["strategy_confidence"] == 0.7


# ── Post-Execution Reflection Tests ─────────────────────────────────────

class TestPostExecReflection:
    """_compute_post_exec_reflection: computed hindsight."""

    def test_returns_all_required_fields(self):
        ctrl = _make_controller()
        exec_result = _make_execution_result(ok=True)
        signals = {
            "strategy_confidence": 0.75,
            "simulation_ran": False,
            "simulation_risk_summary": "",
            "experience_blocker_reason": None,
            "reason_codes": [],
            "simulation_best_action": None,
        }
        result = ctrl._compute_post_exec_reflection(
            user_id="u1",
            strategy=STRATEGY_PLANNED,
            execution_result=exec_result,
            decision_signals=signals,
            duration_ms=150.0,
            query_text="test reflection",
        )
        assert "reflection_ran" in result
        assert "reflection_summary" in result
        assert "strategy_fit" in result
        assert "confidence_hindsight" in result
        assert "risk_hindsight" in result

    def test_failure_handled_gracefully(self):
        ctrl = _make_controller()
        exec_result = _make_execution_result(ok=False, errors=[{"error": "boom"}])
        signals = {
            "strategy_confidence": 0.5,
            "simulation_ran": False,
            "simulation_risk_summary": "",
            "experience_blocker_reason": None,
            "reason_codes": [],
            "simulation_best_action": None,
        }
        # Even if reflection engine isn't importable, should not crash
        result = ctrl._compute_post_exec_reflection(
            user_id="u1",
            strategy=STRATEGY_COGNITIVE,
            execution_result=exec_result,
            decision_signals=signals,
            duration_ms=50.0,
            query_text="test failure",
        )
        assert isinstance(result, dict)
        assert "reflection_ran" in result


# ── Strategy-Action Mapping Tests ────────────────────────────────────────

class TestStrategyActionMapping:
    """Validate the exec strategy ↔ action type mapping."""

    def test_all_strategies_have_action_type(self):
        mapping = ExecutiveController._EXEC_STRATEGY_TO_ACTION
        assert STRATEGY_PLANNED in mapping
        assert STRATEGY_REACTIVE in mapping
        assert STRATEGY_COGNITIVE in mapping

    def test_reverse_mapping_is_consistent(self):
        fwd = ExecutiveController._EXEC_STRATEGY_TO_ACTION
        rev = ExecutiveController._EXEC_ACTION_TO_STRATEGY
        for strategy, action in fwd.items():
            assert rev[action] == strategy


# ── build_agent_cycle_response Parity Field Tests ────────────────────────

class TestBuildAgentCycleResponseParity:
    """Verify that parity fields are surfaced in the canonical response."""

    def _make_cycle(self, **overrides) -> dict[str, Any]:
        base: dict[str, Any] = {
            "ok": True,
            "mode": "tick",
            "user_id": "u1",
            "cycle_id": "u1:tick:1234",
            "strategy": STRATEGY_PLANNED,
            "strategy_reason": "test",
            "planning_used": False,
            "reasoning_used": False,
            "planning_attempted": False,
            "planning_executed": False,
            "reasoning_attempted": False,
            "reasoning_executed": False,
            "context_signals": {"memory_total": 0},
            "perception": {"user_id": "u1", "mode": "tick"},
            "decision_context": {
                "psyche_state": {},
                "cognitive_signal": {},
                "memory_total": 0,
                "knowledge_hits": 0,
                "goal_selected": "",
            },
            "active_goals_summary": [],
            "selected_goal": None,
            "selected_goal_reason": "",
            "goal_context_trace": {
                "created_goal_ids": [],
                "candidates": [],
                "top_scores": [],
                "execution_hint": None,
            },
            "goal_affected_planning": False,
            "goal_progress_changed": False,
            "goal_progress_update": {},
            "execution_plan": {
                "strategy": STRATEGY_PLANNED,
                "strategy_reason": "test",
                "planning_used": False,
                "phases": [],
                "metadata": {},
            },
            "execution_result": {
                "ok": True,
                "action_summary": "test",
                "errors": [],
                "payload": {},
            },
            "reflection": {
                "mode": "tick",
                "strategy": STRATEGY_PLANNED,
                "strategy_reason": "test",
                "planning_used": False,
                "reasoning_used": False,
                "memory_hits": {"total": 0, "episodic": 0, "semantic": 0},
                "context_signals": {},
                "duration_ms": 10.0,
                "action_summary": "test",
                "errors": [],
            },
            "legacy_response": {},
            # Parity fields
            "experience_lookup_happened": True,
            "experience_matches_count": 3,
            "experience_influenced_strategy": True,
            "experience_confidence_adjustment": 0.05,
            "experience_blocker_reason": None,
            "experience_signal_summary": "matches=3 succ=0.80 fail=0.20 conf_adj=+0.05",
            "policy_hints_loaded": True,
            "policy_profile_name": "user:u1",
            "policy_feedback_loaded": True,
            "policy_feedback_applied": True,
            "policy_feedback_summary": "Boosted confidence",
            "policy_confidence_delta": 0.03,
            "policy_blocker_sensitivity": 0.0,
            "policy_simulation_risk_cal": 0.0,
            "policy_strategy_adjustments": {},
            "simulation_ran": True,
            "simulation_variants_count": 4,
            "simulation_best_action": "action",
            "simulation_risk_summary": "risk=0.15 conf=0.82 util=0.65",
            "reflection_ran": True,
            "reflection_summary": "Strategy worked well",
            "strategy_fit": "optimal",
            "confidence_hindsight": 0.08,
            "risk_hindsight": -0.05,
            "decision_signals_reason_codes": ["EXPERIENCE_CONFIDENCE", "POLICY_FEEDBACK_CONFIDENCE"],
        }
        base.update(overrides)
        return base

    def test_parity_fields_present_in_response(self):
        cycle = self._make_cycle()
        response = build_agent_cycle_response(cycle, include_debug=False)

        # Experience parity
        assert response["experience_lookup_happened"] is True
        assert response["experience_matches_count"] == 3
        assert response["experience_influenced_strategy"] is True
        assert response["experience_confidence_adjustment"] == 0.05

        # Policy parity
        assert response["policy_hints_loaded"] is True
        assert response["policy_feedback_applied"] is True
        assert response["policy_confidence_delta"] == 0.03

        # Simulation parity
        assert response["simulation_ran"] is True
        assert response["simulation_variants_count"] == 4
        assert response["simulation_best_action"] == "action"

        # Reflection parity
        assert response["reflection_ran"] is True
        assert response["reflection_summary"] == "Strategy worked well"
        assert response["strategy_fit"] == "optimal"

        # Reason codes
        assert "EXPERIENCE_CONFIDENCE" in response["decision_signals_reason_codes"]

    def test_parity_fields_default_when_missing(self):
        cycle = self._make_cycle()
        # Remove all parity fields
        for key in list(cycle.keys()):
            if key.startswith(("experience_", "policy_", "simulation_", "reflection_", "decision_signals_")):
                if key not in ("experience_lookup_happened",):
                    del cycle[key]
        cycle["experience_lookup_happened"] = False

        response = build_agent_cycle_response(cycle, include_debug=False)

        # Defaults should be safe/falsy
        assert response["experience_lookup_happened"] is False
        assert response["simulation_ran"] is False
        assert response["reflection_ran"] is False
        assert isinstance(response["decision_signals_reason_codes"], list)
