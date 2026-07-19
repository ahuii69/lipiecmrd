#!/usr/bin/env python3
"""
Memory V2 Service - high-level orchestration layer.

Provides complete memory management API with scoring, consolidation, and contradiction detection.
All production callers should obtain one instance via :attr:`MemoryCanonicalCore.v2_service`
(:func:`aihub.memory_core.get_memory_core`) so SQLite-backed state and policies stay consistent.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from aihub.db import _DB_LOCK, _conn, now_ts
from aihub.memory_psyche_contracts import MemoryScope, MemorySourceKind, MemoryType
from aihub.memory_v2_autobio import (
    build_autobiographical_summary,
    build_relationship_summary,
)
from aihub.memory_v2_consolidation import consolidate_episodic_memories
from aihub.memory_v2_contradictions import (
    detect_contradictions,
    mark_as_conflicted,
    resolve_contradiction_supersede,
)
from aihub.memory_v2_models import (
    MemoryV2Item,
    MemoryV2Lesson,
    MemoryV2Procedure,
    MemoryV2SearchRequest,
    MemoryV2SearchResponse,
    MemoryV2SummaryResponse,
)
from aihub.memory_v2_procedural import extract_procedures_from_experiences
from aihub.memory_v2_repository import (
    count_memory_items,
    get_contradicted_memories,
    get_lessons_for_user,
    get_memory_item,
    get_memory_links_from,
    get_procedures_for_user,
    get_top_salient_memories,
    insert_memory_item,
    search_memory_items,
    update_memory_item,
)
from aihub.memory_v2_hybrid import hybrid_search_memory_items, index_memory_item
from aihub.memory_v2_index_jobs import enqueue_index_job, index_job_summary, process_index_jobs, record_index_job
from aihub.memory_v2_scoring import (
    calculate_freshness,
    calculate_identity_relevance,
    calculate_relation_relevance,
    calculate_retrieval_priority,
    calculate_salience,
    initial_stability_tier_for_new_item,
)

logger = logging.getLogger(__name__)


def _retrieval_explanation_dict_from_items(
    user_id: str,
    query: str,
    items: list[MemoryV2Item],
) -> dict[str, Any]:
    """Build cockpit explanation dict from the same ranked item list as :meth:`MemoryV2Service.search`."""
    top_reason_codes: list[str] = []
    reinforced_count = sum(1 for item in items if item.reinforcement_count > 0)
    suppressed_count = sum(1 for item in items if item.is_suppressed)

    if any(item.reinforcement_count > 2 for item in items):
        top_reason_codes.append("STRONG_REINFORCEMENT")
    if any(item.relation_relevance_score > 0.7 for item in items):
        top_reason_codes.append("HIGH_RELATION_RELEVANCE")
    if any(item.recurrence_score > 0.6 for item in items):
        top_reason_codes.append("RECURRING_PATTERN")
    if any(item.contradiction_state != "none" for item in items):
        top_reason_codes.append("CONTRADICTION_PRESENT")

    items_with_scores: list[dict[str, Any]] = []
    for item in items:
        items_with_scores.append(
            {
                "id": item.id,
                "title": item.title,
                "retrieval_priority": item.retrieval_priority_score,
                "salience": item.salience_score,
                "reinforcement_count": item.reinforcement_count,
                "relation_relevance": item.relation_relevance_score,
                "outcome_reinforcement": item.outcome_reinforcement_score,
            }
        )

    retrieval_strategy = "priority_weighted"
    if items and reinforced_count > len(items) * 0.5:
        retrieval_strategy = "reinforcement_driven"
    elif any(item.contradiction_state != "none" for item in items):
        retrieval_strategy = "contradiction_aware"

    return {
        "user_id": user_id,
        "query": query,
        "top_reason_codes": top_reason_codes,
        "match_count": len(items),
        "reinforced_count": reinforced_count,
        "suppressed_count": suppressed_count,
        "top_items_with_scores": items_with_scores,
        "retrieval_strategy": retrieval_strategy,
    }


class MemoryV2Service:
    """Complete Memory V2 service with all operations."""

    def create_memory_item(
        self,
        user_id: str,
        memory_type: MemoryType,
        scope: MemoryScope,
        title: str,
        content: str,
        source_kind: MemorySourceKind,
        source_ref: str | None = None,
        session_id: str | None = None,
        importance_score: float = 0.5,
        emotional_weight: float = 0.0,
        confidence_score: float = 0.7,
        auto_detect_contradictions: bool = True,
    ) -> MemoryV2Item | None:
        """
        Create new memory item with initial scoring.

        Returns created item or None on failure.
        """
        now = time.time()
        item_id = f"memv2-{uuid.uuid4().hex[:16]}"

        # Generate summary
        summary = content[:200] if len(content) > 200 else content

        # Calculate scores
        freshness = calculate_freshness(now, is_pinned=False)
        identity_relevance = calculate_identity_relevance(
            memory_type, scope, emotional_weight
        )
        relation_relevance = calculate_relation_relevance(
            memory_type, scope, source_kind, emotional_weight
        )

        init_tier = initial_stability_tier_for_new_item(memory_type, scope, source_kind)

        item = MemoryV2Item(
            id=item_id,
            user_id=user_id,
            session_id=session_id,
            memory_type=memory_type,
            scope=scope,
            title=title,
            content=content,
            summary=summary,
            source_kind=source_kind,
            source_ref=source_ref,
            importance_score=importance_score,
            salience_score=0.0,
            emotional_weight=emotional_weight,
            recurrence_score=0.0,
            confidence_score=confidence_score,
            freshness_score=freshness,
            identity_relevance_score=identity_relevance,
            relation_relevance_score=relation_relevance,
            outcome_reinforcement_score=0.0,
            source_reliability_score=0.7,
            retrieval_priority_score=0.0,
            reinforcement_count=0,
            success_reinforcements=0,
            failure_reinforcements=0,
            is_suppressed=False,
            stability_tier=init_tier,
            created_ts=now,
            updated_ts=now,
        )

        # Calculate salience
        item.salience_score = calculate_salience(
            item.importance_score,
            item.recurrence_score,
            item.emotional_weight,
            item.identity_relevance_score,
            item.confidence_score,
            item.freshness_score,
        )

        # Calculate retrieval priority
        item.retrieval_priority_score = calculate_retrieval_priority(
            item.salience_score,
            item.recurrence_score,
            item.freshness_score,
            item.identity_relevance_score,
            item.relation_relevance_score,
            item.outcome_reinforcement_score,
            item.source_reliability_score,
            item.contradiction_state,
            item.is_pinned,
            item.is_suppressed,
            item.decay_bucket,
        )

        # Check for contradictions
        if auto_detect_contradictions:
            contradictions = detect_contradictions(user_id, item)
            if contradictions:
                logger.info(
                    f"Detected {len(contradictions)} contradictions for new memory {item_id}"
                )
                item.contradiction_state = "suspected"

        if not insert_memory_item(item):
            logger.error(f"Failed to insert memory item {item_id}")
            return None

        vector_ref = index_memory_item(item)
        if vector_ref:
            item.embedding_vector_ref = vector_ref
            item.updated_ts = now_ts()
            if not update_memory_item(item):
                logger.warning("memory_v2_index_ref_update_failed id=%s ref=%s", item.id, vector_ref)
            record_index_job(user_id=item.user_id, memory_id=item.id, status="indexed", vector_ref=vector_ref)
        else:
            enqueue_index_job(item.user_id, item.id, reason="immediate vector indexing unavailable")

        logger.debug(
            f"Created memory item: id={item_id} type={memory_type} salience={item.salience_score:.2f}"
        )
        return item

    def search(self, request: MemoryV2SearchRequest) -> MemoryV2SearchResponse:
        """
        Execute memory search with filters and ranking.

        Returns search response with items, contradictions, and procedures.
        """
        items = hybrid_search_memory_items(
            user_id=request.user_id,
            query=request.query,
            memory_types=request.memory_types,
            scopes=request.scopes,
            min_salience=request.min_salience,
            min_confidence=request.min_confidence,
            exclude_archived=request.exclude_archived,
            exclude_contradicted=request.exclude_contradicted,
            limit=request.limit,
        )

        # Get related contradictions if any items have them
        contradictions_data = []
        for item in items:
            if item.contradiction_state != "none":
                links = get_memory_links_from(item.id, request.user_id)
                for link in links:
                    if link.link_type in ["contradicts", "supersedes"]:
                        contradictions_data.append(
                            {
                                "from_memory_id": link.from_memory_id,
                                "to_memory_id": link.to_memory_id,
                                "link_type": link.link_type,
                                "weight": link.weight,
                            }
                        )

        # Find relevant procedures if query present (ranked, not top-confidence dump)
        related_procedures = []
        if request.query:
            from aihub.memory_v2_procedural import rank_procedures_for_query

            related_procedures = rank_procedures_for_query(
                request.user_id, request.query, limit=3
            )

        return MemoryV2SearchResponse(
            items=items,
            contradictions=contradictions_data,
            related_procedures=related_procedures[:3],
            total_count=len(items),
        )

    def get_summary(self, user_id: str) -> MemoryV2SummaryResponse:
        """
        Get comprehensive memory summary for user.

        Returns aggregated statistics and top items.
        """
        from aihub.memory_v2_repository import (
            get_reinforced_memories,
            get_suppressed_memories,
            search_memory_items,
        )

        total_items = count_memory_items(user_id)

        # Count by type
        by_type = {}
        for memory_type in [
            "preference",
            "fact",
            "procedural",
            "autobiographical",
            "relationship",
            "lesson",
        ]:
            count = count_memory_items(user_id, memory_type=memory_type)
            if count > 0:
                by_type[memory_type] = count

        # Count by contradiction state
        contradicted = get_contradicted_memories(user_id, limit=100)
        by_contradiction_state = {}
        for item in contradicted:
            state = item.contradiction_state
            by_contradiction_state[state] = by_contradiction_state.get(state, 0) + 1

        # Count by decay bucket
        all_items = search_memory_items(
            user_id=user_id,
            query="",
            memory_types=None,
            scopes=None,
            min_salience=0.0,
            min_confidence=0.0,
            exclude_archived=False,
            exclude_contradicted=False,
            limit=500,
        )
        by_decay_bucket = {}
        active_items = 0
        suppressed_items = 0
        reinforced_items = 0

        for item in all_items:
            bucket = item.decay_bucket
            by_decay_bucket[bucket] = by_decay_bucket.get(bucket, 0) + 1
            if not item.is_archived and not item.is_suppressed:
                active_items += 1
            if item.is_suppressed:
                suppressed_items += 1
            if item.reinforcement_count > 0:
                reinforced_items += 1

        # Top items
        top_salient = get_top_salient_memories(user_id, limit=10)
        top_reinforced = get_reinforced_memories(
            user_id, min_reinforcements=2, limit=10
        )
        top_procedures = get_procedures_for_user(user_id, limit=5)
        top_lessons = get_lessons_for_user(user_id, limit=5)

        # Summaries
        autobio_summary = build_autobiographical_summary(user_id, max_memories=20)
        relation_summary = build_relationship_summary(user_id)

        # Recent write-backs
        recent_writebacks = search_memory_items(
            user_id=user_id,
            query="",
            memory_types=None,
            scopes=None,
            min_salience=0.0,
            min_confidence=0.0,
            exclude_archived=True,
            exclude_contradicted=False,
            limit=10,
        )
        writebacks = [
            item
            for item in recent_writebacks
            if item.source_kind in ["agent_cycle", "chat_turn"]
        ][:5]

        return MemoryV2SummaryResponse(
            user_id=user_id,
            total_items=total_items,
            active_items=active_items,
            suppressed_items=suppressed_items,
            reinforced_items=reinforced_items,
            by_type=by_type,
            by_contradiction_state=by_contradiction_state,
            by_decay_bucket=by_decay_bucket,
            top_salient=top_salient,
            top_reinforced=top_reinforced,
            top_procedures=top_procedures,
            top_lessons=top_lessons,
            autobiographical_summary=autobio_summary,
            relation_summary=relation_summary,
            recent_writebacks=writebacks,
        )

    def list_procedures(
        self, user_id: str, *, limit: int = 20
    ) -> list[MemoryV2Procedure]:
        return get_procedures_for_user(user_id, limit=limit)

    def list_contradicted_memories(
        self, user_id: str, *, limit: int = 50
    ) -> list[MemoryV2Item]:
        return get_contradicted_memories(user_id, limit=limit)

    def autobiographical_plain_summary(self, user_id: str, max_memories: int) -> str:
        return build_autobiographical_summary(user_id, max_memories=max_memories)

    def compact_autobio_episodes(
        self, user_id: str, *, min_episode_count: int
    ) -> dict[str, Any]:
        from aihub.memory_v2_autobio import compact_episodic_memories

        return compact_episodic_memories(
            user_id=user_id,
            min_episode_count=min_episode_count,
        )

    def collect_identity_memory_facets(self, user_id: str) -> dict[str, Any]:
        """Structured memory slice for :mod:`aihub.runtime_identity_bridge` (single DB policy path)."""
        preference_items = search_memory_items(
            user_id=user_id,
            query="",
            memory_types=["preference"],
            scopes=None,
            min_salience=0.4,
            min_confidence=0.0,
            exclude_archived=True,
            exclude_contradicted=False,
            limit=5,
        )
        top_preferences = [
            {
                "id": item.id,
                "title": item.title,
                "salience_score": item.salience_score,
            }
            for item in preference_items
        ]
        procedures = get_procedures_for_user(user_id, limit=3)
        top_procedures = [
            {
                "id": proc.id,
                "name": proc.name,
                "recommended_strategy": proc.recommended_strategy,
                "success_rate": proc.success_rate,
            }
            for proc in procedures
        ]
        contradicted = get_contradicted_memories(user_id, limit=100)
        autobio = build_autobiographical_summary(user_id, max_memories=20)
        return {
            "top_preferences": top_preferences,
            "top_procedures": top_procedures,
            "contradictions_count": len(contradicted),
            "autobio_summary": autobio,
        }

    def build_cockpit_panel_payload(self, user_id: str) -> dict[str, Any]:
        """Operator panel for cockpit: summary plus writeback/stability aggregates from Memory V2."""
        summary = self.get_summary(user_id)

        recent_writebacks: list[dict[str, Any]] = []
        try:
            with _DB_LOCK, _conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, title, memory_type, source_kind, source_ref, created_ts
                    FROM memory_v2_items
                    WHERE user_id = ?
                      AND source_kind IN ('agent_cycle', 'chat_turn')
                    ORDER BY created_ts DESC
                    LIMIT 5
                """,
                    (user_id,),
                )
                rows = cursor.fetchall()
                for row in rows:
                    recent_writebacks.append(
                        {
                            "id": row[0],
                            "title": row[1],
                            "memory_type": row[2],
                            "source_kind": row[3],
                            "source_ref": row[4],
                            "created_ts": row[5],
                        }
                    )
        except Exception:  # noqa: BLE001
            recent_writebacks = []

        stability_by_tier: dict[str, int] = {
            "transient": 0,
            "developing": 0,
            "stable": 0,
        }
        suppressed_active_count = 0
        try:
            with _DB_LOCK, _conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT stability_tier, COUNT(*) FROM memory_v2_items
                    WHERE user_id = ? AND is_archived = 0
                    GROUP BY stability_tier
                    """,
                    (user_id,),
                )
                for row in cur.fetchall():
                    tier = row[0] if row[0] else "transient"
                    if tier in stability_by_tier:
                        stability_by_tier[tier] = int(row[1])
                cur.execute(
                    """
                    SELECT COUNT(*) FROM memory_v2_items
                    WHERE user_id = ? AND is_archived = 0 AND is_suppressed != 0
                    """,
                    (user_id,),
                )
                suppressed_active_count = int(cur.fetchone()[0])
        except Exception:  # noqa: BLE001
            stability_by_tier = {"transient": 0, "developing": 0, "stable": 0}
            suppressed_active_count = 0

        return {
            "user_id": summary.user_id,
            "total_items": summary.total_items,
            "by_type": summary.by_type,
            "by_contradiction_state": summary.by_contradiction_state,
            "top_salient": [
                {
                    "id": item.id,
                    "title": item.title,
                    "memory_type": item.memory_type,
                    "salience_score": item.salience_score,
                    "contradiction_state": item.contradiction_state,
                }
                for item in summary.top_salient[:5]
            ],
            "top_procedures": [
                {
                    "id": proc.id,
                    "name": proc.name,
                    "recommended_strategy": proc.recommended_strategy,
                    "success_rate": proc.success_rate,
                    "confidence_score": proc.confidence_score,
                }
                for proc in summary.top_procedures[:3]
            ],
            "autobiographical_summary": summary.autobiographical_summary,
            "relation_summary": summary.relation_summary,
            "recent_writebacks": recent_writebacks,
            "stability_tier_counts": stability_by_tier,
            "suppressed_active_count": suppressed_active_count,
        }

    def reinforce_memory(
        self, memory_id: str, user_id: str, boost_factor: float = 0.1
    ) -> MemoryV2Item | None:
        """
        Reinforce memory by updating access timestamps and scores.

        Returns updated item if successful.
        """
        item = get_memory_item(memory_id, user_id)
        if not item:
            logger.warning(f"Memory item {memory_id} not found for reinforcement")
            return None
        now = time.time()

        item.last_accessed_ts = now
        item.last_reinforced_ts = now
        item.recurrence_score = min(1.0, item.recurrence_score + boost_factor)
        item.salience_score = calculate_salience(
            item.importance_score,
            item.recurrence_score,
            item.emotional_weight,
            item.identity_relevance_score,
            item.confidence_score,
            item.freshness_score,
        )
        item.updated_ts = now

        if update_memory_item(item):
            return item
        return None

    def consolidate_user_memory(self, user_id: str) -> dict[str, Any]:
        """
        Execute consolidation for user memories.

        Returns consolidation summary.
        """
        result = consolidate_episodic_memories(user_id, scope="session")

        if not result:
            return {
                "consolidated": False,
                "reason": "Insufficient episodic memories for consolidation",
            }

        return {
            "consolidated": True,
            "consolidation_id": result.consolidation_id,
            "input_count": len(result.input_memory_ids),
            "output_memory_id": result.output_memory_id,
            "compression_ratio": result.compression_ratio,
            "summary": result.summary,
        }

    def extract_procedures(self, user_id: str) -> list[MemoryV2Procedure]:
        """
        Extract procedures from user's experience history.

        Returns list of learned procedures.
        """
        procedures = extract_procedures_from_experiences(user_id, min_evidence=3)

        # Persist new procedures
        from aihub.memory_v2_repository import insert_memory_procedure

        for proc in procedures:
            insert_memory_procedure(proc)

        logger.info(
            f"Extracted and persisted {len(procedures)} procedures for user {user_id}"
        )
        return procedures

    def record_agent_outcome(
        self,
        user_id: str,
        cycle_id: str,
        strategy: str,
        ok: bool,
        action_summary: str,
        errors: list[dict[str, Any]],
        goal_progress_changed: bool = False,
        contradictions_present: int = 0,
        procedures_active: int = 0,
        duration_ms: float = 0.0,
    ) -> dict[str, Any]:
        """
        Record agent cycle outcome to Memory V2.

        Returns write-back summary with counts of new items/lessons/procedures.
        """
        now = time.time()
        result = {
            "attempted": True,
            "succeeded": False,
            "new_lessons_count": 0,
            "new_procedures_count": 0,
            "new_items_count": 0,
            "writeback_kind": None,
        }

        try:
            # Success path: record lesson/procedure candidate
            if ok:
                # If procedures were active and contributed to success, reinforce
                if procedures_active > 0:
                    lesson_item = self.create_memory_item(
                        user_id=user_id,
                        memory_type="procedural",
                        scope="workflow",
                        title=f"Successful workflow with {strategy}",
                        content=f"Strategy {strategy} succeeded: {action_summary}",
                        source_kind="agent_cycle",
                        source_ref=cycle_id,
                        importance_score=0.6,
                        confidence_score=0.8,
                        auto_detect_contradictions=False,
                    )
                    if lesson_item:
                        result["new_items_count"] += 1
                        result["writeback_kind"] = "procedure_reinforcement"
                        logger.info(
                            f"V2 write-back: procedure reinforced for user {user_id}"
                        )

                # If goal progress changed, record significant outcome
                if goal_progress_changed:
                    progress_item = self.create_memory_item(
                        user_id=user_id,
                        memory_type="autobiographical",
                        scope="workflow",
                        title="Goal progress achieved",
                        content=f"Made progress using {strategy}: {action_summary}",
                        source_kind="agent_cycle",
                        source_ref=cycle_id,
                        importance_score=0.7,
                        emotional_weight=0.3,
                        confidence_score=0.8,
                        auto_detect_contradictions=False,
                    )
                    if progress_item:
                        result["new_items_count"] += 1
                        result["writeback_kind"] = "progress_milestone"
                        logger.info(
                            f"V2 write-back: progress recorded for user {user_id}"
                        )

            # Failure path: record lesson with failure mode
            else:
                if errors:
                    error_summary = "; ".join(
                        e.get("message", "unknown") for e in errors[:3]
                    )
                    failure_item = self.create_memory_item(
                        user_id=user_id,
                        memory_type="lesson",
                        scope="workflow",
                        title=f"Failed with {strategy}",
                        content=f"Strategy {strategy} failed: {error_summary}. Action: {action_summary}",
                        source_kind="agent_cycle",
                        source_ref=cycle_id,
                        importance_score=0.7,
                        emotional_weight=0.2,
                        confidence_score=0.7,
                        auto_detect_contradictions=False,
                    )
                    if failure_item:
                        result["new_lessons_count"] += 1
                        result["writeback_kind"] = "failure_lesson"
                        logger.info(f"V2 write-back: failure lesson for user {user_id}")

            # Contradiction handling
            if contradictions_present > 0 and not ok:
                # Contradiction may have caused confusion
                contradiction_item = self.create_memory_item(
                    user_id=user_id,
                    memory_type="lesson",
                    scope="interaction",
                    title="Contradiction impact",
                    content=f"{contradictions_present} contradictions present during failed cycle",
                    source_kind="agent_cycle",
                    source_ref=cycle_id,
                    importance_score=0.6,
                    confidence_score=0.6,
                    auto_detect_contradictions=False,
                )
                if contradiction_item:
                    result["new_lessons_count"] += 1
                    result["writeback_kind"] = "contradiction_feedback"

            # Reinforce relevant existing memories
            from aihub.memory_v2_repository import search_memory_items

            related_memories = search_memory_items(
                user_id=user_id,
                query=strategy,
                memory_types=["procedural", "lesson"],
                scopes=None,
                min_salience=0.3,
                min_confidence=0.3,
                exclude_archived=True,
                exclude_contradicted=True,
                limit=5,
            )

            reinforced_ids = []
            for mem in related_memories:
                reinforced_ids.append(mem.id)

            if reinforced_ids:
                reinf_result = self.reinforce_outcome(
                    user_id, reinforced_ids, success=ok
                )
                logger.debug(
                    f"V2: reinforced {reinf_result.get('reinforced_count', 0)} memories user={user_id}"
                )

            result["succeeded"] = True

        except Exception as e:
            logger.error(f"V2 agent outcome write-back failed: {e}", exc_info=True)
            result["succeeded"] = False

        return result

    def record_chat_outcome(
        self,
        user_id: str,
        turn_id: str,
        query_text: str,
        response_text: str,
        strategy: str,
        grounding_mode: str,
        tool_calls_count: int,
        tool_successes: int,
        tool_failures: int,
        contradictions_present: int = 0,
        memory_matches: int = 0,
        degraded: bool = False,
        fallback: bool = False,
    ) -> dict[str, Any]:
        """
        Record chat turn outcome to Memory V2.

        Returns write-back summary.
        """
        now = time.time()
        result = {
            "attempted": True,
            "succeeded": False,
            "new_items_count": 0,
            "new_lessons_count": 0,
            "writeback_kind": None,
        }

        try:
            # Skip durable memory for trivial meta/identity chatter.
            try:
                from aihub.turn.prompt_budget import is_trivial_meta_memory_content

                if is_trivial_meta_memory_content(query_text or "", query=query_text or "") or is_trivial_meta_memory_content(
                    response_text or "", query=query_text or ""
                ):
                    result["attempted"] = False
                    result["succeeded"] = False
                    result["writeback_kind"] = "skipped_trivial_meta"
                    result["skipped_reason"] = "trivial_meta_or_identity_chatter"
                    return result
            except Exception as trivial_skip_exc:
                logger.debug("trivial meta memory filter skipped: %s", trivial_skip_exc)
            # Strong signal: controlled web + success
            if grounding_mode == "controlled_web" and tool_successes > 0:
                web_item = self.create_memory_item(
                    user_id=user_id,
                    memory_type="fact",
                    scope="interaction",
                    title="Web-grounded outcome",
                    content=f"Query: {query_text[:100]}. Grounded with {tool_successes} sources.",
                    source_kind="chat_turn",
                    source_ref=turn_id,
                    importance_score=0.6,
                    confidence_score=0.8,
                    auto_detect_contradictions=True,
                )
                if web_item:
                    result["new_items_count"] += 1
                    result["writeback_kind"] = "web_grounded"
                    logger.info(f"V2 chat write-back: web-grounded for user {user_id}")

            # Memory match reinforcement — reinforce existing hits only.
            # Do NOT create "Memory-guided response" meta-items: they pollute
            # retrieval with preference-ranked junk and starve real facts.
            if memory_matches > 0 and not fallback:
                result["writeback_kind"] = result.get("writeback_kind") or "memory_reinforcement_soft"
                logger.debug(
                    "V2 chat write-back: skip Memory-guided meta item (matches=%s)",
                    memory_matches,
                )

            # Degraded/fallback outcome
            if degraded or fallback:
                degraded_item = self.create_memory_item(
                    user_id=user_id,
                    memory_type="lesson",
                    scope="interaction",
                    title="Degraded response outcome",
                    content=f"Response was degraded/fallback for: {query_text[:80]}",
                    source_kind="chat_turn",
                    source_ref=turn_id,
                    importance_score=0.6,
                    emotional_weight=0.1,
                    confidence_score=0.6,
                    auto_detect_contradictions=False,
                )
                if degraded_item:
                    result["new_lessons_count"] += 1
                    result["writeback_kind"] = "degraded_outcome"
                    logger.info(
                        f"V2 chat write-back: degraded outcome for user {user_id}"
                    )

            # Contradiction follow-up
            if contradictions_present > 0 and not fallback:
                contradiction_item = self.create_memory_item(
                    user_id=user_id,
                    memory_type="lesson",
                    scope="interaction",
                    title="Contradiction context",
                    content=f"{contradictions_present} contradictions during turn: {query_text[:60]}",
                    source_kind="chat_turn",
                    source_ref=turn_id,
                    importance_score=0.5,
                    confidence_score=0.6,
                    auto_detect_contradictions=False,
                )
                if contradiction_item:
                    result["new_lessons_count"] += 1
                    result["writeback_kind"] = "contradiction_followup"

            # Reinforce memories used in this turn
            if memory_matches > 0 and not fallback:
                from aihub.memory_v2_repository import search_memory_items

                related = search_memory_items(
                    user_id=user_id,
                    query=query_text[:100],
                    memory_types=None,
                    scopes=None,
                    min_salience=0.3,
                    min_confidence=0.3,
                    exclude_archived=True,
                    exclude_contradicted=True,
                    limit=3,
                )

                reinf_ids = [m.id for m in related[:memory_matches]]
                if reinf_ids:
                    reinf_result = self.reinforce_outcome(
                        user_id, reinf_ids, success=not degraded
                    )
                    logger.debug(
                        f"V2 chat: reinforced {reinf_result.get('reinforced_count', 0)} memories user={user_id}"
                    )

            result["succeeded"] = True

        except Exception as e:
            logger.error(f"V2 chat outcome write-back failed: {e}", exc_info=True)
            result["succeeded"] = False

        return result

    def reinforce_outcome(
        self,
        user_id: str,
        memory_ids: list[str],
        success: bool,
    ) -> dict[str, Any]:
        """
        Reinforce memory items after outcome.

        Called post-execution to strengthen or weaken memories based on result.
        """
        from aihub.memory_v2_repository import get_memory_item, reinforce_memory_item
        from aihub.memory_v2_scoring import (
            calculate_outcome_reinforcement,
            calculate_retrieval_priority,
        )

        reinforced_count = 0

        for mem_id in memory_ids:
            if reinforce_memory_item(
                mem_id, user_id, success, recurrence_boost=0.05, salience_boost=0.03
            ):
                reinforced_count += 1

                # Recalculate outcome_reinforcement_score and retrieval_priority
                item = get_memory_item(mem_id, user_id)
                if item:
                    item.outcome_reinforcement_score = calculate_outcome_reinforcement(
                        item.success_reinforcements,
                        item.failure_reinforcements,
                        item.reinforcement_count,
                    )
                    item.retrieval_priority_score = calculate_retrieval_priority(
                        item.salience_score,
                        item.recurrence_score,
                        item.freshness_score,
                        item.identity_relevance_score,
                        item.relation_relevance_score,
                        item.outcome_reinforcement_score,
                        item.source_reliability_score,
                        item.contradiction_state,
                        item.is_pinned,
                        item.is_suppressed,
                        item.decay_bucket,
                    )
                    item.updated_ts = now_ts()
                    from aihub.memory_v2_repository import update_memory_item
                    from aihub.memory_v2_scoring import (
                        ARCHIVE_STALE_SEC,
                        apply_stability_evaluation,
                    )

                    stable_item = apply_stability_evaluation(item)
                    now_ts_v = now_ts()
                    last_touch = float(
                        stable_item.last_reinforced_ts or stable_item.created_ts
                    )
                    if (
                        stable_item.decay_bucket == "archive_candidate"
                        and (now_ts_v - last_touch) > ARCHIVE_STALE_SEC
                        and stable_item.reinforcement_count < 2
                    ):
                        stable_item = stable_item.model_copy(
                            update={"is_archived": True}
                        )
                    update_memory_item(stable_item)

        return {
            "attempted": True,
            "reinforced_count": reinforced_count,
            "success": success,
        }

    def run_forgetting_sweep(
        self,
        user_id: str,
        suppress_threshold: float = 0.15,
    ) -> dict[str, Any]:
        """
        Run controlled forgetting sweep: suppress low-value stale items.

        Does NOT delete, only marks is_suppressed=True.
        """
        from aihub.memory_v2_repository import (
            mark_memory_suppressed,
            search_memory_items,
        )
        from aihub.memory_v2_scoring import calculate_retrieval_priority

        # Get all active items
        all_items = search_memory_items(
            user_id=user_id,
            query="",
            memory_types=None,
            scopes=None,
            min_salience=0.0,
            min_confidence=0.0,
            exclude_archived=True,
            exclude_contradicted=False,
            limit=500,
        )

        suppressed_count = 0
        candidates = []

        for item in all_items:
            if item.is_pinned or item.is_suppressed:
                continue

            # Calculate retrieval priority
            priority = calculate_retrieval_priority(
                item.salience_score,
                item.recurrence_score,
                item.freshness_score,
                item.identity_relevance_score,
                item.relation_relevance_score,
                item.outcome_reinforcement_score,
                item.source_reliability_score,
                item.contradiction_state,
                item.is_pinned,
                item.is_suppressed,
                item.decay_bucket,
            )

            # Suppression criteria: low priority + stale + no recent reinforcement
            now = now_ts()
            age = now - item.created_ts
            reinforcement_age = (
                (now - item.last_reinforced_ts) if item.last_reinforced_ts else age
            )

            if (
                priority < suppress_threshold
                and reinforcement_age > 30 * 86400  # 30 days
                and item.reinforcement_count < 2
            ):
                candidates.append(item.id)

        # Suppress candidates
        for item_id in candidates:
            if mark_memory_suppressed(item_id, user_id, suppressed=True):
                suppressed_count += 1

        logger.info(
            f"Forgetting sweep: user={user_id} evaluated={len(all_items)} suppressed={suppressed_count}"
        )

        return {
            "ok": True,
            "evaluated_count": len(all_items),
            "suppressed_count": suppressed_count,
            "threshold": suppress_threshold,
        }


    def process_index_jobs(self, user_id: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Retry pending/stale/failed Memory V2 vector indexing jobs."""
        return process_index_jobs(user_id=user_id, limit=limit)

    def get_index_job_summary(self, user_id: str | None = None) -> dict[str, Any]:
        """Return durable Memory V2 vector indexing outbox status."""
        return index_job_summary(user_id=user_id)

    def get_retrieval_explanation(
        self,
        user_id: str,
        query: str,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """
        Explain memory retrieval reasoning.

        Uses the same :meth:`search` path and filters as default V2 ranked retrieval
        (so cockpit and HTTP search share one ranking semantics).
        """
        lim = max(1, min(int(top_n), 100))
        req = MemoryV2SearchRequest(
            user_id=user_id,
            query=query,
            memory_types=None,
            scopes=None,
            min_salience=0.0,
            min_confidence=0.0,
            exclude_archived=True,
            exclude_contradicted=False,
            limit=lim,
        )
        resp = self.search(req)
        return _retrieval_explanation_dict_from_items(user_id, query, resp.items)
