#!/usr/bin/env python3
"""
Tests for Memory V2 Service.
"""

import time

import pytest

from aihub.memory_v2_models import MemoryV2SearchRequest
from aihub.memory_v2_service import MemoryV2Service


@pytest.fixture
def service():
    """Return Memory V2 Service instance."""
    return MemoryV2Service()


def test_create_memory_item(service: MemoryV2Service):
    """Test memory item creation."""
    result = service.create_memory_item(
        user_id="user1",
        memory_type="preference",
        scope="user",
        title="Test preference",
        content="User prefers short answers",
        source_kind="chat_turn",
        importance_score=0.8,
        emotional_weight=0.5,
        confidence_score=0.9,
    )

    assert result is not None
    assert result.title == "Test preference"
    assert result.memory_type == "preference"
    assert result.salience_score > 0.0


def test_search_memory_items(service: MemoryV2Service):
    """Test memory search."""
    service.create_memory_item(
        user_id="user1",
        memory_type="fact",
        scope="user",
        title="Python fact",
        content="User knows Python",
        source_kind="chat_turn",
        importance_score=0.7,
    )

    service.create_memory_item(
        user_id="user1",
        memory_type="fact",
        scope="user",
        title="JavaScript fact",
        content="User is learning JavaScript",
        source_kind="chat_turn",
        importance_score=0.6,
    )

    req = MemoryV2SearchRequest(
        user_id="user1",
        query_text="Python",
        limit=10,
    )

    response = service.search(req)
    assert response.total_count >= 1
    assert len(response.items) >= 1


def test_get_summary(service: MemoryV2Service):
    """Test memory summary generation."""
    service.create_memory_item(
        user_id="user1",
        memory_type="preference",
        scope="user",
        title="Detail preference",
        content="User prefers detailed responses",
        source_kind="chat_turn",
        importance_score=0.8,
    )

    summary = service.get_summary("user1")
    assert summary is not None
    assert summary.total_items >= 1
    assert "preference" in summary.by_type


def test_reinforce_memory(service: MemoryV2Service):
    """Test memory reinforcement."""
    item = service.create_memory_item(
        user_id="user1",
        memory_type="fact",
        scope="user",
        title="Test fact",
        content="Test content",
        source_kind="chat_turn",
        importance_score=0.5,
    )

    original_recurrence = item.recurrence_score
    result = service.reinforce_memory(item.id, "user1")
    assert result is not None
    assert result.recurrence_score > original_recurrence
    assert result.recurrence_score > original_recurrence
