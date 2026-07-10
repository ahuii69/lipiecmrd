from __future__ import annotations

"""Hybrid retrieval for Memory V2.

Combines lexical SQL candidates, priority fallback candidates and dense vector
hits.  It does not replace Memory V2 lifecycle/scoring; it only fixes candidate
recall so the existing rich memory model can actually be found by paraphrases.
"""

import logging
from typing import Dict, Iterable, List, Optional

from aihub.memory_psyche_contracts import MemoryScope, MemoryType
from aihub.memory_v2_models import MemoryV2Item
from aihub.memory_v2_repository import get_memory_item, search_memory_items

logger = logging.getLogger(__name__)


def _passes_filters(
    item: MemoryV2Item,
    *,
    memory_types: list[MemoryType] | None,
    scopes: list[MemoryScope] | None,
    min_salience: float,
    min_confidence: float,
    exclude_archived: bool,
    exclude_contradicted: bool,
) -> bool:
    if exclude_archived and item.is_archived:
        return False
    if exclude_contradicted and item.contradiction_state != "none":
        return False
    if memory_types and item.memory_type not in memory_types:
        return False
    if scopes and item.scope not in scopes:
        return False
    if item.salience_score < min_salience:
        return False
    if item.confidence_score < min_confidence:
        return False
    return True


def _add_scores(scores: Dict[str, float], items: Dict[str, MemoryV2Item], ranked: Iterable[MemoryV2Item], weight: float) -> None:
    for rank, item in enumerate(ranked, start=1):
        items[item.id] = item
        scores[item.id] = scores.get(item.id, 0.0) + weight / (60.0 + rank)


def _dense_candidates(user_id: str, query: str, limit: int) -> list[MemoryV2Item]:
    if not query.strip():
        return []
    try:
        from aihub.vector_engine import search as vector_search

        resp = vector_search(query, k=max(limit, 1) * 3, user_id=user_id)
        if not resp.get("ok"):
            logger.info("memory_v2_dense_unavailable error=%s", resp.get("error"))
            return []
        out: list[MemoryV2Item] = []
        seen: set[str] = set()
        for hit in resp.get("results", []):
            if hit.get("source") != "memory_v2":
                continue
            mid = hit.get("external_id")
            if not mid or mid in seen:
                continue
            item = get_memory_item(str(mid), user_id)
            if item is not None:
                seen.add(item.id)
                out.append(item)
        return out
    except Exception:  # noqa: BLE001
        logger.warning("memory_v2_dense_search_failed", exc_info=True)
        return []


def hybrid_search_memory_items(
    *,
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
    limit = max(1, int(limit or 20))
    expanded = max(limit * 4, 20)

    lexical = search_memory_items(
        user_id=user_id,
        query=query,
        memory_types=memory_types,
        scopes=scopes,
        min_salience=min_salience,
        min_confidence=min_confidence,
        exclude_archived=exclude_archived,
        exclude_contradicted=exclude_contradicted,
        limit=expanded,
    )
    priority = search_memory_items(
        user_id=user_id,
        query="",
        memory_types=memory_types,
        scopes=scopes,
        min_salience=min_salience,
        min_confidence=min_confidence,
        exclude_archived=exclude_archived,
        exclude_contradicted=exclude_contradicted,
        limit=expanded,
    )
    dense = [
        item
        for item in _dense_candidates(user_id, query, expanded)
        if _passes_filters(
            item,
            memory_types=memory_types,
            scopes=scopes,
            min_salience=min_salience,
            min_confidence=min_confidence,
            exclude_archived=exclude_archived,
            exclude_contradicted=exclude_contradicted,
        )
    ]

    scores: dict[str, float] = {}
    items: dict[str, MemoryV2Item] = {}
    _add_scores(scores, items, dense, 1.4)
    _add_scores(scores, items, lexical, 1.1)
    _add_scores(scores, items, priority, 0.45 if query.strip() else 1.0)

    def final_score(item: MemoryV2Item) -> float:
        return (
            scores.get(item.id, 0.0)
            + item.retrieval_priority_score * 0.25
            + item.salience_score * 0.20
            + item.confidence_score * 0.10
            + item.freshness_score * 0.05
        )

    ranked = sorted(items.values(), key=final_score, reverse=True)
    return ranked[:limit]


def index_memory_item(item: MemoryV2Item) -> str | None:
    """Index a Memory V2 item into the canonical vector engine and return a ref."""
    text = "\n".join(part for part in [item.title, item.summary, item.content] if part)
    if not text.strip():
        return None
    try:
        from aihub.vector_engine import add_memory

        resp = add_memory(
            text,
            user_id=item.user_id,
            external_id=item.id,
            source="memory_v2",
            metadata={"memory_type": item.memory_type, "scope": item.scope},
        )
        if not resp.get("ok"):
            logger.warning("memory_v2_index_failed id=%s error=%s", item.id, resp.get("error"))
            return None
        return f"vector:{resp.get('backend', 'unknown')}:{resp.get('vector_id')}"
    except Exception:  # noqa: BLE001
        logger.warning("memory_v2_index_exception id=%s", item.id, exc_info=True)
        return None
