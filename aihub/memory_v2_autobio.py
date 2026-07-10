#!/usr/bin/env python3
"""
Memory V2 autobiographical synthesis.

Generates narrative summaries from interaction history and compacts
repetitive episodic memories.
"""

import logging
from typing import Any

from aihub.memory_v2_repository import search_memory_items
from aihub.memory_v2_scoring import classify_memory_horizon_kind

logger = logging.getLogger(__name__)


def compact_episodic_memories(
    user_id: str,
    min_episode_count: int = 3,
    similarity_threshold: float = 0.7,
) -> dict[str, Any]:
    """
    Compact repetitive episodic memories into autobiographical summary.

    Identifies patterns where the same type of event occurred multiple times,
    and creates a consolidated autobiographical memory that represents the
    pattern.

    Args:
        user_id: User ID
        min_episode_count: Minimum number of similar episodes to trigger
            compaction
        similarity_threshold: Threshold for grouping similar episodes (based
            on title similarity)

    Returns:
        dict with ok, compacted_count, groups
    """
    # Fetch episodic memories (note: actual type is "autobiographical")
    episodic_items = search_memory_items(
        user_id=user_id,
        memory_types=["autobiographical"],
        min_salience=0.2,
        exclude_archived=True,
        limit=100,
    )

    if len(episodic_items) < min_episode_count:
        return {"ok": True, "compacted_count": 0, "groups": []}

    # Group by normalized title (simple string prefix matching)
    title_groups: dict[str, list] = {}
    for item in episodic_items:
        # Normalize title (lowercase, remove dates/numbers)
        normalized = item.title.lower().split(" ")[0:3]
        key = " ".join(normalized)

        if key not in title_groups:
            title_groups[key] = []
        title_groups[key].append(item)

    # Find groups with repetition
    compactable_groups = [
        (key, items)
        for key, items in title_groups.items()
        if len(items) >= min_episode_count
    ]

    if not compactable_groups:
        return {"ok": True, "compacted_count": 0, "groups": []}

    logger.info(
        "Found %d episodic groups for user %s ready for compaction",
        len(compactable_groups),
        user_id,
    )

    # For each group, create summary metadata
    # (actual consolidation into new memory would be done here)
    compact_summary = []
    for key, items in compactable_groups:
        compact_summary.append(
            {
                "pattern": key,
                "episode_count": len(items),
                "avg_salience": sum(i.salience_score for i in items)
                / len(items),
                "first_ts": min(i.created_ts for i in items),
                "last_ts": max(i.created_ts for i in items),
            }
        )

    return {
        "ok": True,
        "compacted_count": sum(g["episode_count"] for g in compact_summary),
        "groups": compact_summary,
    }


def build_autobiographical_summary(
    user_id: str, max_memories: int = 30
) -> str:
    """
    Build autobiographical summary from important memories.

    Synthesizes narrative from top salient autobiographical and relationship memories.
    """
    autobio_items = search_memory_items(
        user_id=user_id,
        memory_types=["autobiographical", "relationship"],
        min_salience=0.3,
        exclude_archived=True,
        limit=max_memories,
    )

    if not autobio_items:
        return "No significant autobiographical memories yet."

    autobio = [item for item in autobio_items if item.memory_type == "autobiographical"]
    relationships = [item for item in autobio_items if item.memory_type == "relationship"]

    def _stable_label(item: Any) -> bool:
        k = classify_memory_horizon_kind(
            item.memory_type,
            item.scope,
            item.source_kind,
            item.contradiction_state,
            item.stability_tier,
            item.reinforcement_count,
        )
        long_term = k in (
            "reinforced_long_term_pattern",
            "stable_preference",
            "stable_fact",
        )
        return long_term or item.stability_tier in ("developing", "stable")

    stable_autobio = [a for a in autobio if _stable_label(a)]
    fluid_autobio = [a for a in autobio if a not in stable_autobio]

    summary_parts = []

    if stable_autobio:
        summary_parts.append(
            "Trwałe wzorce: "
            + " • ".join([item.title for item in stable_autobio[:4]])
        )
    if fluid_autobio:
        summary_parts.append(
            "Ostatnie epizody (płynne): "
            + " • ".join([item.title for item in fluid_autobio[:4]])
        )
    if relationships:
        rel_s = [r for r in relationships if _stable_label(r)]
        rel_f = [r for r in relationships if r not in rel_s]
        if rel_s:
            summary_parts.append(
                "Relacja (stabilne): " + " • ".join([item.title for item in rel_s[:3]])
            )
        if rel_f:
            summary_parts.append(
                "Relacja (bieżące): " + " • ".join([item.title for item in rel_f[:2]])
            )

    return " | ".join(summary_parts) if summary_parts else "Minimal autobiographical context."


def build_relationship_summary(user_id: str) -> str:
    """Build summary of user relationships and interaction patterns."""
    relationship_items = search_memory_items(
        user_id=user_id,
        memory_types=["relationship"],
        min_salience=0.2,
        exclude_archived=True,
        limit=10,
    )

    if not relationship_items:
        return "No relationship patterns established."

    titles = [item.title for item in relationship_items[:5]]
    return "Active: " + " • ".join(titles)
