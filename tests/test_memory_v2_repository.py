#!/usr/bin/env python3
"""
Tests for Memory V2 Repository.
"""

import time

import pytest

from aihub.memory_v2_models import MemoryV2Item, MemoryV2Link, MemoryV2Procedure
from aihub.memory_v2_repository import (
    count_memory_items,
    get_memory_item,
    get_memory_links_from,
    get_procedures_for_user,
    get_top_salient_memories,
    insert_memory_item,
    insert_memory_link,
    insert_memory_procedure,
    search_memory_items,
    update_memory_item,
)


def test_insert_and_get_memory_item():
    """Test basic insert and retrieval."""
    now = time.time()
    item = MemoryV2Item(
        id="mem-test-1",
        user_id="user1",
        memory_type="preference",
        scope="user",
        title="Test preference",
        content="User prefers detailed responses",
        summary="Detailed responses preferred",
        source_kind="chat_turn",
        importance_score=0.8,
        salience_score=0.75,
        confidence_score=0.9,
        freshness_score=1.0,
        identity_relevance_score=0.85,
        created_ts=now,
        updated_ts=now,
    )

    assert insert_memory_item(item)

    retrieved = get_memory_item("mem-test-1", "user1")
    assert retrieved is not None
    assert retrieved.title == "Test preference"
    assert retrieved.memory_type == "preference"
    assert retrieved.salience_score == 0.75


def test_search_memory_items():
    """Test memory search with filters."""
    now = time.time()

    items = [
        MemoryV2Item(
            id=f"mem-search-{i}",
            user_id="user1",
            memory_type="fact",
            scope="user",
            title=f"Fact {i}",
            content=f"Content about topic {i}",
            summary=f"Summary {i}",
            source_kind="chat_turn",
            salience_score=0.5 + (i * 0.1),
            created_ts=now,
            updated_ts=now,
        )
        for i in range(3)
    ]

    for item in items:
        insert_memory_item(item)

    results = search_memory_items(
        user_id="user1",
        memory_types=["fact"],
        min_salience=0.6,
        limit=10,
    )

    assert len(results) == 2
    assert results[0].salience_score >= 0.6


def test_count_memory_items():
    """Test counting memories by type."""
    now = time.time()

    insert_memory_item(
        MemoryV2Item(
            id="mem-count-1",
            user_id="user1",
            memory_type="preference",
            scope="user",
            title="Pref 1",
            content="Content",
            summary="Summary",
            source_kind="chat_turn",
            created_ts=now,
            updated_ts=now,
        )
    )

    count_all = count_memory_items("user1")
    count_pref = count_memory_items("user1", memory_type="preference")

    assert count_all >= 1
    assert count_pref >= 1


def test_insert_memory_link():
    """Test memory link creation."""
    now = time.time()

    link = MemoryV2Link(
        id="link-test-1",
        user_id="user1",
        from_memory_id="mem-1",
        to_memory_id="mem-2",
        link_type="supersedes",
        weight=1.0,
        created_ts=now,
    )

    assert insert_memory_link(link)

    links = get_memory_links_from("mem-1", "user1")
    assert len(links) >= 1
    assert links[0].link_type == "supersedes"


def test_insert_procedure():
    """Test procedure insertion."""
    now = time.time()

    procedure = MemoryV2Procedure(
        id="proc-test-1",
        user_id="user1",
        name="Test workflow",
        trigger_pattern="query analysis",
        recommended_strategy="instant",
        recommended_tools=["web_search"],
        avoid_patterns=["complex_planning"],
        success_rate=0.8,
        confidence_score=0.75,
        evidence_count=5,
        created_ts=now,
        updated_ts=now,
    )

    assert insert_memory_procedure(procedure)

    procedures = get_procedures_for_user("user1", limit=10)
    assert len(procedures) >= 1
    assert procedures[0].name == "Test workflow"
    assert procedures[0].name == "Test workflow"
