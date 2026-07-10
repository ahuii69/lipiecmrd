#!/usr/bin/env python3
"""
Memory V2 consolidation logic.

Merges episodic memories into higher-level summaries and extracts patterns.
"""

import logging
import time
import uuid
from typing import Any

from aihub.memory_v2_models import MemoryV2Item, MemoryV2Consolidation
from aihub.memory_v2_repository import (
    search_memory_items,
    insert_memory_item,
    insert_memory_consolidation,
    get_memories_by_ids,
    insert_memory_link,
)
from aihub.memory_v2_models import MemoryV2Link
from aihub.memory_psyche_contracts import MemoryV2ConsolidationResult, ConsolidationType
from aihub.memory_v2_scoring import calculate_salience, calculate_freshness, calculate_identity_relevance

logger = logging.getLogger(__name__)


def consolidate_episodic_memories(user_id: str, scope: str = "session") -> MemoryV2ConsolidationResult | None:
    """
    Consolidate small episodic memories into a larger summary.

    Returns consolidation result or None if insufficient items.
    """
    items = search_memory_items(
        user_id=user_id,
        memory_types=["fact"],
        scopes=["session", "interaction"],
        min_salience=0.0,
        exclude_archived=True,
        exclude_contradicted=True,
        limit=50,
    )

    if len(items) < 3:
        logger.debug(f"Insufficient episodic items for consolidation: {len(items)}")
        return None

    # Group by session or time proximity
    consolidated_text = _build_consolidated_summary([item.content for item in items])
    consolidated_title = f"Consolidated {scope} summary"

    new_item_id = f"memv2-{uuid.uuid4().hex[:16]}"
    now = time.time()

    consolidated_item = MemoryV2Item(
        id=new_item_id,
        user_id=user_id,
        memory_type="autobiographical",
        scope="user",
        title=consolidated_title,
        content=consolidated_text,
        summary=consolidated_text[:200],
        source_kind="consolidation",
        source_ref=None,
        importance_score=_average_score([item.importance_score for item in items]),
        salience_score=0.0,
        emotional_weight=_average_score([item.emotional_weight for item in items]),
        recurrence_score=min(1.0, len(items) * 0.05),
        confidence_score=_average_score([item.confidence_score for item in items]),
        freshness_score=calculate_freshness(now, is_pinned=False),
        identity_relevance_score=calculate_identity_relevance("autobiographical", "user", 0.0),
        stability_tier="developing",
        reinforcement_count=min(3, len(items)),
        success_reinforcements=min(3, len(items)),
        created_ts=now,
        updated_ts=now,
    )

    # Recalculate salience
    consolidated_item.salience_score = calculate_salience(
        consolidated_item.importance_score,
        consolidated_item.recurrence_score,
        consolidated_item.emotional_weight,
        consolidated_item.identity_relevance_score,
        consolidated_item.confidence_score,
        consolidated_item.freshness_score,
    )

    if not insert_memory_item(consolidated_item):
        logger.error("Failed to insert consolidated memory item")
        return None

    # Create consolidation record
    consolidation = MemoryV2Consolidation(
        id=f"cons-{uuid.uuid4().hex[:16]}",
        user_id=user_id,
        consolidation_type="episodic_rollup",
        input_memory_ids=[item.id for item in items],
        output_memory_id=new_item_id,
        compression_ratio=len(items) / 1.0,
        created_ts=now,
    )

    if not insert_memory_consolidation(consolidation):
        logger.error("Failed to record consolidation")
        return None

    # Create links from consolidated to inputs
    for item in items:
        link = MemoryV2Link(
            id=f"link-{uuid.uuid4().hex[:16]}",
            user_id=user_id,
            from_memory_id=new_item_id,
            to_memory_id=item.id,
            link_type="refines",
            weight=0.5,
            created_ts=now,
        )
        insert_memory_link(link)

    result = MemoryV2ConsolidationResult(
        consolidation_id=consolidation.id,
        consolidation_type="episodic_rollup",
        input_memory_ids=[item.id for item in items],
        output_memory_id=new_item_id,
        compression_ratio=consolidation.compression_ratio,
        summary=consolidated_text[:200],
        created_ts=now,
    )

    logger.info(f"Consolidated {len(items)} episodic memories for user {user_id}")
    return result


def _build_consolidated_summary(contents: list[str]) -> str:
    """Build a consolidated summary from multiple content strings."""
    if not contents:
        return ""

    # Simple concatenation with deduplication
    unique_contents = []
    seen = set()
    for content in contents:
        normalized = content.lower().strip()
        if normalized and normalized not in seen:
            unique_contents.append(content)
            seen.add(normalized)

    return " | ".join(unique_contents[:10])


def _average_score(scores: list[float]) -> float:
    """Calculate average score, clamped to [0.0, 1.0]."""
    if not scores:
        return 0.0
    avg = sum(scores) / len(scores)
    return max(0.0, min(1.0, avg))
