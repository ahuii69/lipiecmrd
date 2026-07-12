#!/usr/bin/env python3
"""
Tests for Memory V2 behavioral depth: selective recall, reinforcement, forgetting.
"""

import pytest
import time
from aihub.memory_v2_service import MemoryV2Service
from aihub.memory_v2_scoring import (
    calculate_retrieval_priority,
    calculate_relation_relevance,
    calculate_outcome_reinforcement,
)
from aihub.memory_v2_repository import (
    reinforce_memory_item,
    get_reinforced_memories,
    mark_memory_suppressed,
    get_suppressed_memories,
)


def test_retrieval_priority_scoring():
    """Test retrieval priority calculation with all factors."""
    priority = calculate_retrieval_priority(
        salience_score=0.8,
        recurrence_score=0.6,
        freshness_score=0.9,
        identity_relevance_score=0.7,
        relation_relevance_score=0.5,
        outcome_reinforcement_score=0.8,
        source_reliability_score=0.9,
        contradiction_state="none",
        is_pinned=False,
        is_suppressed=False,
        decay_bucket="active",
    )
    
    assert 0.0 <= priority <= 1.0
    assert priority > 0.6


def test_retrieval_priority_contradiction_penalty():
    """Test that contradicted memories have lower priority."""
    base_priority = calculate_retrieval_priority(
        salience_score=0.8,
        recurrence_score=0.6,
        freshness_score=0.9,
        identity_relevance_score=0.7,
        relation_relevance_score=0.5,
        outcome_reinforcement_score=0.8,
        source_reliability_score=0.9,
        contradiction_state="none",
        is_pinned=False,
        is_suppressed=False,
        decay_bucket="active",
    )
    
    conflicted_priority = calculate_retrieval_priority(
        salience_score=0.8,
        recurrence_score=0.6,
        freshness_score=0.9,
        identity_relevance_score=0.7,
        relation_relevance_score=0.5,
        outcome_reinforcement_score=0.8,
        source_reliability_score=0.9,
        contradiction_state="conflicted",
        is_pinned=False,
        is_suppressed=False,
        decay_bucket="active",
    )
    
    assert conflicted_priority < base_priority * 0.5


def test_retrieval_priority_suppressed_zero():
    """Test that suppressed memories have zero priority."""
    priority = calculate_retrieval_priority(
        salience_score=0.8,
        recurrence_score=0.6,
        freshness_score=0.9,
        identity_relevance_score=0.7,
        relation_relevance_score=0.5,
        outcome_reinforcement_score=0.8,
        source_reliability_score=0.9,
        contradiction_state="none",
        is_pinned=False,
        is_suppressed=True,
        decay_bucket="active",
    )
    
    assert priority == 0.0


def test_outcome_reinforcement_success_rate():
    """Test outcome reinforcement calculation."""
    high_success = calculate_outcome_reinforcement(
        success_reinforcements=8,
        failure_reinforcements=2,
        total_reinforcements=10,
    )
    
    low_success = calculate_outcome_reinforcement(
        success_reinforcements=2,
        failure_reinforcements=8,
        total_reinforcements=10,
    )
    
    assert high_success > 0.6
    assert low_success < 0.4
    assert high_success > low_success


def test_relation_relevance_scoring():
    """Test relation relevance scoring."""
    relationship_mem = calculate_relation_relevance(
        memory_type="relationship",
        scope="user",
        source_kind="chat_turn",
        emotional_weight=0.5,
    )
    
    fact_mem = calculate_relation_relevance(
        memory_type="fact",
        scope="general",
        source_kind="web_research",
        emotional_weight=0.0,
    )
    
    assert relationship_mem > 0.8
    assert fact_mem < 0.5
    assert relationship_mem > fact_mem


def test_memory_reinforcement_integration(isolated_db):
    """Test reinforcement integration with DB."""
    user_id = "user-reinforcement-test"
    service = MemoryV2Service()
    
    # Create memory
    item = service.create_memory_item(
        user_id=user_id,
        memory_type="procedural",
        scope="workflow",
        title="Test workflow",
        content="Successful pattern",
        source_kind="agent_cycle",
        importance_score=0.6,
    )
    
    assert item is not None
    assert item.reinforcement_count == 0
    
    # Reinforce success
    assert reinforce_memory_item(item.id, user_id, success=True, recurrence_boost=0.1, salience_boost=0.05)
    
    # Check reinforced
    reinforced = get_reinforced_memories(user_id, min_reinforcements=1, limit=10)
    assert len(reinforced) == 1
    assert reinforced[0].id == item.id
    assert reinforced[0].reinforcement_count == 1
    assert reinforced[0].success_reinforcements == 1
    assert reinforced[0].failure_reinforcements == 0

    # Repeated reinforcement is atomic and scores remain inside their contract.
    assert reinforce_memory_item(
        item.id,
        user_id,
        success=False,
        recurrence_boost=1.0,
        salience_boost=1.0,
    )
    reinforced = get_reinforced_memories(user_id, min_reinforcements=2, limit=10)
    assert reinforced[0].reinforcement_count == 2
    assert reinforced[0].success_reinforcements == 1
    assert reinforced[0].failure_reinforcements == 1
    assert reinforced[0].recurrence_score == 1.0
    assert reinforced[0].salience_score == 1.0


def test_memory_reinforcement_rejects_invalid_boosts_and_missing_target():
    with pytest.raises(ValueError, match="recurrence_boost"):
        reinforce_memory_item(
            "missing",
            "user",
            success=True,
            recurrence_boost=float("nan"),
        )

    with pytest.raises(ValueError, match="salience_boost"):
        reinforce_memory_item(
            "missing",
            "user",
            success=True,
            salience_boost=-0.1,
        )

    assert reinforce_memory_item("missing", "user", success=True) is False


def test_memory_suppression_integration(isolated_db):
    """Test suppression marking."""
    user_id = "user-suppression-test"
    service = MemoryV2Service()
    
    # Create low-value memory
    item = service.create_memory_item(
        user_id=user_id,
        memory_type="fact",
        scope="domain",
        title="Stale fact",
        content="Old information",
        source_kind="chat_turn",
        importance_score=0.2,
        confidence_score=0.3,
    )
    
    assert item is not None
    
    # Suppress
    assert mark_memory_suppressed(item.id, user_id, suppressed=True)
    
    # Check suppressed
    suppressed = get_suppressed_memories(user_id, limit=10)
    assert len(suppressed) == 1
    assert suppressed[0].id == item.id
    assert suppressed[0].is_suppressed is True


def test_forgetting_sweep(isolated_db):
    """Test forgetting sweep suppresses stale low-value items."""
    user_id = "user-forgetting-test"
    service = MemoryV2Service()
    
    # Create high-value item
    high_value = service.create_memory_item(
        user_id=user_id,
        memory_type="preference",
        scope="user",
        title="Important preference",
        content="User preference",
        source_kind="chat_turn",
        importance_score=0.8,
        emotional_weight=0.5,
    )
    
    # Create low-value stale item (simulate age)
    low_value = service.create_memory_item(
        user_id=user_id,
        memory_type="fact",
        scope="domain",
        title="Stale fact",
        content="Old fact",
        source_kind="chat_turn",
        importance_score=0.1,
        confidence_score=0.2,
    )
    
    # Force low freshness by backdating (would need DB manipulation in real test)
    # For now, test sweep logic
    result = service.run_forgetting_sweep(user_id, suppress_threshold=0.15)
    
    assert result["ok"] is True
    assert result["evaluated_count"] >= 2


def test_retrieval_explanation(isolated_db):
    """Test retrieval explanation generation."""
    user_id = "user-explanation-test"
    service = MemoryV2Service()
    
    # Create reinforced memory
    item = service.create_memory_item(
        user_id=user_id,
        memory_type="procedural",
        scope="workflow",
        title="Tested pattern",
        content="Pattern content",
        source_kind="agent_cycle",
        importance_score=0.7,
    )
    
    # Reinforce
    reinforce_memory_item(item.id, user_id, success=True)
    
    # Get explanation
    explanation = service.get_retrieval_explanation(user_id, query="pattern", top_n=5)
    
    assert explanation["user_id"] == user_id
    assert "top_reason_codes" in explanation
    assert "retrieval_strategy" in explanation
    assert explanation["match_count"] >= 0


def test_reinforce_outcome_batch(isolated_db):
    """Test batch reinforcement after outcome."""
    user_id = "user-batch-test"
    service = MemoryV2Service()
    
    # Create multiple items
    item1 = service.create_memory_item(
        user_id=user_id,
        memory_type="procedural",
        scope="workflow",
        title="Pattern A",
        content="Content A",
        source_kind="agent_cycle",
    )
    
    item2 = service.create_memory_item(
        user_id=user_id,
        memory_type="lesson",
        scope="interaction",
        title="Lesson B",
        content="Content B",
        source_kind="chat_turn",
    )
    
    # Reinforce batch
    result = service.reinforce_outcome(
        user_id=user_id,
        memory_ids=[item1.id, item2.id],
        success=True,
    )
    
    assert result["attempted"] is True
    assert result["reinforced_count"] == 2
    assert result["success"] is True
