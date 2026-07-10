#!/usr/bin/env python3
"""
Tests for runtime V2 bridges.
"""

import time

import pytest

from aihub.memory_v2_service import MemoryV2Service
from aihub.psyche_core import get_psyche_core
from aihub.runtime_identity_bridge import build_identity_bridge_snapshot
from aihub.runtime_memory_bridge import (
    build_memory_v2_runtime_snapshot,
    summarize_memory_v2_for_agent,
    summarize_memory_v2_for_chat,
)
from aihub.runtime_psyche_bridge import (
    build_psyche_v2_runtime_snapshot,
    summarize_psyche_v2_for_agent,
    summarize_psyche_v2_for_chat,
)


def test_build_memory_v2_runtime_snapshot():
    """Test memory runtime snapshot creation."""
    snapshot = build_memory_v2_runtime_snapshot("user1", "test query")
    assert "loaded" in snapshot
    assert "match_count" in snapshot
    assert "contradictions_count" in snapshot


def test_summarize_memory_v2_for_chat():
    """Test memory summary for chat."""
    service = MemoryV2Service()
    service.create_memory_item(
        user_id="user2",
        memory_type="preference",
        scope="user",
        title="Test pref",
        content="User prefers detailed answers",
        source_kind="chat_turn",
        importance_score=0.8,
    )

    summary = summarize_memory_v2_for_chat("user2", "detailed")
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_summarize_memory_v2_for_agent():
    """Test memory summary for agent."""
    snapshot = summarize_memory_v2_for_agent("user3", "test task")
    assert "available" in snapshot
    assert isinstance(snapshot, dict)


def test_build_psyche_v2_runtime_snapshot():
    """Test psyche runtime snapshot creation."""
    snapshot = build_psyche_v2_runtime_snapshot("user4")
    assert "loaded" in snapshot
    assert "mode" in snapshot


def test_summarize_psyche_v2_for_chat():
    """Test psyche summary for chat."""
    service = get_psyche_core().v2_service
    service.ensure_user("user5")

    summary = summarize_psyche_v2_for_chat("user5")
    assert isinstance(summary, str)


def test_summarize_psyche_v2_for_agent():
    """Test psyche summary for agent."""
    snapshot = summarize_psyche_v2_for_agent("user6")
    assert "available" in snapshot
    assert isinstance(snapshot, dict)


def test_build_identity_bridge_snapshot():
    """Test identity bridge snapshot."""
    memory_service = MemoryV2Service()
    psyche_service = get_psyche_core().v2_service

    psyche_service.ensure_user("user7")
    memory_service.create_memory_item(
        user_id="user7",
        memory_type="preference",
        scope="user",
        title="Identity test",
        content="User prefers fast responses",
        source_kind="chat_turn",
        importance_score=0.9,
    )

    snapshot = build_identity_bridge_snapshot("user7", "test query")
    assert snapshot is not None
    assert snapshot.user_id == "user7"
    assert "top_preferences" in snapshot.__dict__
