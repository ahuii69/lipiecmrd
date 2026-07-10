#!/usr/bin/env python3
"""Canonical read specification and outcome for all memory retrieval entrypoints.

HTTP legacy search, Memory V2 POST search, tools, and runtime bridges must map
to :class:`MemoryReadSpec` and consume :class:`MemoryReadOutcome` (or legacy
dict adapters built from it) so ranking and filters share one orchestration path
in :meth:`aihub.memory_core.MemoryCanonicalCore.read_memory`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from aihub.memory_v2_models import MemoryV2SearchRequest, MemoryV2SearchResponse


class MemoryReadSpec(BaseModel):
    """Single input contract for memory read orchestration."""

    user_id: str
    query: str = ""
    include_graph: bool = True
    graph_limit: int = Field(default=10, ge=0, le=100)
    v2: MemoryV2SearchRequest

    @classmethod
    def unified(cls, user_id: str, query: str, limit: int) -> MemoryReadSpec:
        """Same semantics as previous ``retrieve_unified`` (graph + default V2 search)."""
        cap = max(1, min(int(limit), 100))
        v2_limit = max(1, min(cap * 2, 50))
        q = query or ""
        return cls(
            user_id=user_id,
            query=q,
            include_graph=True,
            graph_limit=cap,
            v2=MemoryV2SearchRequest(
                user_id=user_id,
                query=q,
                memory_types=None,
                scopes=None,
                min_salience=0.0,
                min_confidence=0.0,
                exclude_archived=True,
                exclude_contradicted=False,
                limit=v2_limit,
            ),
        )

    @classmethod
    def v2_http(cls, request: MemoryV2SearchRequest) -> MemoryReadSpec:
        """POST ``/memory/v2/search`` body: V2 slice only (no graph/STM/L1/L2)."""
        return cls(
            user_id=request.user_id,
            query=request.query or "",
            include_graph=False,
            graph_limit=0,
            v2=request,
        )


class MemoryReadOutcome(BaseModel):
    """Canonical read result: optional graph slice + mandatory V2 ranked slice."""

    user_id: str
    query: str
    graph_context: dict[str, Any] | None = None
    v2: MemoryV2SearchResponse


def unified_dict_from_outcome(outcome: MemoryReadOutcome) -> dict[str, Any]:
    """Legacy flat dict shape for tools, ``retrieve_context``, HTTP ``/memory/search``."""
    if outcome.graph_context is not None:
        base = dict(outcome.graph_context)
    else:
        base = {
            "user_id": outcome.user_id,
            "query": outcome.query,
            "stm": [],
            "episodic": [],
            "semantic": [],
            "dense_hits": [],
            "graph_hits": [],
            "total": 0,
        }
    v2 = outcome.v2
    base["memory_v2_items"] = [item.model_dump(mode="json") for item in v2.items]
    base["memory_v2_total"] = v2.total_count
    base["memory_v2_contradictions"] = list(v2.contradictions)
    base["memory_v2_related_procedures"] = [
        p.model_dump(mode="json") for p in v2.related_procedures
    ]
    return base
