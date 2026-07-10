#!/usr/bin/env python3
"""
Tests for Memory V2 API endpoints.
"""

import pytest
from fastapi.testclient import TestClient


def test_create_memory_item(client: TestClient):
    """Test POST /memory/v2/item."""
    response = client.post(
        "/memory/v2/item",
        json={
            "user_id": "user1",
            "memory_type": "preference",
            "scope": "user",
            "title": "Test preference",
            "content": "User prefers concise answers",
            "source_kind": "chat_turn",
            "importance_score": 0.8,
            "emotional_weight": 0.5,
            "confidence_score": 0.9,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["memory_id"] is not None


def test_get_memory_summary(client: TestClient):
    """Test GET /memory/v2/summary/{user_id}."""
    client.post(
        "/memory/v2/item",
        json={
            "user_id": "user2",
            "memory_type": "fact",
            "scope": "user",
            "title": "Test fact",
            "content": "Test content",
            "source_kind": "chat_turn",
            "importance_score": 0.7,
        },
    )

    response = client.get("/memory/v2/summary/user2")
    assert response.status_code == 200

    data = response.json()
    assert "total_items" in data
    assert data["total_items"] >= 1


def test_search_memory(client: TestClient):
    """Test POST /memory/v2/search."""
    client.post(
        "/memory/v2/item",
        json={
            "user_id": "user3",
            "memory_type": "fact",
            "scope": "user",
            "title": "Python knowledge",
            "content": "User knows Python",
            "source_kind": "chat_turn",
            "importance_score": 0.8,
        },
    )

    response = client.post(
        "/memory/v2/search",
        json={
            "user_id": "user3",
            "query_text": "Python",
            "limit": 10,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) >= 1


def test_get_procedures(client: TestClient):
    """Test GET /memory/v2/procedures/{user_id}."""
    response = client.get("/memory/v2/procedures/user4?limit=10")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_contradictions(client: TestClient):
    """Test GET /memory/v2/contradictions/{user_id}."""
    response = client.get("/memory/v2/contradictions/user5?limit=20")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_memory_v2_item_archive_suppress_pin(client: TestClient):
    """POST /memory/v2/item/{archive,suppress,pin} — single-record flags."""
    uid = "user_v2_item_actions"
    create = client.post(
        "/memory/v2/item",
        json={
            "user_id": uid,
            "memory_type": "fact",
            "scope": "user",
            "title": "action test",
            "content": "content for flags",
            "source_kind": "chat_turn",
            "importance_score": 0.5,
        },
    )
    assert create.status_code == 200
    mid = create.json()["memory_id"]
    assert mid

    r_sup = client.post(
        "/memory/v2/item/suppress",
        json={"user_id": uid, "memory_id": mid, "suppressed": True},
    )
    assert r_sup.status_code == 200
    assert r_sup.json().get("ok") is True
    assert r_sup.json().get("is_suppressed") is True

    r_unsup = client.post(
        "/memory/v2/item/suppress",
        json={"user_id": uid, "memory_id": mid, "suppressed": False},
    )
    assert r_unsup.status_code == 200
    assert r_unsup.json().get("is_suppressed") is False

    r_pin = client.post(
        "/memory/v2/item/pin",
        json={"user_id": uid, "memory_id": mid, "pinned": True},
    )
    assert r_pin.status_code == 200
    assert r_pin.json().get("is_pinned") is True

    r_unpin = client.post(
        "/memory/v2/item/pin",
        json={"user_id": uid, "memory_id": mid, "pinned": False},
    )
    assert r_unpin.status_code == 200
    assert r_unpin.json().get("is_pinned") is False

    r_arch = client.post(
        "/memory/v2/item/archive",
        json={"user_id": uid, "memory_id": mid},
    )
    assert r_arch.status_code == 200
    assert r_arch.json().get("ok") is True


def test_memory_v2_item_actions_not_found(client: TestClient):
    r = client.post(
        "/memory/v2/item/suppress",
        json={
            "user_id": "no_such_user_x",
            "memory_id": "no_such_id",
            "suppressed": True,
        },
    )
    assert r.status_code == 404
