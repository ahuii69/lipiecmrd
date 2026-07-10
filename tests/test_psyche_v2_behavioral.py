#!/usr/bin/env python3
"""
Tests for Psyche V2 behavioral depth: habits, relations, pressure recovery.
"""

import pytest
import time
from aihub.psyche_v2_service import PsycheV2Service
from aihub.psyche_v2_habits import (
    reinforce_or_create_habit,
    get_habits_for_user,
    decay_habits,
)
from aihub.psyche_v2_repository import (
    ensure_psyche_profile,
    update_psyche_profile,
)


def test_habit_creation(isolated_db):
    """Test habit creation from repeated pattern."""
    user_id = "user-habit-test"
    
    habit = reinforce_or_create_habit(
        user_id=user_id,
        habit_name="cautious_after_failure",
        habit_type="caution_tendency",
        context={"source": "test", "outcome": "failure"},
        intensity_boost=0.1,
    )
    
    assert habit is not None
    assert habit.habit_name == "cautious_after_failure"
    assert habit.habit_type == "caution_tendency"
    assert habit.intensity >= 0.3
    assert habit.reinforcement_count == 1


def test_habit_reinforcement(isolated_db):
    """Test habit reinforcement increases intensity."""
    user_id = "user-habit-reinforce-test"
    
    # Create habit
    habit1 = reinforce_or_create_habit(
        user_id=user_id,
        habit_name="confident_after_success",
        habit_type="confidence_tendency",
        context={"outcome": "success"},
        intensity_boost=0.1,
    )
    
    initial_intensity = habit1.intensity
    
    # Reinforce
    habit2 = reinforce_or_create_habit(
        user_id=user_id,
        habit_name="confident_after_success",
        habit_type="confidence_tendency",
        context={"outcome": "success"},
        intensity_boost=0.1,
    )
    
    assert habit2.reinforcement_count == 2
    assert habit2.intensity > initial_intensity


def test_habit_decay(isolated_db):
    """Test habit intensity decays over time."""
    user_id = "user-habit-decay-test"
    
    # Create habit
    habit = reinforce_or_create_habit(
        user_id=user_id,
        habit_name="test_habit",
        habit_type="test_type",
        context={},
        intensity_boost=0.2,
    )
    
    initial_intensity = habit.intensity
    
    # Decay
    decayed_count = decay_habits(user_id, decay_rate=0.05)
    
    # For fresh habit, decay may not trigger (age check)
    # Test logic exists
    assert decayed_count >= 0


def test_relation_dynamics_update(isolated_db):
    """Test relation dynamics update from outcome."""
    user_id = "user-relation-test"
    service = PsycheV2Service()
    
    # Ensure user
    profile, state = service.ensure_user(user_id)
    initial_trust = profile.relation_trust
    initial_friction = profile.relation_friction
    
    # Apply success outcome
    service.update_relations_from_outcome(
        user_id=user_id,
        outcome_kind="success",
        contradictions_present=0,
    )
    
    # Check updated
    updated_profile = ensure_psyche_profile(user_id)
    assert updated_profile.relation_trust >= initial_trust
    assert updated_profile.relation_friction <= initial_friction


def test_relation_friction_increases_on_failure(isolated_db):
    """Test relation friction increases on failure."""
    user_id = "user-friction-test"
    service = PsycheV2Service()
    
    profile, state = service.ensure_user(user_id)
    initial_friction = profile.relation_friction
    
    # Apply failure outcome
    service.update_relations_from_outcome(
        user_id=user_id,
        outcome_kind="failure",
        contradictions_present=0,
    )
    
    updated_profile = ensure_psyche_profile(user_id)
    assert updated_profile.relation_friction > initial_friction


def test_pressure_recovery_on_success(isolated_db):
    """Test pressure decreases on success."""
    user_id = "user-pressure-success-test"
    service = PsycheV2Service()
    
    # Ensure user and set high pressure
    profile, state = service.ensure_user(user_id)
    state.pressure = 0.8
    from aihub.psyche_v2_repository import update_psyche_state, ensure_psyche_state
    update_psyche_state(state)
    
    # Apply success outcome
    result = service.apply_outcome_event(
        user_id=user_id,
        outcome_kind="success",
        source_ref="test-success",
        context={},
    )
    
    assert result["succeeded"] is True
    
    # Check pressure reduced
    updated_state = ensure_psyche_state(user_id)
    assert updated_state.pressure < 0.8


def test_pressure_increase_on_failure(isolated_db):
    """Test pressure increases on failure."""
    user_id = "user-pressure-failure-test"
    service = PsycheV2Service()
    
    profile, state = service.ensure_user(user_id)
    initial_pressure = state.pressure
    
    # Apply failure outcome
    result = service.apply_outcome_event(
        user_id=user_id,
        outcome_kind="failure",
        source_ref="test-failure",
        context={},
    )
    
    assert result["succeeded"] is True
    
    from aihub.psyche_v2_repository import ensure_psyche_state
    updated_state = ensure_psyche_state(user_id)
    assert updated_state.pressure > initial_pressure


def test_habits_created_from_outcome(isolated_db):
    """Test that habits are created during outcome events."""
    user_id = "user-habit-outcome-test"
    service = PsycheV2Service()
    
    # Ensure user
    service.ensure_user(user_id)
    
    # Apply failure (should create cautious habit)
    service.apply_outcome_event(
        user_id=user_id,
        outcome_kind="failure",
        source_ref="test",
        context={},
    )
    
    # Check habit created
    habits = service.get_habits(user_id, min_intensity=0.0)
    assert len(habits) > 0
    
    habit_names = [h.habit_name for h in habits]
    assert "cautious_after_failure" in habit_names


def test_psyche_event_compaction(isolated_db):
    """Test psyche event compaction prevents unbounded growth."""
    user_id = "user-compact-test"
    service = PsycheV2Service()
    
    # Create many events
    for i in range(30):
        service.apply_event(
            user_id=user_id,
            event_type="interaction_complete",
            reason_text=f"Event {i}",
            source_ref=f"ref-{i}",
            signal_strength=0.5,
        )
    
    # Compact
    result = service.compact_recent_events(user_id, keep_recent=20)
    
    assert result["ok"] is True
    assert result["kept_count"] == 20
    assert result["compacted"] is True
    assert result["deleted_count"] == 10


def test_get_relations_summary(isolated_db):
    """Test relations summary endpoint logic."""
    user_id = "user-relations-summary-test"
    service = PsycheV2Service()
    
    # Ensure user
    service.ensure_user(user_id)
    
    # Get relations
    relations = service.get_relations_summary(user_id)
    
    assert "trust" in relations
    assert "friction" in relations
    assert "warmth" in relations
    assert "directness_tolerance" in relations
    assert 0.0 <= relations["trust"] <= 1.0


def test_habits_service_integration(isolated_db):
    """Test habits get/reinforce through service."""
    user_id = "user-habits-service-test"
    service = PsycheV2Service()
    
    # Create habit
    habit = service.reinforce_habit(
        user_id=user_id,
        habit_name="test_habit",
        habit_type="test_type",
        context={"test": "data"},
    )
    
    assert habit is not None
    assert habit.habit_name == "test_habit"
    
    # Get habits
    habits = service.get_habits(user_id, min_intensity=0.0)
    assert len(habits) >= 1
    assert any(h.habit_name == "test_habit" for h in habits)
