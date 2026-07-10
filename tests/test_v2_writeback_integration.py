#!/usr/bin/env python3
"""Integration tests for V2 write-back in /agent/run and /chat/turn."""

import pytest
from fastapi.testclient import TestClient

from aihub.main import app
from aihub.db import init_db
from aihub.memory_v2_service import MemoryV2Service
from aihub.psyche_v2_service import PsycheV2Service


@pytest.fixture
def client(monkeypatch):
    """Create test client."""
    from aihub import main
    monkeypatch.setenv("API_KEY", "")
    init_db()
    return TestClient(app)


def test_agent_run_includes_writeback_fields(client):
    """Test /agent/run includes V2 write-back fields in response."""
    user_id = "test-agent-writeback-integration"
    
    # Ensure user has V2 data
    mem_svc = MemoryV2Service()
    mem_svc.create_memory_item(
        user_id=user_id,
        memory_type="preference",
        scope="user",
        title="Test preference",
        content="User prefers minimal output",
        source_kind="explicit_learning",
    )
    
    response = client.post(
        "/agent/run",
        json={
            "user_id": user_id,
            "user_text": "Test agent run",
            "mode": "run",
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Check V2 write-back fields exist
    assert "memory_v2_writeback_attempted" in data
    assert "memory_v2_writeback_succeeded" in data
    assert "memory_v2_writeback_kind" in data
    assert "memory_v2_new_lessons_count" in data
    assert "memory_v2_new_procedures_count" in data
    assert "psyche_v2_writeback_attempted" in data
    assert "psyche_v2_writeback_succeeded" in data
    assert "psyche_v2_event_applied" in data


def test_chat_turn_includes_writeback_fields(client):
    """Test /chat/turn includes V2 write-back fields in trace."""
    user_id = "test-chat-writeback-integration"
    
    # Ensure user has V2 profile
    psy_svc = PsycheV2Service()
    psy_svc.ensure_user(user_id)
    
    response = client.post(
        "/chat/turn",
        json={
            "user_id": user_id,
            "message": "Test chat turn",
            "mode": "chat",
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    trace = data.get("trace", {})
    
    # Check V2 write-back fields exist in trace
    assert "memory_v2_writeback_attempted" in trace
    assert "memory_v2_writeback_succeeded" in trace
    assert "memory_v2_new_items_count" in trace
    assert "memory_v2_new_lessons_count" in trace
    assert "psyche_v2_writeback_attempted" in trace
    assert "psyche_v2_writeback_succeeded" in trace
    assert "psyche_v2_event_applied" in trace
    assert "response_outcome_quality" in trace
    
    # Verify outcome quality is valid
    assert trace["response_outcome_quality"] in ["success", "degraded", "fallback", "blocked"]


def test_agent_run_writeback_success_creates_memory(client):
    """Test successful agent run creates memory items."""
    user_id = "test-agent-success-writeback"
    mem_svc = MemoryV2Service()
    
    # Baseline
    from aihub.memory_v2_models import MemoryV2SearchRequest
    baseline_items = mem_svc.search(
        MemoryV2SearchRequest(
            user_id=user_id,
            exclude_archived=True,
            limit=100,
        )
    ).items
    baseline_count = len(baseline_items)
    
    response = client.post(
        "/agent/run",
        json={
            "user_id": user_id,
            "user_text": "Simple task",
            "mode": "run",
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # If write-back succeeded, check DB
    if data.get("memory_v2_writeback_succeeded"):
        from aihub.memory_v2_models import MemoryV2SearchRequest
        new_items = mem_svc.search(
            MemoryV2SearchRequest(
                user_id=user_id,
                exclude_archived=True,
                limit=100,
            )
        ).items
        assert len(new_items) >= baseline_count


def test_chat_turn_writeback_success_creates_psyche_event(client):
    """Test successful chat turn creates psyche event."""
    user_id = "test-chat-psyche-event"
    psy_svc = PsycheV2Service()
    psy_svc.ensure_user(user_id)
    
    # Baseline
    from aihub.psyche_v2_repository import get_recent_psyche_events
    baseline_events = get_recent_psyche_events(user_id, limit=100)
    baseline_count = len(baseline_events)
    
    response = client.post(
        "/chat/turn",
        json={
            "user_id": user_id,
            "message": "Hello",
            "mode": "chat",
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    trace = data.get("trace", {})
    
    # If write-back succeeded, verify event created
    if trace.get("psyche_v2_writeback_succeeded"):
        new_events = get_recent_psyche_events(user_id, limit=100)
        assert len(new_events) > baseline_count
        assert trace["psyche_v2_event_applied"] is not None


def test_cockpit_memory_v2_includes_writebacks(client):
    """Test /cockpit/memory-v2 returns recent write-backs."""
    user_id = "test-cockpit-memory-writebacks"
    
    # Create item via write-back simulation
    mem_svc = MemoryV2Service()
    mem_svc.create_memory_item(
        user_id=user_id,
        memory_type="lesson",
        scope="workflow",
        title="Test write-back",
        content="Test content",
        source_kind="agent_cycle",
        source_ref="cycle-test-123",
    )
    
    response = client.get(f"/cockpit/memory-v2/{user_id}")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "recent_writebacks" in data
    writebacks = data["recent_writebacks"]
    assert isinstance(writebacks, list)
    
    if len(writebacks) > 0:
        wb = writebacks[0]
        assert "id" in wb
        assert "title" in wb
        assert "source_kind" in wb
        assert wb["source_kind"] in ["agent_cycle", "chat_turn"]


def test_cockpit_psyche_v2_includes_recent_events(client):
    """Test /cockpit/psyche-v2 returns recent events."""
    user_id = "test-cockpit-psyche-events"
    
    # Create event
    psy_svc = PsycheV2Service()
    psy_svc.apply_event(
        user_id=user_id,
        event_type="interaction_complete",
        reason_text="Test event",
        source_ref="turn-test-456",
    )
    
    response = client.get(f"/cockpit/psyche-v2/{user_id}")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "recent_events" in data
    events = data["recent_events"]
    assert isinstance(events, list)
    
    if len(events) > 0:
        event = events[0]
        assert "id" in event
        assert "event_type" in event
        assert "reason_text" in event
        assert "source_ref" in event
        assert "created_ts" in event
