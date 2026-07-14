#!/usr/bin/env python3
"""
Memory V2 decay and lifecycle management.

Handles memory freshness decay, bucket transitions, and archival logic.

Runtime wiring (19.07): ``run_decay_pass`` is called from
``aihub.workers.consolidation._run_once`` alongside Memory V2 consolidation and
forgetting sweep.
"""

import logging
import time
from typing import Any

from aihub.memory_psyche_contracts import DecayBucket
from aihub.memory_v2_repository import (
    get_top_salient_memories,
    update_memory_item,
)
from aihub.memory_v2_models import MemoryV2Item
from aihub.memory_v2_scoring import calculate_freshness

logger = logging.getLogger(__name__)


def assign_decay_bucket(
    freshness_score: float,
    salience_score: float,
    is_pinned: bool,
    recurrence_score: float,
) -> DecayBucket:
    """
    Assign memory to decay bucket based on scoring.

    Buckets:
    - active: fresh, salient, or frequently accessed
    - warm: moderately fresh, decent salience
    - cooling: aging, low salience
    - archive_candidate: very old, very low salience
    """
    if is_pinned:
        return "active"

    if freshness_score >= 0.7 or salience_score >= 0.7 or recurrence_score >= 0.6:
        return "active"

    if freshness_score >= 0.4 or salience_score >= 0.5:
        return "warm"

    if freshness_score >= 0.2 or salience_score >= 0.3:
        return "cooling"

    return "archive_candidate"


def apply_decay_update(item: MemoryV2Item) -> MemoryV2Item:
    """
    Recalculate freshness and decay bucket for a memory item.

    Returns updated item (not yet persisted to DB).
    """
    new_freshness = calculate_freshness(item.created_ts, item.is_pinned)
    new_bucket = assign_decay_bucket(
        freshness_score=new_freshness,
        salience_score=item.salience_score,
        is_pinned=item.is_pinned,
        recurrence_score=item.recurrence_score,
    )

    item.freshness_score = new_freshness
    item.decay_bucket = new_bucket
    item.updated_ts = time.time()

    return item


def run_decay_pass(user_id: str, batch_size: int = 100) -> int:
    """
    Run a decay pass on user's memory items.

    Recalculates freshness and decay buckets for top memories.
    Returns count of items updated.
    """
    items = get_top_salient_memories(user_id, limit=batch_size, exclude_archived=True)
    updated_count = 0

    for item in items:
        updated_item = apply_decay_update(item)
        if update_memory_item(updated_item):
            updated_count += 1

    logger.info(f"Decay pass for user {user_id}: updated {updated_count} items")
    return updated_count
