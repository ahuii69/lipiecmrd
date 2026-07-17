#!/usr/bin/env python3
"""
Runtime Memory V2 Bridge.

Provides read-only snapshots of Memory V2 for active runtime consumption.
"""

import logging
from typing import Any

from aihub.memory_psyche_contracts import MemoryV2RuntimeContext
from aihub.memory_v2_models import MemoryV2SearchRequest
from aihub.memory_v2_repository import count_memory_items, search_memory_items
from aihub.memory_v2_scoring import (
    MIN_REINFORCEMENT_FOR_RUNTIME_WEIGHT,
    classify_memory_horizon_kind,
    effective_procedure_confidence,
    is_runtime_actionable_contradiction,
    is_transient_contradiction_item,
    memory_item_to_stability_meta,
    tier_runtime_weight,
)

logger = logging.getLogger(__name__)


def _canonical_v2():
    """Memory V2 service owned by :func:`aihub.memory_core.get_memory_core` (single instance)."""
    from aihub.memory_core import get_memory_core

    return get_memory_core().v2_service


def _core_v2_search(request: MemoryV2SearchRequest):
    """V2 ranked retrieval via :meth:`MemoryCanonicalCore.v2_search` (same read core as HTTP)."""
    from aihub.memory_core import get_memory_core

    return get_memory_core().v2_search(request)


def build_memory_v2_runtime_snapshot(
    user_id: str, query_text: str = ""
) -> dict[str, Any]:
    """
    Build runtime snapshot of Memory V2 for chat/agent decision context.

    Returns lightweight summary optimized for runtime consumption.
    """
    try:
        # Get top salient memories
        request = MemoryV2SearchRequest(
            user_id=user_id,
            query=query_text,
            min_salience=0.3,
            exclude_archived=True,
            limit=10,
        )
        search_result = _core_v2_search(request)

        # Query-ranked procedures (user-declared debug flows beat stale dumps)
        from aihub.memory_v2_procedural import rank_procedures_for_query

        procedures = rank_procedures_for_query(user_id, query_text or "", limit=5)

        # Count contradictions - use direct query not filtered search results
        all_items = search_memory_items(
            user_id=user_id,
            exclude_archived=True,
            limit=500,
        )
        contradictions_count = sum(
            1 for item in all_items if item.contradiction_state != "none"
        )
        actionable_contradictions_count = sum(
            1 for item in all_items if is_runtime_actionable_contradiction(item)
        )
        transient_contradiction_count = sum(
            1 for item in all_items if is_transient_contradiction_item(item)
        )

        # Count reinforced items
        reinforced_count = sum(1 for item in all_items if item.reinforcement_count > 0)

        # Count suppressed items
        suppressed_count = sum(1 for item in all_items if item.is_suppressed)

        # Retrieval explanation
        retrieval_explanation = _canonical_v2().get_retrieval_explanation(
            user_id, query_text, top_n=5
        )

        eff_confs: list[float] = []
        for p in procedures:
            succ = int(round(p.evidence_count * max(0.0, min(1.0, p.success_rate))))
            fail = max(0, p.evidence_count - succ)
            eff_confs.append(
                effective_procedure_confidence(
                    p.confidence_score, p.evidence_count, succ, fail
                )
            )
        avg_procedure_confidence = sum(eff_confs) / len(eff_confs) if eff_confs else 0.0

        return {
            "loaded": True,
            "match_count": len(search_result.items),
            "reinforced_count": reinforced_count,
            "suppressed_count": suppressed_count,
            "top_reason_codes": retrieval_explanation.get("top_reason_codes", []),
            "retrieval_strategy": retrieval_explanation.get("retrieval_strategy", ""),
            "top_memories": [
                {
                    "id": item.id,
                    "title": item.title,
                    "memory_type": item.memory_type,
                    "salience_score": item.salience_score,
                    "retrieval_priority_score": item.retrieval_priority_score,
                    "reinforcement_count": item.reinforcement_count,
                    "stability_tier": item.stability_tier,
                }
                for item in search_result.items[:5]
            ],
            "procedures_count": len(procedures),
            "avg_procedure_confidence": avg_procedure_confidence,
            "contradictions_count": contradictions_count,
            "actionable_contradictions_count": actionable_contradictions_count,
            "transient_contradiction_count": transient_contradiction_count,
            "total_items": count_memory_items(user_id),
        }
    except Exception as e:
        logger.warning(f"Failed to build memory v2 snapshot: {e}")
        return {
            "loaded": False,
            "error": str(e),
            "match_count": 0,
            "procedures_count": 0,
            "contradictions_count": 0,
        }


def summarize_memory_v2_for_chat(user_id: str, query_text: str = "") -> str:
    """
    Generate text summary of Memory V2 for chat context injection.

    Returns compact text suitable for LLM context.
    """
    try:
        snapshot = build_memory_v2_runtime_snapshot(user_id, query_text)

        if not snapshot.get("loaded"):
            return ""

        summary_parts = []

        if snapshot.get("match_count", 0) > 0:
            summary_parts.append(f"{snapshot['match_count']} relevant memories")

        if snapshot.get("procedures_count", 0) > 0:
            summary_parts.append(f"{snapshot['procedures_count']} learned procedures")

        if snapshot.get("contradictions_count", 0) > 0:
            summary_parts.append(f"{snapshot['contradictions_count']} contradictions")

        return (
            " | ".join(summary_parts)
            if summary_parts
            else "No significant memory context."
        )
    except Exception as e:
        logger.warning(f"Failed to summarize memory v2 for chat: {e}")
        return ""


def summarize_memory_v2_for_agent(user_id: str, query_text: str = "") -> dict[str, Any]:
    """
    Generate structured summary of Memory V2 for agent decision context.

    Returns dict with memory insights for strategy selection.
    """
    try:
        snapshot = build_memory_v2_runtime_snapshot(user_id, query_text)

        if not snapshot.get("loaded"):
            return {"available": False}

        return {
            "available": True,
            "match_count": snapshot.get("match_count", 0),
            "procedures_count": snapshot.get("procedures_count", 0),
            "contradictions_count": snapshot.get("contradictions_count", 0),
            "actionable_contradictions_count": snapshot.get(
                "actionable_contradictions_count", 0
            ),
            "transient_contradiction_count": snapshot.get(
                "transient_contradiction_count", 0
            ),
            "reinforced_count": snapshot.get("reinforced_count", 0),
            "suppressed_count": snapshot.get("suppressed_count", 0),
            "top_reason_codes": snapshot.get("top_reason_codes", []),
            "total_items": snapshot.get("total_items", 0),
            "top_memory_titles": [
                m["title"] for m in snapshot.get("top_memories", [])[:3]
            ],
        }
    except Exception as e:
        logger.warning(f"Failed to summarize memory v2 for agent: {e}")
        return {"available": False, "error": str(e)}


def build_memory_v2_runtime_context(
    user_id: str, query_text: str = ""
) -> MemoryV2RuntimeContext:
    """
    Build enriched Memory V2 runtime context for behavior injection.

    Returns production context with facts, preferences, procedures, contradictions,
    and confidence modifiers for real runtime use.
    """
    try:
        # Get top salient memories
        request = MemoryV2SearchRequest(
            user_id=user_id,
            query=query_text,
            min_salience=0.3,
            exclude_archived=True,
            limit=20,
        )
        search_result = _core_v2_search(request)

        from aihub.memory_v2_procedural import rank_procedures_for_query

        procedures = rank_procedures_for_query(user_id, query_text or "", limit=10)

        # Get contradictions (independently of search results)
        contradicted_items = _canonical_v2().list_contradicted_memories(
            user_id, limit=10
        )
        contradiction_alerts: list[str] = []
        transient_contradiction_hints: list[str] = []
        for item in contradicted_items[:10]:
            label = f"{item.title} ({item.contradiction_state})"
            if is_runtime_actionable_contradiction(item):
                contradiction_alerts.append(label)
            elif item.contradiction_state != "none":
                transient_contradiction_hints.append(label)
        contradiction_alerts = contradiction_alerts[:3]
        transient_contradiction_hints = transient_contradiction_hints[:5]

        facts_all = [i for i in search_result.items if i.memory_type == "fact"]
        facts_all.sort(
            key=lambda i: i.salience_score
            * tier_runtime_weight(i.stability_tier, i.reinforcement_count),
            reverse=True,
        )
        facts = facts_all[:5]

        pref_all = [i for i in search_result.items if i.memory_type == "preference"]
        pref_all.sort(
            key=lambda i: i.salience_score
            * tier_runtime_weight(i.stability_tier, i.reinforcement_count),
            reverse=True,
        )
        preferences = pref_all[:5]

        reinforced = [
            item
            for item in search_result.items
            if item.reinforcement_count > 2
            and (item.stability_tier != "transient" or item.reinforcement_count >= 5)
        ]
        reinforced_patterns = [item.title for item in reinforced[:5]]

        # Get autobiographical summary from identity bridge
        from aihub.runtime_identity_bridge import build_identity_bridge_snapshot

        identity = build_identity_bridge_snapshot(user_id, query_text)
        autobio_summary = identity.autobio_summary if identity else ""

        eff_list: list[float] = []
        for p in procedures:
            succ = int(round(p.evidence_count * max(0.0, min(1.0, p.success_rate))))
            fail = max(0, p.evidence_count - succ)
            eff_list.append(
                effective_procedure_confidence(
                    p.confidence_score, p.evidence_count, succ, fail
                )
            )
        confidence_modifier_raw = (
            sum(p.confidence_score for p in procedures) / len(procedures)
            if procedures
            else 0.5
        )
        avg_proc_confidence = (
            sum(eff_list) / len(eff_list) if eff_list else confidence_modifier_raw
        )

        stability_tier_counts: dict[str, int] = {
            "transient": 0,
            "developing": 0,
            "stable": 0,
        }
        for item in search_result.items:
            st = item.stability_tier
            if st in stability_tier_counts:
                stability_tier_counts[st] += 1

        # Retrieval explanation
        retrieval_explanation = _canonical_v2().get_retrieval_explanation(
            user_id, query_text, top_n=5
        )

        top_procedures_list: list[dict[str, Any]] = []
        for proc in procedures[:5]:
            succ = int(
                round(proc.evidence_count * max(0.0, min(1.0, proc.success_rate)))
            )
            fail = max(0, proc.evidence_count - succ)
            top_procedures_list.append(
                {
                    "id": proc.id,
                    "name": proc.name,
                    "trigger_pattern": proc.trigger_pattern,
                    "steps": (proc.recommended_strategy or "")[:500],
                    "recommended_strategy": proc.recommended_strategy,
                    "confidence": effective_procedure_confidence(
                        proc.confidence_score,
                        proc.evidence_count,
                        succ,
                        fail,
                    ),
                    "confidence_raw": proc.confidence_score,
                    "evidence_count": proc.evidence_count,
                }
            )

        all_items_ctx = search_memory_items(
            user_id=user_id,
            exclude_archived=True,
            limit=300,
        )
        actionable_cc = sum(
            1 for i in all_items_ctx if is_runtime_actionable_contradiction(i)
        )
        transient_cc = sum(
            1 for i in all_items_ctx if is_transient_contradiction_item(i)
        )

        stable_memory_operational: list[dict[str, Any]] = []
        transient_memory_operational: list[dict[str, Any]] = []
        suppressed_memory_operational: list[dict[str, Any]] = []
        for it in all_items_ctx:
            meta = memory_item_to_stability_meta(it)
            if it.is_suppressed or it.decay_bucket == "archive_candidate":
                if len(suppressed_memory_operational) < 14:
                    suppressed_memory_operational.append(meta)
                continue
            if it.stability_tier in ("developing", "stable") and (
                it.reinforcement_count >= MIN_REINFORCEMENT_FOR_RUNTIME_WEIGHT
            ):
                if len(stable_memory_operational) < 12:
                    stable_memory_operational.append(meta)
            elif it.stability_tier == "transient" or it.scope in (
                "session",
                "interaction",
            ):
                if len(transient_memory_operational) < 12:
                    transient_memory_operational.append(meta)

        ranked = sorted(
            search_result.items,
            key=lambda x: x.retrieval_priority_score,
            reverse=True,
        )
        promotion_audit = [memory_item_to_stability_meta(i) for i in ranked[:10]]

        def _mem_row(item: Any) -> dict[str, Any]:
            return {
                "id": item.id,
                "title": item.title,
                "content": item.content,
                "salience": item.salience_score,
                "reinforcement": item.reinforcement_count,
                "stability_tier": item.stability_tier,
                "horizon_kind": classify_memory_horizon_kind(
                    item.memory_type,
                    item.scope,
                    item.source_kind,
                    item.contradiction_state,
                    item.stability_tier,
                    item.reinforcement_count,
                ),
                "runtime_weight": tier_runtime_weight(
                    item.stability_tier, item.reinforcement_count
                ),
            }

        return MemoryV2RuntimeContext(
            loaded=True,
            top_facts=[_mem_row(item) for item in facts],
            top_preferences=[_mem_row(item) for item in preferences],
            top_procedures=top_procedures_list,
            contradiction_alerts=contradiction_alerts,
            autobiographical_summary=autobio_summary[:300],
            reinforced_patterns=reinforced_patterns,
            retrieval_reason_codes=retrieval_explanation.get("top_reason_codes", []),
            confidence_modifier=avg_proc_confidence,
            total_items=count_memory_items(user_id),
            stability_tier_counts=stability_tier_counts,
            transient_contradiction_hints=transient_contradiction_hints,
            confidence_modifier_raw=confidence_modifier_raw,
            actionable_contradictions_count=actionable_cc,
            transient_contradiction_count=transient_cc,
            stable_memory_operational=stable_memory_operational,
            transient_memory_operational=transient_memory_operational,
            suppressed_memory_operational=suppressed_memory_operational,
            promotion_audit=promotion_audit,
        )
    except Exception as e:
        logger.warning(f"Failed to build memory v2 runtime context: {e}")
        return MemoryV2RuntimeContext(
            loaded=False,
            top_facts=[],
            top_preferences=[],
            top_procedures=[],
            contradiction_alerts=[],
            autobiographical_summary="",
            reinforced_patterns=[],
            retrieval_reason_codes=[],
            confidence_modifier=0.5,
            total_items=0,
        )
