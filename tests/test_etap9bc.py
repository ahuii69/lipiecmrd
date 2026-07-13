#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ETAP 9B/9C comprehensive tests:
  - ConsistencyEngine
  - ReflectionEngine
  - PolicyEngine
  - SimulationEngine
  - Cockpit API
  - DB schema (new tables)
  - Integration wiring
"""

from __future__ import annotations

import pytest

from aihub.db import exec_one, fetch_all, fetch_one, init_db, now_ts

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture(autouse=True)
def _setup_db():
    """Ensure DB is initialized for all tests."""
    init_db()


# ============================================================================
# DB SCHEMA TESTS
# ============================================================================


class TestEtap9Schema:
    """Verify new ETAP 9 tables exist and accept data."""

    def test_consistency_checks_table_exists(self) -> None:
        rows = fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='consistency_checks'"
        )
        assert len(rows) == 1

    def test_reflections_table_exists(self) -> None:
        rows = fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reflections'"
        )
        assert len(rows) == 1

    def test_policy_profiles_table_exists(self) -> None:
        rows = fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='policy_profiles'"
        )
        assert len(rows) == 1

    def test_simulations_table_exists(self) -> None:
        rows = fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='simulations'"
        )
        assert len(rows) == 1

    def test_consistency_checks_insert(self) -> None:
        exec_one(
            """INSERT INTO consistency_checks(id, user_id, fact_text, classification,
               confidence, similarity_score, reasoning, suggested_action, metadata, ts)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "test_cc_1",
                "u1",
                "fact",
                "new_fact",
                0.8,
                0.0,
                "ok",
                "store",
                "{}",
                now_ts(),
            ),
        )
        row = fetch_one("SELECT * FROM consistency_checks WHERE id='test_cc_1'")
        assert row is not None
        assert row["classification"] == "new_fact"

    def test_reflections_insert(self) -> None:
        exec_one(
            """INSERT INTO reflections(id, user_id, action_type, outcome, outcome_score,
               lesson_learned, policy_signal, policy_weight, recommended_adjustment,
               patterns_detected, metadata, ts)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "test_ref_1",
                "u1",
                "query",
                "success",
                0.9,
                "lesson",
                "boost",
                0.5,
                "no_change",
                "[]",
                "{}",
                now_ts(),
            ),
        )
        row = fetch_one("SELECT * FROM reflections WHERE id='test_ref_1'")
        assert row is not None
        assert row["outcome"] == "success"

    def test_policy_profiles_upsert(self) -> None:
        ts = now_ts()
        exec_one(
            """INSERT INTO policy_profiles(user_id, hints, reliability_index, total_reflections, ts)
               VALUES(?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET hints=excluded.hints, ts=excluded.ts""",
            ("u1", "[]", 0.7, 5, ts),
        )
        row = fetch_one("SELECT * FROM policy_profiles WHERE user_id='u1'")
        assert row is not None
        assert float(row["reliability_index"]) == pytest.approx(0.7)

    def test_simulations_insert(self) -> None:
        exec_one(
            """INSERT INTO simulations(id, user_id, variants_evaluated, best_action,
               best_score, ranked_data, simulation_time_ms, metadata, ts)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            ("test_sim_1", "u1", 3, "query", 0.8, "[]", 1.5, "{}", now_ts()),
        )
        row = fetch_one("SELECT * FROM simulations WHERE id='test_sim_1'")
        assert row is not None
        assert int(row["variants_evaluated"]) == 3


# ============================================================================
# CONSISTENCY ENGINE TESTS
# ============================================================================


class TestConsistencyEngine:
    """Tests for ConsistencyEngine scoring and classification."""

    def test_import(self) -> None:
        from aihub.consistency_engine import (
            apply_consistency_verdict,
            check_consistency,
            get_consistency_checks,
            get_consistency_stats,
        )

        assert callable(check_consistency)
        assert callable(apply_consistency_verdict)
        assert callable(get_consistency_checks)
        assert callable(get_consistency_stats)

    def test_new_fact_classification(self) -> None:
        """A brand new fact with no history should be 'new_fact'."""
        from aihub.consistency_engine import check_consistency

        verdict = check_consistency("test_user_cons", "Zupełnie nowy fakt o kwantach")
        assert verdict is not None
        assert verdict.classification == "new_fact"
        assert verdict.confidence > 0

    def test_duplicate_detection(self) -> None:
        """Identical fact text should be detected as duplicate."""
        from aihub.consistency_engine import check_consistency
        from aihub.memory_engine import add_fact

        uid = "test_user_dup"
        fact_text = "Użytkownik preferuje programowanie w Pythonie"
        add_fact(uid, fact_text, ["pref"], {"source": "test"})

        verdict = check_consistency(uid, fact_text)
        assert verdict is not None
        assert verdict.classification in ("duplicate", "revision")
        assert verdict.similarity_score > 0.8

    def test_revision_detection(self) -> None:
        """A revised version of a fact should be classified appropriately."""
        from aihub.consistency_engine import check_consistency
        from aihub.memory_engine import add_fact

        uid = "test_user_rev"
        add_fact(uid, "Lubię kawę z mlekiem", ["pref"], {"source": "test"})

        verdict = check_consistency(uid, "Teraz lubię kawę bez mleka")
        assert verdict is not None
        # Should detect this as revision or conflict due to negation/revision keywords
        assert verdict.classification in ("revision", "conflict", "new_fact")

    def test_get_stats_empty(self) -> None:
        from aihub.consistency_engine import get_consistency_stats

        stats = get_consistency_stats("nonexistent_user")
        assert isinstance(stats, dict)
        assert stats.get("total_checks", 0) == 0

    def test_get_checks_empty(self) -> None:
        from aihub.consistency_engine import get_consistency_checks

        checks = get_consistency_checks("nonexistent_user")
        assert isinstance(checks, list)
        assert len(checks) == 0

    def test_verdict_dataclass_fields(self) -> None:
        from aihub.consistency_engine import ConsistencyVerdict

        v = ConsistencyVerdict(
            classification="new_fact",
            confidence=0.9,
            matched_node_id=None,
            matched_content=None,
            similarity_score=0.0,
            reasoning="test",
            suggested_action="store",
        )
        assert v.classification == "new_fact"
        assert v.confidence == 0.9
        assert v.suggested_action == "store"


# ============================================================================
# REFLECTION ENGINE TESTS
# ============================================================================


class TestReflectionEngine:
    """Tests for ReflectionEngine outcome classification and lesson extraction."""

    def test_import(self) -> None:
        from aihub.reflection_engine import (
            reflect_on_action,
        )

        assert callable(reflect_on_action)

    def test_success_outcome(self) -> None:
        """Successful action should produce boost signal."""
        from aihub.reflection_engine import ReflectionInput, reflect_on_action

        rinput = ReflectionInput(
            user_id="test_user_ref",
            action_type="memory_search",
            parameters={"query": "test"},
            confidence=0.8,
            execution_result={"ok": True, "total": 5},
            decision_reasoning="search memory for test",
        )
        result = reflect_on_action(rinput)
        assert result.outcome == "success"
        assert result.outcome_score > 0.5
        assert result.policy_signal == "boost"
        assert result.lesson_learned != ""

    def test_failure_outcome(self) -> None:
        """Failed action should produce penalize signal."""
        from aihub.reflection_engine import ReflectionInput, reflect_on_action

        rinput = ReflectionInput(
            user_id="test_user_ref_fail",
            action_type="web_request",
            parameters={"url": "test"},
            confidence=0.5,
            execution_result={"ok": False, "error": "timeout"},
        )
        result = reflect_on_action(rinput)
        assert result.outcome == "failure"
        assert result.policy_signal == "penalize"
        assert (
            "timeout" in result.recommended_adjustment.lower()
            or "timeout" in result.lesson_learned.lower()
        )

    def test_skipped_outcome(self) -> None:
        """Empty execution result should be skipped."""
        from aihub.reflection_engine import ReflectionInput, reflect_on_action

        rinput = ReflectionInput(
            user_id="test_user_ref_skip",
            action_type="skip",
            parameters={},
            confidence=0.3,
            execution_result={},
        )
        result = reflect_on_action(rinput)
        assert result.outcome == "skipped"
        assert result.policy_signal == "neutral"

    def test_reflection_persistence(self) -> None:
        """Reflection should be persisted to DB."""
        from aihub.reflection_engine import (
            ReflectionInput,
            get_reflections,
            reflect_on_action,
        )

        uid = "test_user_ref_persist"
        rinput = ReflectionInput(
            user_id=uid,
            action_type="learn",
            parameters={},
            confidence=0.9,
            execution_result={"ok": True},
        )
        reflect_on_action(rinput)
        refs = get_reflections(uid, limit=5)
        assert len(refs) >= 1
        assert refs[0]["action_type"] == "learn"

    def test_lesson_for_action(self) -> None:
        """get_action_lessons should return lessons."""
        from aihub.reflection_engine import (
            ReflectionInput,
            get_action_lessons,
            reflect_on_action,
        )

        uid = "test_user_ref_lesson"
        rinput = ReflectionInput(
            user_id=uid,
            action_type="research",
            parameters={},
            confidence=0.7,
            execution_result={"ok": True, "total": 3},
        )
        reflect_on_action(rinput)
        lessons = get_action_lessons(uid, "research")
        assert len(lessons) >= 1

    def test_low_confidence_lesson(self) -> None:
        """Low confidence should produce advisory lesson."""
        from aihub.reflection_engine import ReflectionInput, reflect_on_action

        rinput = ReflectionInput(
            user_id="test_user_lowconf",
            action_type="reason",
            parameters={},
            confidence=0.2,
            execution_result={"ok": True},
        )
        result = reflect_on_action(rinput)
        assert (
            "pewności" in result.lesson_learned or "kontekst" in result.lesson_learned
        )


# ============================================================================
# POLICY ENGINE TESTS
# ============================================================================


class TestPolicyEngine:
    """Tests for PolicyEngine hint generation and application."""

    def test_import(self) -> None:
        from aihub.policy_engine import (
            build_policy_profile,
        )

        assert callable(build_policy_profile)

    def test_empty_profile(self) -> None:
        """No reflections → empty profile."""
        from aihub.policy_engine import build_policy_profile

        profile = build_policy_profile("nonexistent_user")
        assert profile.user_id == "nonexistent_user"
        assert len(profile.hints) == 0
        assert profile.reliability_index == 0.5

    def test_profile_after_reflections(self) -> None:
        """Profile should have hints after multiple reflections."""
        from aihub.policy_engine import build_policy_profile
        from aihub.reflection_engine import ReflectionInput, reflect_on_action

        uid = "test_user_policy"
        for _ in range(3):
            reflect_on_action(
                ReflectionInput(
                    user_id=uid,
                    action_type="memory_search",
                    parameters={},
                    confidence=0.8,
                    execution_result={"ok": True, "total": 5},
                )
            )

        profile = build_policy_profile(uid)
        assert profile.total_reflections >= 3
        # With 3+ reflections for same action, should generate a hint
        matching = [h for h in profile.hints if h.action_type == "memory_search"]
        assert len(matching) >= 1
        assert matching[0].signal == "boost"

    def test_boost_increases_confidence(self) -> None:
        """Boost hint should increase confidence."""
        from aihub.policy_engine import PolicyHint, apply_policy_to_confidence

        hints = [
            PolicyHint(
                action_type="test_action",
                signal="boost",
                weight=0.8,
                reason="test",
            )
        ]
        adj, reason = apply_policy_to_confidence(0.5, hints, "test_action")
        assert adj > 0.5
        assert "boost" in reason

    def test_penalize_decreases_confidence(self) -> None:
        """Penalize hint should decrease confidence."""
        from aihub.policy_engine import PolicyHint, apply_policy_to_confidence

        hints = [
            PolicyHint(
                action_type="bad_action",
                signal="penalize",
                weight=0.8,
                reason="test",
            )
        ]
        adj, reason = apply_policy_to_confidence(0.7, hints, "bad_action")
        assert adj < 0.7
        assert "penalize" in reason

    def test_avoid_heavy_reduction(self) -> None:
        """Avoid signal should heavily reduce confidence."""
        from aihub.policy_engine import PolicyHint, apply_policy_to_confidence

        hints = [
            PolicyHint(
                action_type="dangerous",
                signal="avoid",
                weight=0.9,
                reason="4 failures",
            )
        ]
        adj, reason = apply_policy_to_confidence(0.8, hints, "dangerous")
        assert adj < 0.3
        assert "avoid" in reason

    def test_no_hint_no_change(self) -> None:
        """If no matching hint, confidence unchanged."""
        from aihub.policy_engine import PolicyHint, apply_policy_to_confidence

        hints = [
            PolicyHint(action_type="other", signal="boost", weight=0.5, reason="x")
        ]
        adj, reason = apply_policy_to_confidence(0.6, hints, "unrelated")
        assert adj == 0.6
        assert "no_policy_hint" in reason

    def test_get_profile_data(self) -> None:
        from aihub.policy_engine import get_policy_profile_data

        data = get_policy_profile_data("some_user")
        assert isinstance(data, dict)
        assert "user_id" in data


# ============================================================================
# SIMULATION ENGINE TESTS
# ============================================================================


class TestSimulationEngine:
    """Tests for SimulationEngine variant scoring and ranking."""

    def test_import(self) -> None:
        from aihub.simulation_engine import (
            simulate_action,
        )

        assert callable(simulate_action)

    def test_basic_simulation(self) -> None:
        """Simulate should return ranked variants."""
        from aihub.simulation_engine import simulate_action

        result = simulate_action(
            "test_sim_user",
            "memory_search",
            {"query": "test"},
            {"intent": "query"},
        )
        assert result.variants_evaluated > 0
        assert result.best_variant is not None
        assert result.best_variant.composite_score > 0
        assert len(result.ranked_variants) > 0

    def test_variants_are_ranked_descending(self) -> None:
        """Variants should be sorted by composite score descending."""
        from aihub.simulation_engine import simulate_action

        result = simulate_action(
            "test_sim_rank",
            "research",
            {"query": "test"},
            {"intent": "research"},
        )
        scores = [v.composite_score for v in result.ranked_variants]
        assert scores == sorted(scores, reverse=True)

    def test_skip_variant_included(self) -> None:
        """The skip variant should be included in simulation."""
        from aihub.simulation_engine import simulate_action

        result = simulate_action(
            "test_sim_skip",
            "action",
            {},
            {"intent": "action"},
        )
        actions = [v.action_type for v in result.ranked_variants]
        assert "skip" in actions

    def test_all_score_fields_present(self) -> None:
        """Each variant score should have risk, confidence, utility, cost."""
        from aihub.simulation_engine import simulate_action

        result = simulate_action(
            "test_sim_fields",
            "reason",
            {},
            {"intent": "query"},
        )
        for v in result.ranked_variants:
            assert 0.0 <= v.risk <= 1.0
            assert 0.0 <= v.confidence <= 1.0
            assert 0.0 <= v.utility <= 1.0
            assert 0.0 <= v.cost <= 1.0
            assert v.reasoning != ""

    def test_simulation_persistence(self) -> None:
        """Simulation should be persisted to DB."""
        from aihub.simulation_engine import get_simulations, simulate_action

        uid = "test_sim_persist"
        simulate_action(uid, "learn", {}, {"intent": "learn"})
        sims = get_simulations(uid)
        assert len(sims) >= 1
        assert sims[0]["best_action"] != ""

    def test_simulation_with_policy_hints(self) -> None:
        """Simulation should respect policy hints in context."""
        from aihub.simulation_engine import simulate_action

        result = simulate_action(
            "test_sim_policy",
            "research",
            {"query": "test"},
            {
                "intent": "research",
                "policy_hints": [
                    {
                        "action_type": "research",
                        "signal": "penalize",
                        "weight": 0.8,
                        "reason": "test",
                    },
                ],
            },
        )
        # Research should be penalized
        research_variants = [
            v for v in result.ranked_variants if v.action_type == "research"
        ]
        assert len(research_variants) >= 1
        # Its confidence should be lower than default
        assert research_variants[0].confidence < 0.6

    def test_max_variants_respected(self) -> None:
        """max_variants should limit variant count."""
        from aihub.simulation_engine import simulate_action

        result = simulate_action(
            "test_sim_limit",
            "action",
            {},
            {"intent": "action"},
            max_variants=2,
        )
        assert result.variants_evaluated <= 2


# ============================================================================
# COCKPIT API TESTS
# ============================================================================


class TestCockpitAPI:
    """Tests for cockpit API endpoints."""

    def test_import(self) -> None:
        from aihub.cockpit_api import router

        assert router is not None
        assert router.prefix == "/cockpit"

    def test_routes_registered(self) -> None:
        from aihub.cockpit_api import router

        route_paths = [r.path for r in router.routes]
        assert "/cockpit/consistency/{user_id}" in route_paths
        assert "/cockpit/reflections/{user_id}" in route_paths
        assert "/cockpit/policy/{user_id}" in route_paths
        assert "/cockpit/simulations/{user_id}" in route_paths
        assert "/cockpit/overview/{user_id}" in route_paths


# ============================================================================
# INTEGRATION WIRING TESTS
# ============================================================================


class TestIntegrationWiring:
    """Verify ETAP 9 engines are wired into the runtime."""

    def test_consistency_wired_in_add_fact(self) -> None:
        """add_fact should run consistency check (doesn't crash)."""
        import aihub.memory_engine as me

        # Should not raise, even if consistency check produces new_fact
        node_id = me.add_fact(
            "test_wire_cons",
            "Testowy fakt dla weryfikacji wiringu",
            ["test"],
            {"source": "test"},
        )
        assert node_id != ""

    def test_main_includes_cockpit_router(self) -> None:
        """Main app should include cockpit router."""
        from aihub.main import app

        route_paths = [r.path for r in app.routes]
        cockpit_paths = [p for p in route_paths if "/cockpit" in p]
        assert len(cockpit_paths) >= 1

    def test_chat_trace_has_etap9_fields(self) -> None:
        """Chat trace template should include ETAP 9B/9C fields."""
        # We can't easily run the full chat flow, but we can verify
        # the trace fields exist in the source
        import inspect

        from aihub import chat_runtime
        from aihub.turn import ops as turn_ops
        from aihub.turn.mixins import decision as turn_decision
        from aihub.turn.mixins import experience as turn_experience
        from aihub.turn.mixins import web as turn_web
        from aihub.turn.mixins import prompt_context as turn_prompt
        from aihub.turn.mixins import execution as turn_execution
        from aihub.turn.mixins import pipeline as turn_pipeline

        source = "".join(
            [
                inspect.getsource(chat_runtime),
                inspect.getsource(turn_ops),
                inspect.getsource(turn_decision),
                inspect.getsource(turn_experience),
                inspect.getsource(turn_web),
                inspect.getsource(turn_prompt),
                inspect.getsource(turn_execution),
                inspect.getsource(turn_pipeline),
            ]
        )
        assert "consistency_check_ran" in source
        assert "reflection_ran" in source
        assert "policy_hints_loaded" in source
        assert "simulation_ran" in source
        assert "simulation_best_action" in source
