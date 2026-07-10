#!/usr/bin/env python3
"""
Memory V2 contradiction detection and resolution.

Identifies conflicts between memory items and manages contradiction state.
"""

import logging
import time
import uuid
from typing import Any

from aihub.memory_v2_models import MemoryV2Item, MemoryV2Link
from aihub.memory_v2_repository import (
    search_memory_items,
    get_memory_item,
    update_memory_item,
    insert_memory_link,
)
from aihub.memory_psyche_contracts import MemoryV2Contradiction

logger = logging.getLogger(__name__)


def detect_contradictions(
    user_id: str, candidate_item: MemoryV2Item
) -> list[MemoryV2Contradiction]:
    """
    Detect contradictions between candidate item and existing memories.

    Returns list of detected contradictions with confidence scores.
    """
    contradictions: list[MemoryV2Contradiction] = []

    if candidate_item.memory_type == "preference":
        contradictions.extend(_detect_preference_conflicts(user_id, candidate_item))
    elif candidate_item.memory_type == "fact":
        contradictions.extend(_detect_fact_conflicts(user_id, candidate_item))
    elif candidate_item.memory_type == "procedural":
        contradictions.extend(_detect_procedural_conflicts(user_id, candidate_item))

    return contradictions


def _detect_preference_conflicts(
    user_id: str, candidate: MemoryV2Item
) -> list[MemoryV2Contradiction]:
    """Detect conflicting preferences."""
    existing = search_memory_items(
        user_id=user_id,
        memory_types=["preference"],
        exclude_archived=True,
        limit=50,
    )

    contradictions = []
    candidate_lower = candidate.content.lower()

    for item in existing:
        if item.id == candidate.id:
            continue

        item_lower = item.content.lower()

        # Simple heuristic: opposite sentiment signals
        if _contains_opposite_sentiment(candidate_lower, item_lower):
            confidence = min(candidate.confidence_score, item.confidence_score)
            contradictions.append(
                MemoryV2Contradiction(
                    from_memory_id=candidate.id,
                    to_memory_id=item.id,
                    contradiction_type="preference_conflict",
                    confidence=confidence,
                    reason=f"Preference '{candidate.title}' conflicts with '{item.title}'",
                    created_ts=time.time(),
                )
            )

    return contradictions


def _detect_fact_conflicts(
    user_id: str, candidate: MemoryV2Item
) -> list[MemoryV2Contradiction]:
    """Detect conflicting facts."""
    existing = search_memory_items(
        user_id=user_id,
        memory_types=["fact"],
        query=candidate.title[:50],
        exclude_archived=True,
        limit=20,
    )

    contradictions = []
    for item in existing:
        if item.id == candidate.id:
            continue

        # If titles are very similar but content differs significantly, flag as potential conflict
        title_sim = _simple_similarity(candidate.title, item.title)
        content_sim = _simple_similarity(candidate.content, item.content)

        if title_sim > 0.6 and content_sim < 0.4:
            confidence = min(candidate.confidence_score, item.confidence_score) * 0.8
            contradictions.append(
                MemoryV2Contradiction(
                    from_memory_id=candidate.id,
                    to_memory_id=item.id,
                    contradiction_type="fact_conflict",
                    confidence=confidence,
                    reason=f"Fact '{candidate.title}' may contradict '{item.title}'",
                    created_ts=time.time(),
                )
            )

    return contradictions


def _detect_procedural_conflicts(
    user_id: str, candidate: MemoryV2Item
) -> list[MemoryV2Contradiction]:
    """Detect conflicting procedural memories."""
    existing = search_memory_items(
        user_id=user_id,
        memory_types=["procedural"],
        query=candidate.title[:50],
        exclude_archived=True,
        limit=20,
    )

    contradictions = []
    candidate_lower = candidate.content.lower()

    for item in existing:
        if item.id == candidate.id:
            continue

        item_lower = item.content.lower()

        # Check for opposite strategies or tools
        if _contains_opposite_strategy(candidate_lower, item_lower):
            confidence = min(candidate.confidence_score, item.confidence_score) * 0.7
            contradictions.append(
                MemoryV2Contradiction(
                    from_memory_id=candidate.id,
                    to_memory_id=item.id,
                    contradiction_type="procedural_conflict",
                    confidence=confidence,
                    reason=f"Procedure '{candidate.title}' recommends opposite of '{item.title}'",
                    created_ts=time.time(),
                )
            )

    return contradictions


def _contains_opposite_sentiment(text_a: str, text_b: str) -> bool:
    """Simple heuristic for opposite sentiment detection."""
    # Positive signals
    likes_a = any(word in text_a for word in ["prefer", "like", "favor", "want", "choose", "detailed", "comprehensive"])
    dislikes_a = any(word in text_a for word in ["dislike", "avoid", "reject", "don't want", "short", "concise", "brief"])

    likes_b = any(word in text_b for word in ["prefer", "like", "favor", "want", "choose", "detailed", "comprehensive"])
    dislikes_b = any(word in text_b for word in ["dislike", "avoid", "reject", "don't want", "short", "concise", "brief"])

    # Check for specific opposites
    has_short_a = any(word in text_a for word in ["short", "concise", "brief"])
    has_detailed_a = any(word in text_a for word in ["detailed", "comprehensive", "extensive"])
    has_short_b = any(word in text_b for word in ["short", "concise", "brief"])
    has_detailed_b = any(word in text_b for word in ["detailed", "comprehensive", "extensive"])

    if (has_short_a and has_detailed_b) or (has_detailed_a and has_short_b):
        return True

    return (likes_a and dislikes_b) or (dislikes_a and likes_b)


def _contains_opposite_strategy(text_a: str, text_b: str) -> bool:
    """Simple heuristic for opposite strategy detection."""
    keywords_a = set()
    keywords_b = set()

    if "instant" in text_a:
        keywords_a.add("instant")
    if "planning" in text_a or "planned" in text_a:
        keywords_a.add("planning")

    if "instant" in text_b:
        keywords_b.add("instant")
    if "planning" in text_b or "planned" in text_b:
        keywords_b.add("planning")

    return bool(keywords_a & {"instant"} and keywords_b & {"planning"}) or bool(
        keywords_a & {"planning"} and keywords_b & {"instant"}
    )


def _simple_similarity(text_a: str, text_b: str) -> float:
    """
    Simple Jaccard similarity for text comparison.

    Returns value in [0.0, 1.0].
    """
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())

    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0

    intersection = words_a & words_b
    union = words_a | words_b

    return len(intersection) / len(union) if union else 0.0


def resolve_contradiction_supersede(
    newer_memory_id: str, older_memory_id: str, user_id: str
) -> bool:
    """
    Resolve contradiction by marking older memory as superseded.

    Returns True if successful.
    """
    older = get_memory_item(older_memory_id, user_id)
    if not older:
        logger.warning(f"Cannot resolve: older memory {older_memory_id} not found")
        return False

    older.contradiction_state = "superseded"
    older.valid_to_ts = time.time()
    older.updated_ts = time.time()

    if not update_memory_item(older):
        return False

    # Create link
    link = MemoryV2Link(
        id=f"link-{uuid.uuid4().hex[:16]}",
        user_id=user_id,
        from_memory_id=newer_memory_id,
        to_memory_id=older_memory_id,
        link_type="supersedes",
        weight=1.0,
        created_ts=time.time(),
    )
    insert_memory_link(link)

    logger.info(f"Resolved contradiction: {newer_memory_id} supersedes {older_memory_id}")
    return True


def mark_as_conflicted(memory_id: str, user_id: str) -> bool:
    """Mark memory as conflicted (user or system decision)."""
    item = get_memory_item(memory_id, user_id)
    if not item:
        return False

    item.contradiction_state = "conflicted"
    item.updated_ts = time.time()
    return update_memory_item(item)
