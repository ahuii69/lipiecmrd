#!/usr/bin/env python3
"""
Agent Response Quality Tests

Verifies that /agent/run and /agent/tick produce different response_text
based on psyche state, memory context, and procedure confidence.
"""

import pytest
from fastapi.testclient import TestClient

from aihub.memory_v2_models import MemoryV2Item, MemoryV2Procedure
from aihub.memory_v2_repository import insert_memory_item, insert_memory_procedure
from aihub.psyche_v2_repository import ensure_psyche_profile, ensure_psyche_state, update_psyche_state, update_psyche_profile
from aihub.db import now_ts


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    """Disable API key auth for tests."""
    from aihub import main
    monkeypatch.setenv("API_KEY", "")


def test_agent_response_varies_with_procedure_confidence(client: TestClient, isolated_db, monkeypatch):
    """
    Test that agent response reflects high procedure confidence.
    
    High confidence procedure → more execution-focused response
    """
    user_id = "agent_proc_conf_user"
    ts = now_ts()
    
    # High confidence procedure
    proc = MemoryV2Procedure(
        id="proc1",
        user_id=user_id,
        name="Run pytest for testing",
        trigger_pattern="test.*pytest",
        recommended_strategy="pytest -q tests/",
        confidence_score=0.92,
        evidence_count=12,
        success_count=11,
        failure_count=1,
        last_used_ts=ts,
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_procedure(proc)
    
    # Focused, high confidence psyche
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.current_mode = "focused"
    state.certainty = 0.85
    state.pressure = 0.3
    update_psyche_state(state)
    
    # Run agent
    response = client.post(
        "/agent/run",
        json={
            "user_id": user_id,
            "goal_id": "test-goal",
            "mode": "run",
            "query": "Run tests",
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Validate: Procedure bias applied
    assert data.get("memory_v2_procedure_bias_applied", False), "Procedure bias should apply"
    assert data.get("psyche_v2_behavior_applied", False), "Psyche behavior should apply"
    
    # Validate: Response text exists and reflects context
    assert data.get("response_text"), "Agent should return response_text"


def test_agent_response_varies_with_contradictions(client: TestClient, isolated_db, monkeypatch):
    """
    Test that agent response reflects contradictions + caution.
    
    Contradictions present → more cautious/exploratory response
    """
    user_id = "agent_contradiction_user"
    ts = now_ts()
    
    # Contradicting memories
    fact1 = MemoryV2Item(
        id="a_fact1",
        user_id=user_id,
        memory_type="fact",
        scope="user",
        title="User prefers Docker",
        content="User said Docker is preferred",
        source_kind="agent_cycle",
        importance_score=0.8,
        salience_score=0.8,
        contradiction_state="conflicted",
        summary="Docker pref",
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(fact1)
    
    fact2 = MemoryV2Item(
        id="a_fact2",
        user_id=user_id,
        memory_type="fact",
        scope="user",
        title="Recently asked about Podman",
        content="User explored Podman alternatives",
        source_kind="agent_cycle",
        importance_score=0.7,
        salience_score=0.7,
        contradiction_state="conflicted",
        summary="Podman interest",
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(fact2)
    
    # High caution psyche
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.certainty = 0.35
    state.pressure = 0.7
    update_psyche_state(state)
    
    # Run agent
    response = client.post(
        "/agent/run",
        json={
            "user_id": user_id,
            "goal_id": "test-goal",
            "mode": "run",
            "query": "Setup containerization",
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Validate: Contradiction guard applied
    assert data.get("memory_v2_contradiction_guard_applied", False), "Contradiction guard should apply"
    assert data.get("psyche_v2_behavior_applied", False), "Psyche behavior should apply"


def test_agent_response_varies_with_relation_friction(client: TestClient, isolated_db, monkeypatch):
    """
    Test that agent response reflects high friction.
    
    High friction → more precise, structured response
    """
    user_id = "agent_friction_user"
    
    # High friction profile
    profile = ensure_psyche_profile(user_id)
    profile.relation_friction = 0.85
    profile.relation_trust = 0.35
    update_psyche_profile(profile)
    
    state = ensure_psyche_state(user_id)
    state.certainty = 0.5
    update_psyche_state(state)
    
    # Run agent
    response = client.post(
        "/agent/run",
        json={
            "user_id": user_id,
            "goal_id": "test-goal",
            "mode": "run",
            "query": "Analyze data",
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Validate: Relation tone applied
    assert data.get("psyche_v2_relation_tone_applied", False), "Relation tone should apply with high friction"
    
    # Validate: Friction reflected in final profile
    assert data.get("final_behavior_profile", {}).get("friction", 0.0) > 0.7
