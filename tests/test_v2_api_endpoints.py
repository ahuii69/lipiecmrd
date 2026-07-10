#!/usr/bin/env python3
"""
Tests for new Memory V2 and Psyche V2 API endpoints: forgetting, habits, relations.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    """Disable API key auth for tests."""
    from aihub import main
    monkeypatch.setenv("API_KEY", "")


def test_memory_v2_forgetting_endpoint(client: TestClient, isolated_db):
    """Test /memory/v2/forgetting/{user_id} endpoint."""
    user_id = "user-forgetting-api-test"
    
    # Create some memories first
    client.post(
        "/memory/v2/item",
        json={
            "user_id": user_id,
            "memory_type": "fact",
            "scope": "domain",
            "title": "Test fact",
            "content": "Test content",
            "source_kind": "chat_turn",
            "importance_score": 0.2,
        },
    )
    
    # Run forgetting sweep
    response = client.post(f"/memory/v2/forgetting/{user_id}?threshold=0.15")
    
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "evaluated_count" in data
    assert "suppressed_count" in data
    assert "threshold" in data


def test_memory_v2_retrieval_explanation_endpoint(client: TestClient, isolated_db):
    """Test /memory/v2/retrieval-explain/{user_id} endpoint."""
    user_id = "user-retrieval-api-test"
    
    # Create memory
    client.post(
        "/memory/v2/item",
        json={
            "user_id": user_id,
            "memory_type": "preference",
            "scope": "user",
            "title": "User preference",
            "content": "Preference content",
            "source_kind": "chat_turn",
            "importance_score": 0.7,
        },
    )
    
    # Get retrieval explanation
    response = client.get(f"/memory/v2/retrieval-explain/{user_id}?query=preference")
    
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == user_id
    assert "top_reason_codes" in data
    assert "match_count" in data
    assert "reinforced_count" in data
    assert "suppressed_count" in data
    assert "retrieval_strategy" in data


def test_psyche_v2_habits_endpoint(client: TestClient, isolated_db):
    """Test /psyche/v2/habits/{user_id} endpoint."""
    user_id = "user-habits-api-test"
    
    # Create habit via service
    from aihub.psyche_v2_service import PsycheV2Service
    service = PsycheV2Service()
    service.reinforce_habit(
        user_id=user_id,
        habit_name="test_habit",
        habit_type="test_type",
        context={"test": "context"},
    )
    
    # Get habits
    response = client.get(f"/psyche/v2/habits/{user_id}?min_intensity=0.0")
    
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == user_id
    assert "habits" in data
    assert "total_count" in data
    assert data["total_count"] >= 1


def test_psyche_v2_relations_endpoint(client: TestClient, isolated_db):
    """Test /psyche/v2/relations/{user_id} endpoint."""
    user_id = "user-relations-api-test"
    
    # Ensure user
    from aihub.psyche_v2_service import PsycheV2Service
    service = PsycheV2Service()
    service.ensure_user(user_id)
    
    # Get relations
    response = client.get(f"/psyche/v2/relations/{user_id}")
    
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == user_id
    assert "trust" in data
    assert "friction" in data
    assert "warmth" in data
    assert "directness_tolerance" in data
    assert "collaboration_confidence" in data
    assert 0.0 <= data["trust"] <= 1.0


def test_cockpit_memory_v2_retrieval_endpoint(client: TestClient, isolated_db):
    """Test /cockpit/memory-v2/retrieval/{user_id} endpoint."""
    user_id = "user-cockpit-retrieval-test"
    
    # Create memory
    client.post(
        "/memory/v2/item",
        json={
            "user_id": user_id,
            "memory_type": "procedural",
            "scope": "workflow",
            "title": "Workflow pattern",
            "content": "Pattern details",
            "source_kind": "agent_cycle",
        },
    )
    
    # Get cockpit retrieval
    response = client.get(f"/cockpit/memory-v2/retrieval/{user_id}?query=workflow")
    
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == user_id
    assert "top_reason_codes" in data
    assert "retrieval_strategy" in data
    assert "top_items" in data


def test_cockpit_psyche_v2_habits_endpoint(client: TestClient, isolated_db):
    """Test /cockpit/psyche-v2/habits/{user_id} endpoint."""
    user_id = "user-cockpit-habits-test"
    
    # Create habit
    from aihub.psyche_v2_service import PsycheV2Service
    service = PsycheV2Service()
    service.reinforce_habit(
        user_id=user_id,
        habit_name="habit_test",
        habit_type="test",
        context={},
    )
    
    # Get cockpit habits
    response = client.get(f"/cockpit/psyche-v2/habits/{user_id}")
    
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == user_id
    assert "habits" in data
    assert "total_count" in data


def test_cockpit_psyche_v2_relations_endpoint(client: TestClient, isolated_db):
    """Test /cockpit/psyche-v2/relations/{user_id} endpoint."""
    user_id = "user-cockpit-relations-test"
    
    # Ensure user
    from aihub.psyche_v2_service import PsycheV2Service
    service = PsycheV2Service()
    service.ensure_user(user_id)
    
    # Get cockpit relations
    response = client.get(f"/cockpit/psyche-v2/relations/{user_id}")
    
    assert response.status_code == 200
    data = response.json()
    assert "trust" in data
    assert "friction" in data
    assert "warmth" in data
