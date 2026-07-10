#!/usr/bin/env python3
"""
Smoke tests for calibration endpoint.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    """Disable API key auth for tests."""
    from aihub import main
    monkeypatch.setenv("API_KEY", "")


def test_calibration_endpoint_smoke(client: TestClient, isolated_db):
    """Test that calibration endpoint returns expected structure."""
    response = client.get("/cockpit/calibration/smoke_user?query=test")
    
    assert response.status_code == 200
    data = response.json()
    
    # Validate structure
    assert "user_id" in data
    assert "query" in data
    assert "active_thresholds" in data
    assert "applied_behavior_rules" in data
    assert "promoted_memory_items" in data
    assert "psyche_biases" in data
    assert "memory_context_loaded" in data
    assert "psyche_context_loaded" in data
    assert "contradiction_count" in data
    assert "procedure_confidence" in data
    
    # Validate types
    assert isinstance(data["active_thresholds"], dict)
    assert isinstance(data["applied_behavior_rules"], list)
    assert isinstance(data["promoted_memory_items"], list)
    assert isinstance(data["psyche_biases"], dict)
    assert isinstance(data["memory_context_loaded"], bool)
    assert isinstance(data["psyche_context_loaded"], bool)
    
    print(f"Calibration endpoint OK: {len(data['applied_behavior_rules'])} rules, {data['contradiction_count']} contradictions")
