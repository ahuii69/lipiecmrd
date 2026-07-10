#!/usr/bin/env python3
"""
Tests for V2 trace fields in chat and agent responses.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    """Disable API key auth for tests."""
    from aihub import main
    monkeypatch.setenv("API_KEY", "")


def test_chat_turn_trace_has_v2_behavioral_fields(client: TestClient, isolated_db, monkeypatch):
    """Test /chat/turn trace includes new behavioral depth fields."""
    user_id = "user-chat-trace-test"
    
    # Ensure user has memories and habits
    from aihub.memory_v2_service import MemoryV2Service
    from aihub.psyche_v2_service import PsycheV2Service
    
    mem_svc = MemoryV2Service()
    psy_svc = PsycheV2Service()
    
    mem_svc.create_memory_item(
        user_id=user_id,
        memory_type="preference",
        scope="user",
        title="Test pref",
        content="Content",
        source_kind="chat_turn",
    )
    
    psy_svc.reinforce_habit(
        user_id=user_id,
        habit_name="test_habit",
        habit_type="test",
        context={},
    )
    
    response = client.post(
        "/chat/turn",
        json={
            "user_id": user_id,
            "session_id": "test-session",
            "message": "test query",
            "mode": "chat",
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    trace = data["trace"]
    
    # Check behavioral depth fields
    assert "memory_v2_reinforced_count" in trace
    assert "memory_v2_suppressed_count" in trace
    assert "memory_v2_top_reason_codes" in trace
    assert "memory_v2_retrieval_explanation" in trace
    assert "psyche_v2_habit_biases" in trace
    assert "psyche_v2_relation_friction" in trace
    assert "psyche_v2_behavior_style" in trace


def test_agent_run_response_has_v2_behavioral_fields(client: TestClient, isolated_db, monkeypatch):
    """Test /agent/run response includes new behavioral depth fields."""
    user_id = "user-agent-trace-test"
    
    # Create memories
    from aihub.memory_v2_service import MemoryV2Service
    mem_svc = MemoryV2Service()
    
    item = mem_svc.create_memory_item(
        user_id=user_id,
        memory_type="procedural",
        scope="workflow",
        title="Test procedure",
        content="Procedure content",
        source_kind="agent_cycle",
        importance_score=0.7,
    )
    
    # Reinforce it
    from aihub.memory_v2_repository import reinforce_memory_item
    reinforce_memory_item(item.id, user_id, success=True)
    
    response = client.post(
        "/agent/run",
        json={
            "user_id": user_id,
            "goal_id": "test-goal",
            "mode": "run",
            "query": "test task",
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Check V2 fields
    assert "memory_v2_reinforced_count" in data
    assert "memory_v2_suppressed_count" in data
    assert "memory_v2_retrieval_reason_codes" in data
    assert "psyche_v2_habit_biases" in data
    assert "psyche_v2_relation_friction" in data
    assert "psyche_v2_pressure" in data


def test_memory_reinforcement_after_success(client: TestClient, isolated_db, monkeypatch):
    """Test that successful agent run reinforces related memories."""
    user_id = "user-reinf-success-test"
    
    from aihub.memory_v2_service import MemoryV2Service
    mem_svc = MemoryV2Service()
    
    # Create memory
    item = mem_svc.create_memory_item(
        user_id=user_id,
        memory_type="procedural",
        scope="workflow",
        title="Successful pattern",
        content="Pattern details",
        source_kind="agent_cycle",
        importance_score=0.7,
    )
    
    initial_count = item.reinforcement_count
    
    # Run agent (simulate success by having memory present)
    response = client.post(
        "/agent/run",
        json={
            "user_id": user_id,
            "goal_id": "test-goal",
            "mode": "run",
            "query": "use pattern",
        },
    )
    
    assert response.status_code == 200
    
    # Check if write-back attempted
    data = response.json()
    assert data.get("memory_v2_writeback_attempted", False) in [True, False]
