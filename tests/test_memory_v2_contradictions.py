#!/usr/bin/env python3
"""
Tests for Memory V2 contradiction detection.
"""

import time

import pytest

from aihub.memory_v2_contradictions import (
    detect_contradictions,
    resolve_contradiction_supersede,
)
from aihub.memory_v2_models import MemoryV2Item
from aihub.memory_v2_repository import insert_memory_item


def test_detect_preference_conflict():
    """Test preference contradiction detection."""
    now = time.time()

    item1 = MemoryV2Item(
        id="pref-1",
        user_id="user1",
        memory_type="preference",
        scope="user",
        title="Prefers short answers",
        content="User prefers concise responses",
        summary="Short answers",
        source_kind="chat_turn",
        salience_score=0.6,
        confidence_score=0.8,
        created_ts=now - 1000,
        updated_ts=now - 1000,
    )
    insert_memory_item(item1)

    item2 = MemoryV2Item(
        id="pref-2",
        user_id="user1",
        memory_type="preference",
        scope="user",
        title="Prefers detailed answers",
        content="User prefers comprehensive detailed responses",
        summary="Detailed answers",
        source_kind="chat_turn",
        salience_score=0.7,
        confidence_score=0.9,
        created_ts=now,
        updated_ts=now,
    )

    contradictions = detect_contradictions("user1", item2)
    assert len(contradictions) >= 1

    found = any(c.to_memory_id == "pref-1" for c in contradictions)
    assert found


def test_resolve_contradiction_supersede():
    """Test contradiction resolution."""
    now = time.time()

    old_item = MemoryV2Item(
        id="old-fact",
        user_id="user1",
        memory_type="fact",
        scope="user",
        title="Old fact",
        content="Old content",
        summary="Old",
        source_kind="chat_turn",
        confidence_score=0.6,
        created_ts=now - 1000,
        updated_ts=now - 1000,
    )
    insert_memory_item(old_item)

    new_item = MemoryV2Item(
        id="new-fact",
        user_id="user1",
        memory_type="fact",
        scope="user",
        title="New fact",
        content="New content",
        summary="New",
        source_kind="chat_turn",
        confidence_score=0.9,
        created_ts=now,
        updated_ts=now,
    )

    result = resolve_contradiction_supersede(
        newer_memory_id="new-fact",
        older_memory_id="old-fact",
        user_id="user1",
    )

    assert result is True
    assert result is True
