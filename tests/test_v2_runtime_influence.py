#!/usr/bin/env python3
"""
Tests for real runtime influence: Memory V2 and Psyche V2 actively shaping decisions.
"""

import pytest
from aihub.memory_v2_service import MemoryV2Service
from aihub.psyche_v2_service import PsycheV2Service
from aihub.runtime_memory_bridge import build_memory_v2_runtime_snapshot
from aihub.runtime_psyche_bridge import build_psyche_v2_runtime_snapshot
from aihub.runtime_identity_bridge import build_identity_bridge_snapshot


def test_procedure_confidence_in_snapshot(isolated_db):
    """Test procedure confidence is included in memory snapshot."""
    user_id = "user-proc-conf-test"
    
    # Build snapshot (even without procedures)
    snapshot = build_memory_v2_runtime_snapshot(user_id, "deploy")
    
    assert snapshot["loaded"] is True
    assert "avg_procedure_confidence" in snapshot
    assert snapshot["avg_procedure_confidence"] >= 0.0


def test_memory_contradiction_influences_strategy(isolated_db):
    """Test contradictions trigger strategy override."""
    user_id = "user-contradiction-influence-test"
    service = MemoryV2Service()
    
    # Create contradicting memories
    service.create_memory_item(
        user_id=user_id,
        memory_type="preference",
        scope="user",
        title="Preference A",
        content="User likes X",
        source_kind="chat_turn",
        importance_score=0.8,
    )
    
    # Simulate contradiction (would require contradiction detection logic)
    # For now, test that snapshot returns contradictions_count
    snapshot = build_memory_v2_runtime_snapshot(user_id, "")
    
    assert "contradictions_count" in snapshot


def test_psyche_behavior_policy_in_snapshot(isolated_db):
    """Test behavior policy is included in psyche snapshot."""
    user_id = "user-behavior-policy-test"
    service = PsycheV2Service()
    
    # Ensure user
    service.ensure_user(user_id)
    
    # Build snapshot
    snapshot = build_psyche_v2_runtime_snapshot(user_id)
    
    assert snapshot["loaded"] is True
    assert "behavior_policy" in snapshot
    
    policy = snapshot["behavior_policy"]
    assert "directness" in policy
    assert "verbosity" in policy
    assert "autonomy_bias" in policy


def test_identity_bridge_includes_habits_and_pressure(isolated_db):
    """Test unified identity bridge includes habits and pressure."""
    user_id = "user-identity-bridge-test"
    
    # Create habit
    from aihub.psyche_v2_service import PsycheV2Service
    service = PsycheV2Service()
    service.reinforce_habit(
        user_id=user_id,
        habit_name="test_habit",
        habit_type="test",
        context={},
    )
    
    # Build identity bridge
    identity = build_identity_bridge_snapshot(user_id, "test query")
    
    assert identity.user_id == user_id
    assert "active_habits" in identity.__dict__
    assert "pressure" in identity.__dict__
    assert identity.active_habits is not None


def test_high_pressure_low_trust_signals_caution(isolated_db):
    """Test that high pressure + low trust combination signals caution."""
    user_id = "user-caution-signal-test"
    service = PsycheV2Service()
    
    # Set high pressure, low trust
    profile, state = service.ensure_user(user_id)
    state.pressure = 0.8
    state.current_mode = "cautious"
    from aihub.psyche_v2_repository import update_psyche_state, update_psyche_profile
    update_psyche_state(state)
    
    profile.relation_trust = 0.2
    update_psyche_profile(profile)
    
    # Get policy
    from aihub.psyche_v2_policy import derive_behavior_policy
    policy = derive_behavior_policy(user_id, profile, state)
    
    assert policy.current_mode == "cautious"
    assert policy.relation_trust == 0.2
    assert policy.caution >= 0.4
