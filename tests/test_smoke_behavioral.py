#!/usr/bin/env python3
"""
Smoke tests for behavioral endpoints.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _disable_hub_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "")


@pytest.fixture
def client():
    from aihub.main import app

    return TestClient(app)


def test_smoke_system_ping(client):
    """Smoke: /system/ping."""
    response = client.get("/system/ping")
    assert response.status_code == 200


def test_smoke_memory_v2_summary(client):
    """Smoke: /memory/v2/summary/{user_id}."""
    response = client.get("/memory/v2/summary/test-user")
    assert response.status_code == 200


def test_smoke_memory_v2_forgetting(client):
    """Smoke: /memory/v2/forgetting/{user_id}."""
    response = client.post("/memory/v2/forgetting/test-user?threshold=0.15")
    assert response.status_code == 200


def test_smoke_memory_v2_retrieval_explain(client):
    """Smoke: /memory/v2/retrieval-explain/{user_id}."""
    response = client.get("/memory/v2/retrieval-explain/test-user?query=test")
    assert response.status_code == 200


def test_smoke_psyche_v2_snapshot(client):
    """Smoke: /psyche/v2/{user_id}."""
    response = client.get("/psyche/v2/test-user")
    assert response.status_code == 200


def test_smoke_psyche_v2_habits(client):
    """Smoke: /psyche/v2/habits/{user_id}."""
    response = client.get("/psyche/v2/habits/test-user")
    assert response.status_code == 200


def test_smoke_psyche_v2_relations(client):
    """Smoke: /psyche/v2/relations/{user_id}."""
    response = client.get("/psyche/v2/relations/test-user")
    assert response.status_code == 200


def test_smoke_cockpit_memory_v2(client):
    """Smoke: /cockpit/memory-v2/{user_id}."""
    response = client.get("/cockpit/memory-v2/test-user")
    assert response.status_code == 200


def test_smoke_cockpit_psyche_v2(client):
    """Smoke: /cockpit/psyche-v2/{user_id}."""
    response = client.get("/cockpit/psyche-v2/test-user")
    assert response.status_code == 200


def test_smoke_cockpit_identity(client):
    """Smoke: /cockpit/identity/{user_id}."""
    response = client.get("/cockpit/identity/test-user")
    assert response.status_code == 200


def test_smoke_cockpit_memory_v2_retrieval(client):
    """Smoke: /cockpit/memory-v2/retrieval/{user_id}."""
    response = client.get("/cockpit/memory-v2/retrieval/test-user?query=test")
    assert response.status_code == 200


def test_smoke_cockpit_psyche_v2_habits(client):
    """Smoke: /cockpit/psyche-v2/habits/{user_id}."""
    response = client.get("/cockpit/psyche-v2/habits/test-user")
    assert response.status_code == 200


def test_smoke_cockpit_psyche_v2_relations(client):
    """Smoke: /cockpit/psyche-v2/relations/{user_id}."""
    response = client.get("/cockpit/psyche-v2/relations/test-user")
    assert response.status_code == 200


if __name__ == "__main__":
    import os

    os.environ["API_KEY"] = ""
    from aihub.main import app

    _client = TestClient(app)
    print("Running smoke tests...")
    test_smoke_system_ping(_client)
    test_smoke_memory_v2_summary(_client)
    test_smoke_memory_v2_forgetting(_client)
    test_smoke_memory_v2_retrieval_explain(_client)
    test_smoke_psyche_v2_snapshot(_client)
    test_smoke_psyche_v2_habits(_client)
    test_smoke_psyche_v2_relations(_client)
    test_smoke_cockpit_memory_v2(_client)
    test_smoke_cockpit_psyche_v2(_client)
    test_smoke_cockpit_identity(_client)
    test_smoke_cockpit_memory_v2_retrieval(_client)
    test_smoke_cockpit_psyche_v2_habits(_client)
    test_smoke_cockpit_psyche_v2_relations(_client)
    print("All smoke tests passed!")
