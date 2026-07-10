#!/usr/bin/env python3
"""
Tests for Cockpit Memory/Psyche V2 endpoints.
"""

import pytest
from fastapi.testclient import TestClient


def test_cockpit_memory_v2(client: TestClient):
    """Test GET /cockpit/memory-v2/{user_id}."""
    response = client.get("/cockpit/memory-v2/user1")
    assert response.status_code == 200

    data = response.json()
    assert "total_items" in data
    assert "by_type" in data
    assert "by_contradiction_state" in data
    assert "top_salient" in data


def test_cockpit_psyche_v2(client: TestClient):
    """Test GET /cockpit/psyche-v2/{user_id}."""
    response = client.get("/cockpit/psyche-v2/user2")
    assert response.status_code == 200

    data = response.json()
    assert "profile" in data
    assert "state" in data
    assert "active_rules_count" in data
    assert "recent_events_count" in data


def test_cockpit_identity(client: TestClient):
    """Test GET /cockpit/identity/{user_id}."""
    response = client.get("/cockpit/identity/user3")
    assert response.status_code == 200

    data = response.json()
    assert "user_id" in data
    assert "top_preferences" in data
    assert "top_procedures" in data
    assert "active_contradictions_count" in data
    assert "behavior_mode" in data
