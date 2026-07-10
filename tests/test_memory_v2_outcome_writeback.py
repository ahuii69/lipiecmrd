#!/usr/bin/env python3
"""Tests for Memory V2 outcome write-back logic."""

import pytest

from aihub.memory_v2_service import MemoryV2Service
from aihub.memory_v2_repository import search_memory_items
from aihub.db import init_db


@pytest.fixture
def setup_db():
    """Initialize DB for tests."""
    init_db()
    yield


def test_record_agent_outcome_success(setup_db):
    """Test recording successful agent cycle outcome."""
    service = MemoryV2Service()
    user_id = "test-agent-success"
    cycle_id = "cycle-123"

    result = service.record_agent_outcome(
        user_id=user_id,
        cycle_id=cycle_id,
        strategy="reactive",
        ok=True,
        action_summary="Executed task successfully",
        errors=[],
        goal_progress_changed=True,
        contradictions_present=0,
        procedures_active=2,
        duration_ms=1500.0,
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True
    assert result["new_items_count"] > 0
    assert result["writeback_kind"] in ["procedure_reinforcement", "progress_milestone"]

    # Verify DB write
    items = search_memory_items(user_id=user_id, limit=10)
    assert len(items) > 0
    assert any(item.source_ref == cycle_id for item in items)


def test_record_agent_outcome_failure(setup_db):
    """Test recording failed agent cycle outcome."""
    service = MemoryV2Service()
    user_id = "test-agent-failure"
    cycle_id = "cycle-456"

    result = service.record_agent_outcome(
        user_id=user_id,
        cycle_id=cycle_id,
        strategy="planned_reasoning",
        ok=False,
        action_summary="Failed to complete task",
        errors=[{"message": "Timeout error", "kind": "timeout"}],
        goal_progress_changed=False,
        contradictions_present=0,
        procedures_active=0,
        duration_ms=5000.0,
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True
    assert result["new_lessons_count"] > 0
    assert result["writeback_kind"] == "failure_lesson"

    # Verify DB write
    items = search_memory_items(user_id=user_id, memory_types=["lesson"], limit=10)
    assert len(items) > 0
    failure_items = [item for item in items if "failed" in item.content.lower()]
    assert len(failure_items) > 0


def test_record_agent_outcome_contradictions(setup_db):
    """Test recording outcome with contradictions present."""
    service = MemoryV2Service()
    user_id = "test-agent-contradictions"
    cycle_id = "cycle-789"

    result = service.record_agent_outcome(
        user_id=user_id,
        cycle_id=cycle_id,
        strategy="planned_reasoning",
        ok=False,
        action_summary="Execution failed",
        errors=[{"message": "Conflict detected"}],
        goal_progress_changed=False,
        contradictions_present=3,
        procedures_active=0,
        duration_ms=2000.0,
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True
    assert result["new_lessons_count"] > 0
    assert result["writeback_kind"] in ["failure_lesson", "contradiction_feedback"]

    # Verify contradiction-related items
    items = search_memory_items(user_id=user_id, limit=10)
    contradiction_items = [
        item for item in items if "contradiction" in item.content.lower()
    ]
    assert len(contradiction_items) > 0


def test_record_chat_outcome_web_grounded(setup_db):
    """Test recording web-grounded chat outcome."""
    service = MemoryV2Service()
    user_id = "test-chat-web"
    turn_id = "turn-123"

    result = service.record_chat_outcome(
        user_id=user_id,
        turn_id=turn_id,
        query_text="What is the weather?",
        response_text="The weather is sunny.",
        strategy="contextual",
        grounding_mode="controlled_web",
        tool_calls_count=1,
        tool_successes=1,
        tool_failures=0,
        contradictions_present=0,
        memory_matches=0,
        degraded=False,
        fallback=False,
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True
    assert result["new_items_count"] > 0
    assert result["writeback_kind"] == "web_grounded"

    # Verify DB write
    items = search_memory_items(user_id=user_id, memory_types=["fact"], limit=10)
    assert len(items) > 0
    assert any("grounded" in item.content.lower() for item in items)


def test_record_chat_outcome_degraded(setup_db):
    """Test recording degraded chat outcome."""
    service = MemoryV2Service()
    user_id = "test-chat-degraded"
    turn_id = "turn-456"

    result = service.record_chat_outcome(
        user_id=user_id,
        turn_id=turn_id,
        query_text="Complex question",
        response_text="I'm not sure.",
        strategy="instant",
        grounding_mode="none",
        tool_calls_count=0,
        tool_successes=0,
        tool_failures=0,
        contradictions_present=0,
        memory_matches=0,
        degraded=True,
        fallback=False,
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True
    assert result["new_lessons_count"] > 0
    assert result["writeback_kind"] == "degraded_outcome"

    # Verify lesson recorded
    items = search_memory_items(user_id=user_id, memory_types=["lesson"], limit=10)
    assert len(items) > 0
    assert any("degraded" in item.content.lower() for item in items)


def test_record_chat_outcome_memory_reinforcement(setup_db):
    """Test memory reinforcement on successful outcome."""
    service = MemoryV2Service()
    user_id = "test-chat-memory-boost"
    turn_id = "turn-789"

    result = service.record_chat_outcome(
        user_id=user_id,
        turn_id=turn_id,
        query_text="Standard question",
        response_text="Standard answer",
        strategy="contextual",
        grounding_mode="none",
        tool_calls_count=0,
        tool_successes=0,
        tool_failures=0,
        contradictions_present=0,
        memory_matches=5,
        degraded=False,
        fallback=False,
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True
    assert result["new_items_count"] > 0
    assert result["writeback_kind"] == "memory_reinforcement"

    # Verify preference item
    items = search_memory_items(user_id=user_id, memory_types=["preference"], limit=10)
    assert len(items) > 0
    assert any("context" in item.content.lower() or "memories" in item.content.lower() for item in items)


def test_record_chat_outcome_fallback(setup_db):
    """Test recording fallback outcome."""
    service = MemoryV2Service()
    user_id = "test-chat-fallback"
    turn_id = "turn-999"

    result = service.record_chat_outcome(
        user_id=user_id,
        turn_id=turn_id,
        query_text="Failed query",
        response_text="",
        strategy="instant",
        grounding_mode="none",
        tool_calls_count=0,
        tool_successes=0,
        tool_failures=0,
        contradictions_present=0,
        memory_matches=0,
        degraded=False,
        fallback=True,
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True
    assert result["new_lessons_count"] > 0
    assert result["writeback_kind"] == "degraded_outcome"
