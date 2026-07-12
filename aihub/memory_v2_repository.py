#!/usr/bin/env python3
"""
Memory V2 Repository - data access layer for memory_v2_* tables.

Handles all CRUD operations for Memory V2 entities with proper SQLite row mapping.
"""

import json
import logging
import math
import sqlite3
from typing import Any

from aihub.db import (
    exec_one,
    exec_one_rowcount,
    fetch_all,
    fetch_one,
    json_dumps,
    json_loads,
    now_ts,
)
from aihub.memory_v2_models import (
    MemoryV2Item,
    MemoryV2Link,
    MemoryV2Consolidation,
    MemoryV2Procedure,
    MemoryV2Lesson,
)
from aihub.memory_psyche_contracts import (
    MemoryType,
    MemoryScope,
    MemorySourceKind,
    ContradictionState,
    DecayBucket,
    MemoryLinkType,
    ConsolidationType,
)

logger = logging.getLogger(__name__)


# ─── Memory Items ───────────────────────────────────────────────────────────


def insert_memory_item(item: MemoryV2Item) -> bool:
    """Insert new memory item into database."""
    try:
        exec_one(
            """
            INSERT INTO memory_v2_items(
                id, user_id, session_id, memory_type, scope,
                title, content, summary, source_kind, source_ref,
                importance_score, salience_score, emotional_weight,
                recurrence_score, confidence_score, freshness_score,
                identity_relevance_score, relation_relevance_score,
                outcome_reinforcement_score, source_reliability_score,
                retrieval_priority_score, contradiction_state,
                valid_from_ts, valid_to_ts, last_accessed_ts, last_reinforced_ts,
                reinforcement_count, success_reinforcements, failure_reinforcements,
                decay_bucket, stability_tier, is_pinned, is_archived, is_suppressed, embedding_vector_ref,
                created_ts, updated_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                item.id,
                item.user_id,
                item.session_id,
                item.memory_type,
                item.scope,
                item.title,
                item.content,
                item.summary,
                item.source_kind,
                item.source_ref,
                item.importance_score,
                item.salience_score,
                item.emotional_weight,
                item.recurrence_score,
                item.confidence_score,
                item.freshness_score,
                item.identity_relevance_score,
                item.relation_relevance_score,
                item.outcome_reinforcement_score,
                item.source_reliability_score,
                item.retrieval_priority_score,
                item.contradiction_state,
                item.valid_from_ts,
                item.valid_to_ts,
                item.last_accessed_ts,
                item.last_reinforced_ts,
                item.reinforcement_count,
                item.success_reinforcements,
                item.failure_reinforcements,
                item.decay_bucket,
                item.stability_tier,
                int(item.is_pinned),
                int(item.is_archived),
                int(item.is_suppressed),
                item.embedding_vector_ref,
                item.created_ts,
                item.updated_ts,
            ),
        )
        logger.debug(f"Inserted memory item: id={item.id} user={item.user_id} type={item.memory_type}")
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to insert memory item: {e}")
        return False


def update_memory_item(item: MemoryV2Item) -> bool:
    """Update existing memory item."""
    try:
        exec_one(
            """
            UPDATE memory_v2_items SET
                title=?, content=?, summary=?,
                importance_score=?, salience_score=?, emotional_weight=?,
                recurrence_score=?, confidence_score=?, freshness_score=?,
                identity_relevance_score=?, relation_relevance_score=?,
                outcome_reinforcement_score=?, source_reliability_score=?,
                retrieval_priority_score=?, contradiction_state=?,
                last_accessed_ts=?, last_reinforced_ts=?,
                reinforcement_count=?, success_reinforcements=?, failure_reinforcements=?,
                decay_bucket=?, stability_tier=?, is_pinned=?, is_archived=?, is_suppressed=?,
                embedding_vector_ref=?, updated_ts=?
            WHERE id=? AND user_id=?
            """,
            (
                item.title,
                item.content,
                item.summary,
                item.importance_score,
                item.salience_score,
                item.emotional_weight,
                item.recurrence_score,
                item.confidence_score,
                item.freshness_score,
                item.identity_relevance_score,
                item.relation_relevance_score,
                item.outcome_reinforcement_score,
                item.source_reliability_score,
                item.retrieval_priority_score,
                item.contradiction_state,
                item.last_accessed_ts,
                item.last_reinforced_ts,
                item.reinforcement_count,
                item.success_reinforcements,
                item.failure_reinforcements,
                item.decay_bucket,
                item.stability_tier,
                int(item.is_pinned),
                int(item.is_archived),
                int(item.is_suppressed),
                item.embedding_vector_ref,
                item.updated_ts,
                item.id,
                item.user_id,
            ),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to update memory item: {e}")
        return False


def get_memory_item(item_id: str, user_id: str) -> MemoryV2Item | None:
    """Retrieve single memory item by ID."""
    row = fetch_one(
        "SELECT * FROM memory_v2_items WHERE id=? AND user_id=?",
        (item_id, user_id),
    )
    if not row:
        return None
    return _row_to_memory_item(row)


def search_memory_items(
    user_id: str,
    query: str = "",
    memory_types: list[MemoryType] | None = None,
    scopes: list[MemoryScope] | None = None,
    min_salience: float = 0.0,
    min_confidence: float = 0.0,
    exclude_archived: bool = True,
    exclude_contradicted: bool = False,
    limit: int = 20,
) -> list[MemoryV2Item]:
    """Search memory items with filters and ranking."""
    conditions = ["user_id=?"]
    params: list[Any] = [user_id]

    if exclude_archived:
        conditions.append("is_archived=0")

    if exclude_contradicted:
        conditions.append("contradiction_state='none'")

    if memory_types:
        bind_marks = ",".join("?" * len(memory_types))
        conditions.append(f"memory_type IN ({bind_marks})")
        params.extend(memory_types)

    if scopes:
        bind_marks = ",".join("?" * len(scopes))
        conditions.append(f"scope IN ({bind_marks})")
        params.extend(scopes)

    if min_salience > 0.0:
        conditions.append("salience_score >= ?")
        params.append(min_salience)

    if min_confidence > 0.0:
        conditions.append("confidence_score >= ?")
        params.append(min_confidence)

    if query:
        conditions.append("(title LIKE ? OR content LIKE ? OR summary LIKE ?)")
        query_pattern = f"%{query}%"
        params.extend([query_pattern, query_pattern, query_pattern])

    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT * FROM memory_v2_items
        WHERE {where_clause}
        ORDER BY salience_score DESC, freshness_score DESC, created_ts DESC
        LIMIT ?
    """
    params.append(limit)

    rows = fetch_all(sql, tuple(params))
    return [_row_to_memory_item(r) for r in rows]


def get_top_salient_memories(
    user_id: str, limit: int = 10, exclude_archived: bool = True
) -> list[MemoryV2Item]:
    """Get most salient memories for user."""
    conditions = ["user_id=?"]
    if exclude_archived:
        conditions.append("is_archived=0")

    where_clause = " AND ".join(conditions)
    rows = fetch_all(
        f"""
        SELECT * FROM memory_v2_items
        WHERE {where_clause}
        ORDER BY salience_score DESC, created_ts DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    return [_row_to_memory_item(r) for r in rows]


def count_memory_items(user_id: str, memory_type: MemoryType | None = None) -> int:
    """Count memory items for user, optionally filtered by type."""
    if memory_type:
        row = fetch_one(
            "SELECT COUNT(*) as cnt FROM memory_v2_items WHERE user_id=? AND memory_type=? AND is_archived=0",
            (user_id, memory_type),
        )
    else:
        row = fetch_one(
            "SELECT COUNT(*) as cnt FROM memory_v2_items WHERE user_id=? AND is_archived=0",
            (user_id,),
        )
    return int(row["cnt"]) if row else 0


def get_contradicted_memories(user_id: str, limit: int = 50) -> list[MemoryV2Item]:
    """Get memories with contradiction state."""
    rows = fetch_all(
        """
        SELECT * FROM memory_v2_items
        WHERE user_id=? AND contradiction_state != 'none' AND is_archived=0
        ORDER BY updated_ts DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    return [_row_to_memory_item(r) for r in rows]


def reinforce_memory_item(
    item_id: str,
    user_id: str,
    success: bool,
    recurrence_boost: float = 0.1,
    salience_boost: float = 0.05,
) -> bool:
    """
    Reinforce memory item after outcome.
    
    - Increments reinforcement counters
    - Updates last_reinforced_ts
    - Boosts recurrence and salience
    - Updates outcome_reinforcement_score
    """
    for name, value in (
        ("recurrence_boost", recurrence_boost),
        ("salience_boost", salience_boost),
    ):
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be finite and between 0.0 and 1.0")

    now = now_ts()
    success_delta = 1 if success else 0
    failure_delta = 0 if success else 1

    affected = exec_one_rowcount(
        """
        UPDATE memory_v2_items SET
            reinforcement_count = reinforcement_count + 1,
            success_reinforcements = success_reinforcements + ?,
            failure_reinforcements = failure_reinforcements + ?,
            recurrence_score = CASE
                WHEN recurrence_score + ? >= 1.0 THEN 1.0
                ELSE recurrence_score + ?
            END,
            salience_score = CASE
                WHEN salience_score + ? >= 1.0 THEN 1.0
                ELSE salience_score + ?
            END,
            last_reinforced_ts = ?,
            updated_ts = ?
        WHERE id=? AND user_id=?
        """,
        (
            success_delta,
            failure_delta,
            recurrence_boost,
            recurrence_boost,
            salience_boost,
            salience_boost,
            now,
            now,
            item_id,
            user_id,
        ),
    )
    if affected == 0:
        logger.warning("Memory reinforcement target was not found")
        return False
    logger.debug("Memory reinforcement committed: success=%s", success)
    return True


def mark_memory_suppressed(item_id: str, user_id: str, suppressed: bool) -> bool:
    """Mark memory as suppressed (soft delete for low-value items)."""
    try:
        exec_one(
            "UPDATE memory_v2_items SET is_suppressed=?, updated_ts=? WHERE id=? AND user_id=?",
            (int(suppressed), now_ts(), item_id, user_id),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to mark suppressed: {e}")
        return False


def get_reinforced_memories(user_id: str, min_reinforcements: int = 2, limit: int = 20) -> list[MemoryV2Item]:
    """Get memories that have been reinforced multiple times."""
    rows = fetch_all(
        """
        SELECT * FROM memory_v2_items
        WHERE user_id=? AND reinforcement_count >= ? AND is_archived=0 AND is_suppressed=0
        ORDER BY reinforcement_count DESC, salience_score DESC
        LIMIT ?
        """,
        (user_id, min_reinforcements, limit),
    )
    return [_row_to_memory_item(r) for r in rows]


def get_suppressed_memories(user_id: str, limit: int = 50) -> list[MemoryV2Item]:
    """Get suppressed memories."""
    rows = fetch_all(
        "SELECT * FROM memory_v2_items WHERE user_id=? AND is_suppressed=1 ORDER BY updated_ts DESC LIMIT ?",
        (user_id, limit),
    )
    return [_row_to_memory_item(r) for r in rows]


def _row_to_memory_item(row: sqlite3.Row) -> MemoryV2Item:
    """Convert SQLite row to MemoryV2Item model."""
    return MemoryV2Item(
        id=row["id"],
        user_id=row["user_id"],
        session_id=row["session_id"],
        memory_type=row["memory_type"],
        scope=row["scope"],
        title=row["title"],
        content=row["content"],
        summary=row["summary"],
        source_kind=row["source_kind"],
        source_ref=row["source_ref"],
        importance_score=float(row["importance_score"]),
        salience_score=float(row["salience_score"]),
        emotional_weight=float(row["emotional_weight"]),
        recurrence_score=float(row["recurrence_score"]),
        confidence_score=float(row["confidence_score"]),
        freshness_score=float(row["freshness_score"]),
        identity_relevance_score=float(row["identity_relevance_score"]),
        relation_relevance_score=float(row["relation_relevance_score"] if row["relation_relevance_score"] is not None else 0.0),
        outcome_reinforcement_score=float(row["outcome_reinforcement_score"] if row["outcome_reinforcement_score"] is not None else 0.0),
        source_reliability_score=float(row["source_reliability_score"] if row["source_reliability_score"] is not None else 0.7),
        retrieval_priority_score=float(row["retrieval_priority_score"] if row["retrieval_priority_score"] is not None else 0.0),
        contradiction_state=row["contradiction_state"],
        valid_from_ts=float(row["valid_from_ts"]) if row["valid_from_ts"] else None,
        valid_to_ts=float(row["valid_to_ts"]) if row["valid_to_ts"] else None,
        last_accessed_ts=float(row["last_accessed_ts"]) if row["last_accessed_ts"] else None,
        last_reinforced_ts=float(row["last_reinforced_ts"]) if row["last_reinforced_ts"] else None,
        reinforcement_count=int(row["reinforcement_count"] if row["reinforcement_count"] is not None else 0),
        success_reinforcements=int(row["success_reinforcements"] if row["success_reinforcements"] is not None else 0),
        failure_reinforcements=int(row["failure_reinforcements"] if row["failure_reinforcements"] is not None else 0),
        decay_bucket=row["decay_bucket"],
        stability_tier=(
            row["stability_tier"]
            if "stability_tier" in row.keys() and row["stability_tier"]
            else "transient"
        ),
        is_pinned=bool(row["is_pinned"]),
        is_archived=bool(row["is_archived"]),
        is_suppressed=bool(row["is_suppressed"] if row["is_suppressed"] is not None else 0),
        embedding_vector_ref=row["embedding_vector_ref"],
        created_ts=float(row["created_ts"]),
        updated_ts=float(row["updated_ts"]),
    )


# ─── Memory Links ───────────────────────────────────────────────────────────


def insert_memory_link(link: MemoryV2Link) -> bool:
    """Insert new memory link."""
    try:
        exec_one(
            """
            INSERT INTO memory_v2_links(
                id, user_id, from_memory_id, to_memory_id, link_type, weight, created_ts
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                link.id,
                link.user_id,
                link.from_memory_id,
                link.to_memory_id,
                link.link_type,
                link.weight,
                link.created_ts,
            ),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to insert memory link: {e}")
        return False


def get_memory_links_from(memory_id: str, user_id: str) -> list[MemoryV2Link]:
    """Get all links originating from a memory."""
    rows = fetch_all(
        "SELECT * FROM memory_v2_links WHERE from_memory_id=? AND user_id=? ORDER BY created_ts DESC",
        (memory_id, user_id),
    )
    return [_row_to_memory_link(r) for r in rows]


def get_memory_links_to(memory_id: str, user_id: str) -> list[MemoryV2Link]:
    """Get all links pointing to a memory."""
    rows = fetch_all(
        "SELECT * FROM memory_v2_links WHERE to_memory_id=? AND user_id=? ORDER BY created_ts DESC",
        (memory_id, user_id),
    )
    return [_row_to_memory_link(r) for r in rows]


def _row_to_memory_link(row: sqlite3.Row) -> MemoryV2Link:
    """Convert SQLite row to MemoryV2Link model."""
    return MemoryV2Link(
        id=row["id"],
        user_id=row["user_id"],
        from_memory_id=row["from_memory_id"],
        to_memory_id=row["to_memory_id"],
        link_type=row["link_type"],
        weight=float(row["weight"]),
        created_ts=float(row["created_ts"]),
    )


# ─── Memory Consolidations ──────────────────────────────────────────────────


def insert_memory_consolidation(consolidation: MemoryV2Consolidation) -> bool:
    """Record a consolidation operation."""
    try:
        exec_one(
            """
            INSERT INTO memory_v2_consolidations(
                id, user_id, consolidation_type,
                input_memory_ids_json, output_memory_id,
                compression_ratio, created_ts
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                consolidation.id,
                consolidation.user_id,
                consolidation.consolidation_type,
                json_dumps(consolidation.input_memory_ids),
                consolidation.output_memory_id,
                consolidation.compression_ratio,
                consolidation.created_ts,
            ),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to insert consolidation: {e}")
        return False


def get_recent_consolidations(user_id: str, limit: int = 10) -> list[MemoryV2Consolidation]:
    """Get recent consolidation operations."""
    rows = fetch_all(
        "SELECT * FROM memory_v2_consolidations WHERE user_id=? ORDER BY created_ts DESC LIMIT ?",
        (user_id, limit),
    )
    return [_row_to_consolidation(r) for r in rows]


def _row_to_consolidation(row: sqlite3.Row) -> MemoryV2Consolidation:
    """Convert SQLite row to MemoryV2Consolidation model."""
    return MemoryV2Consolidation(
        id=row["id"],
        user_id=row["user_id"],
        consolidation_type=row["consolidation_type"],
        input_memory_ids=json_loads(row["input_memory_ids_json"]) or [],
        output_memory_id=row["output_memory_id"],
        compression_ratio=float(row["compression_ratio"]),
        created_ts=float(row["created_ts"]),
    )


# ─── Memory Procedures ──────────────────────────────────────────────────────


def insert_memory_procedure(procedure: MemoryV2Procedure) -> bool:
    """Insert new procedural memory."""
    try:
        exec_one(
            """
            INSERT INTO memory_v2_procedures(
                id, user_id, name, trigger_pattern,
                recommended_strategy, recommended_tools_json, avoid_patterns_json,
                success_rate, failure_rate, confidence_score,
                evidence_count, last_validated_ts, created_ts, updated_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                procedure.id,
                procedure.user_id,
                procedure.name,
                procedure.trigger_pattern,
                procedure.recommended_strategy,
                json_dumps(procedure.recommended_tools),
                json_dumps(procedure.avoid_patterns),
                procedure.success_rate,
                procedure.failure_rate,
                procedure.confidence_score,
                procedure.evidence_count,
                procedure.last_validated_ts,
                procedure.created_ts,
                procedure.updated_ts,
            ),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to insert procedure: {e}")
        return False


def update_memory_procedure(procedure: MemoryV2Procedure) -> bool:
    """Update existing procedural memory."""
    try:
        exec_one(
            """
            UPDATE memory_v2_procedures SET
                recommended_strategy=?, recommended_tools_json=?, avoid_patterns_json=?,
                success_rate=?, failure_rate=?, confidence_score=?,
                evidence_count=?, last_validated_ts=?, updated_ts=?
            WHERE id=? AND user_id=?
            """,
            (
                procedure.recommended_strategy,
                json_dumps(procedure.recommended_tools),
                json_dumps(procedure.avoid_patterns),
                procedure.success_rate,
                procedure.failure_rate,
                procedure.confidence_score,
                procedure.evidence_count,
                procedure.last_validated_ts,
                procedure.updated_ts,
                procedure.id,
                procedure.user_id,
            ),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to update procedure: {e}")
        return False


def get_procedures_for_user(user_id: str, limit: int = 50) -> list[MemoryV2Procedure]:
    """Get all procedures for user, ordered by confidence."""
    rows = fetch_all(
        """
        SELECT * FROM memory_v2_procedures
        WHERE user_id=?
        ORDER BY confidence_score DESC, created_ts DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    return [_row_to_procedure(r) for r in rows]


def find_matching_procedures(
    user_id: str, trigger_pattern: str, limit: int = 5
) -> list[MemoryV2Procedure]:
    """Find procedures matching trigger pattern."""
    rows = fetch_all(
        """
        SELECT * FROM memory_v2_procedures
        WHERE user_id=? AND trigger_pattern LIKE ?
        ORDER BY confidence_score DESC, evidence_count DESC
        LIMIT ?
        """,
        (user_id, f"%{trigger_pattern}%", limit),
    )
    return [_row_to_procedure(r) for r in rows]


def _row_to_procedure(row: sqlite3.Row) -> MemoryV2Procedure:
    """Convert SQLite row to MemoryV2Procedure model."""
    return MemoryV2Procedure(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        trigger_pattern=row["trigger_pattern"],
        recommended_strategy=row["recommended_strategy"],
        recommended_tools=json_loads(row["recommended_tools_json"]) or [],
        avoid_patterns=json_loads(row["avoid_patterns_json"]) or [],
        success_rate=float(row["success_rate"]),
        failure_rate=float(row["failure_rate"]),
        confidence_score=float(row["confidence_score"]),
        evidence_count=int(row["evidence_count"]),
        last_validated_ts=float(row["last_validated_ts"]) if row["last_validated_ts"] else None,
        created_ts=float(row["created_ts"]),
        updated_ts=float(row["updated_ts"]),
    )


# ─── Memory Lessons ─────────────────────────────────────────────────────────


def insert_memory_lesson(lesson: MemoryV2Lesson) -> bool:
    """Insert new lesson."""
    try:
        exec_one(
            """
            INSERT INTO memory_v2_lessons(
                id, user_id, lesson_scope, lesson_text,
                applies_when_json, avoid_when_json,
                strength_score, evidence_count, created_ts, updated_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                lesson.id,
                lesson.user_id,
                lesson.lesson_scope,
                lesson.lesson_text,
                json_dumps(lesson.applies_when),
                json_dumps(lesson.avoid_when),
                lesson.strength_score,
                lesson.evidence_count,
                lesson.created_ts,
                lesson.updated_ts,
            ),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to insert lesson: {e}")
        return False


def get_lessons_for_user(user_id: str, limit: int = 20) -> list[MemoryV2Lesson]:
    """Get lessons for user, ordered by strength."""
    rows = fetch_all(
        """
        SELECT * FROM memory_v2_lessons
        WHERE user_id=?
        ORDER BY strength_score DESC, created_ts DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    return [_row_to_lesson(r) for r in rows]


def _row_to_lesson(row: sqlite3.Row) -> MemoryV2Lesson:
    """Convert SQLite row to MemoryV2Lesson model."""
    return MemoryV2Lesson(
        id=row["id"],
        user_id=row["user_id"],
        lesson_scope=row["lesson_scope"],
        lesson_text=row["lesson_text"],
        applies_when=json_loads(row["applies_when_json"]) or [],
        avoid_when=json_loads(row["avoid_when_json"]) or [],
        strength_score=float(row["strength_score"]),
        evidence_count=int(row["evidence_count"]),
        created_ts=float(row["created_ts"]),
        updated_ts=float(row["updated_ts"]),
    )


# ─── Batch Operations ───────────────────────────────────────────────────────


def get_memories_by_ids(memory_ids: list[str], user_id: str) -> dict[str, MemoryV2Item]:
    """Batch fetch memories by IDs."""
    if not memory_ids:
        return {}

    bind_marks = ",".join("?" * len(memory_ids))
    rows = fetch_all(
        f"SELECT * FROM memory_v2_items WHERE id IN ({bind_marks}) AND user_id=?",
        (*memory_ids, user_id),
    )
    return {row["id"]: _row_to_memory_item(row) for row in rows}
