#!/usr/bin/env python3
"""
Tests for Psyche V2 API endpoints.
"""

import pytest
from fastapi.testclient import TestClient


def test_get_psyche_snapshot(client: TestClient):
    """Test GET /psyche/v2/{user_id}."""
    response = client.get("/psyche/v2/user1")
    assert response.status_code == 200

    data = response.json()
    assert "profile" in data
    assert "state" in data
    assert data["profile"]["user_id"] == "user1"


def test_apply_event(client: TestClient):
    """Test POST /psyche/v2/event."""
    response = client.post(
        "/psyche/v2/event",
        json={
            "user_id": "user2",
            "event_type": "tool_success",
            "reason_text": "Task completed",
            "signal_strength": 0.3,
            "metadata": {"task": "test"},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["event_id"] is not None


def test_reflect_user(client: TestClient):
    """Test POST /psyche/v2/reflect/{user_id}."""
    client.post(
        "/psyche/v2/event",
        json={
            "user_id": "user3",
            "event_type": "tool_success",
            "reason_text": "Test success",
            "signal_strength": 0.3,
        },
    )

    response = client.post("/psyche/v2/reflect/user3")
    assert response.status_code == 200

    data = response.json()
    assert "events_analyzed" in data
    assert data["events_analyzed"] >= 0


def test_get_policy(client: TestClient):
    """Test GET /psyche/v2/policy/{user_id}."""
    response = client.get("/psyche/v2/policy/user4")
    assert response.status_code == 200

    data = response.json()
    assert "directness" in data
    assert "verbosity" in data


def test_get_history(client: TestClient):
    """Test GET /psyche/v2/history/{user_id}."""
    response = client.get("/psyche/v2/history/user5?limit=10")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
