#!/usr/bin/env python3
"""
Tests for Memory V2 consolidation.
"""

import time

import pytest

from aihub.memory_v2_consolidation import consolidate_episodic_memories
from aihub.memory_v2_models import MemoryV2Item
from aihub.memory_v2_repository import insert_memory_item


def test_consolidate_episodic_memories():
    """Test consolidation of episodic memories."""
    now = time.time()

    items = [
        MemoryV2Item(
            id=f"episode-{i}",
            user_id="user1",
            memory_type="fact",
            scope="session",
            title=f"Chat episode {i}",
            content=f"User discussed topic {i}",
            summary=f"Topic {i}",
            source_kind="chat_turn",
            salience_score=0.4,
            created_ts=now - (i * 100),
            updated_ts=now - (i * 100),
        )
        for i in range(3)
    ]

    for item in items:
        insert_memory_item(item)

    result = consolidate_episodic_memories(
        user_id="user1",
        scope="session",
    )

    assert result is not None
    assert result.output_memory_id is not None
    assert len(result.input_memory_ids) >= 3
    assert len(result.input_memory_ids) >= 3
