#!/usr/bin/env python3
"""Canonical memory operations for runtime, HTTP, tools, and cockpit adapters.

Every memory read/write path that participates in production behavior should go
through :class:`MemoryCanonicalCore` (via :func:`get_memory_core`). Graph
(STM/L1/L2) persistence and ranking live in :mod:`aihub.memory_engine`;
Memory V2 persistence and ranking live in :class:`aihub.memory_v2_service.MemoryV2Service`,
exposed as :attr:`MemoryCanonicalCore.v2_service` (one instance per process).

**Reads:** all ranked V2 retrieval for HTTP/tools/runtime flows through
:meth:`read_memory` + :class:`aihub.memory_read_contracts.MemoryReadSpec`;
:meth:`retrieve_unified` and :meth:`v2_search` are adapters on top of it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from aihub.memory_psyche_contracts import (
    MemoryScope,
    MemorySourceKind,
    MemoryType,
)
from aihub.memory_read_contracts import (
    MemoryReadOutcome,
    MemoryReadSpec,
    unified_dict_from_outcome,
)
from aihub.memory_v2_models import MemoryV2SearchRequest, MemoryV2SearchResponse
from aihub.memory_v2_service import MemoryV2Service

logger = logging.getLogger(__name__)

_core_singleton: Optional["MemoryCanonicalCore"] = None


def get_memory_core() -> "MemoryCanonicalCore":
    global _core_singleton
    if _core_singleton is None:
        _core_singleton = MemoryCanonicalCore()
    return _core_singleton


class MemoryCanonicalCore:
    """Single entry point for memory operations across graph (v1) and Memory V2."""

    __slots__ = ("_v2",)

    def __init__(self) -> None:
        self._v2 = MemoryV2Service()

    @property
    def v2_service(self) -> MemoryV2Service:
        """The process-wide Memory V2 orchestration instance (shared with all adapters)."""
        return self._v2

    def ingest_turn(
        self,
        user_id: str,
        user_msg: str,
        assistant_msg: str,
        intent: str,
        meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Persist one graph-memory turn.

        Psyche mutation is intentionally not owned here.  A completed chat turn
        fans out Memory and Psyche as separate idempotent durable handlers.
        """
        from aihub.memory_engine import process_turn
        return process_turn(user_id, user_msg, assistant_msg, intent, meta)

    def ingest_fact(
        self,
        user_id: str,
        fact: str,
        tags: List[str],
        meta: Dict[str, Any],
    ) -> str:
        """Tool ``memory.add_fact`` and planner learn tasks — graph L2."""
        from aihub.memory_engine import add_fact

        return add_fact(user_id, fact, tags=list(tags or []), meta=dict(meta or {}))

    def ingest_episode(
        self,
        user_id: str,
        summary: str,
        meta: Dict[str, Any],
    ) -> str:
        """Tool ``memory.add_episode`` — graph L1."""
        from aihub.memory_engine import add_episode

        return add_episode(user_id, summary, dict(meta or {}))

    def ingest_stm_message(
        self,
        user_id: str,
        role: str,
        content: str,
        meta: Dict[str, Any],
    ) -> str:
        """Raw STM append (``POST /turn`` legacy) — same persistence as ``add_stm``."""
        from aihub.memory_engine import add_stm

        return add_stm(user_id, role, content, dict(meta or {}))

    def record_agent_outcome(self, **kwargs: Any) -> Dict[str, Any]:
        """Agent cycle write-back to Memory V2 (executive controller)."""
        return self._v2.record_agent_outcome(**kwargs)

    def record_chat_outcome(self, **kwargs: Any) -> Dict[str, Any]:
        """Chat turn write-back to Memory V2 (:mod:`aihub.chat_runtime`)."""
        return self._v2.record_chat_outcome(**kwargs)

    def read_memory(self, spec: MemoryReadSpec) -> MemoryReadOutcome:
        """Single orchestration path for graph + Memory V2 ranked retrieval."""
        from aihub.memory_engine import retrieve_context_v1

        graph: Dict[str, Any] | None = None
        if spec.include_graph:
            glim = max(1, min(int(spec.graph_limit), 100))
            graph = retrieve_context_v1(spec.user_id, spec.query, glim)

        v2_req = spec.v2.model_copy(update={"user_id": spec.user_id})
        try:
            v2_resp = self._v2.search(v2_req)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Memory read V2 search failed: %s", exc)
            v2_resp = MemoryV2SearchResponse(items=[], total_count=0)

        return MemoryReadOutcome(
            user_id=spec.user_id,
            query=spec.query,
            graph_context=graph,
            v2=v2_resp,
        )

    def retrieve_unified(self, user_id: str, query: str, limit: int) -> Dict[str, Any]:
        """Merge L1/L2/STM + V2; flat dict adapter over :meth:`read_memory`."""
        outcome = self.read_memory(MemoryReadSpec.unified(user_id, query, limit))
        return unified_dict_from_outcome(outcome)

    def v2_search(self, request: MemoryV2SearchRequest) -> MemoryV2SearchResponse:
        """POST ``/memory/v2/search``; adapter over :meth:`read_memory` (V2-only spec)."""
        return self.read_memory(MemoryReadSpec.v2_http(request)).v2

    def v2_get_summary(self, user_id: str):
        return self._v2.get_summary(user_id)

    def v2_create_item(
        self,
        *,
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
    ):
        return self._v2.create_memory_item(
            user_id=user_id,
            memory_type=memory_type,
            scope=scope,
            title=title,
            content=content,
            source_kind=source_kind,
            source_ref=source_ref,
            session_id=session_id,
            importance_score=importance_score,
            emotional_weight=emotional_weight,
            confidence_score=confidence_score,
        )

    def v2_consolidate_user_memory(self, user_id: str) -> Dict[str, Any]:
        return self._v2.consolidate_user_memory(user_id)

    def v2_run_forgetting_sweep(
        self, user_id: str, suppress_threshold: float
    ) -> Dict[str, Any]:
        return self._v2.run_forgetting_sweep(
            user_id, suppress_threshold=suppress_threshold
        )

    def v2_get_retrieval_explanation(
        self, user_id: str, query: str, top_n: int
    ) -> Dict[str, Any]:
        return self._v2.get_retrieval_explanation(user_id, query, top_n)

    def v2_list_procedures(self, user_id: str, limit: int):
        return self._v2.list_procedures(user_id, limit=limit)

    def v2_extract_procedures(self, user_id: str):
        """Run procedural learning from experiences and persist new procedures."""
        return self._v2.extract_procedures(user_id)

    def v2_list_contradictions(self, user_id: str, limit: int):
        return self._v2.list_contradicted_memories(user_id, limit=limit)

    def v2_autobiographical_summary(self, user_id: str, max_memories: int) -> str:
        return self._v2.autobiographical_plain_summary(user_id, max_memories)

    def v2_compact_autobio(
        self, user_id: str, min_episode_count: int
    ) -> Dict[str, Any]:
        return self._v2.compact_autobio_episodes(
            user_id, min_episode_count=min_episode_count
        )


    def build_context_pack(
        self,
        user_id: str,
        query: str,
        *,
        limit: int = 24,
        max_chars: int = 8000,
        include_graph: bool = True,
        correction_hints: str = "",
    ):
        """Build the canonical MemoryContextPack used by prompt/trace/frontend."""
        from aihub.memory_context_pack import build_memory_context_pack

        spec = MemoryReadSpec.unified(user_id, query, limit)
        if not include_graph:
            spec = spec.model_copy(update={"include_graph": False, "graph_limit": 0})
        outcome = self.read_memory(spec)
        return build_memory_context_pack(
            outcome,
            max_chars=max_chars,
            max_items=limit,
            correction_hints=correction_hints,
        )

    def v2_process_index_jobs(self, user_id: str | None = None, limit: int = 50) -> Dict[str, Any]:
        """Retry durable Memory V2 vector index jobs."""
        return self._v2.process_index_jobs(user_id=user_id, limit=limit)

    def v2_index_job_summary(self, user_id: str | None = None) -> Dict[str, Any]:
        """Return durable Memory V2 vector index job summary."""
        return self._v2.get_index_job_summary(user_id=user_id)

    def build_health_report(self, user_id: str) -> Dict[str, Any]:
        """``GET /memory/health`` — warstwy użytkownika + stan wektorów i embeddera."""
        from aihub import embedding_engine as ee_mod
        from aihub import vector_engine as ve_mod
        from aihub.db import fetch_one

        l1 = fetch_one(
            "SELECT COUNT(*) AS n FROM memory_nodes WHERE user_id=? AND layer='L1' AND deleted=0",
            (user_id,),
        )
        l2 = fetch_one(
            "SELECT COUNT(*) AS n FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
            (user_id,),
        )
        v2 = fetch_one(
            "SELECT COUNT(*) AS n FROM memory_v2_items WHERE user_id=? AND is_archived=0",
            (user_id,),
        )
        def _n(row: Any) -> int:
            if row is None:
                return 0
            return int(row["n"])

        layers = {
            "L1_episodes": _n(l1),
            "L2_facts": _n(l2),
            "memory_v2_items": _n(v2),
        }
        return {
            "user_id": user_id,
            "layers": layers,
            "vector": ve_mod.health(),
            "embedding": ee_mod.healthcheck(),
            "memory_v2_index_jobs": self.v2_index_job_summary(user_id),
        }

    def build_cockpit_memory_v2_panel(self, user_id: str) -> Dict[str, Any]:
        """Payload for ``GET /cockpit/memory-v2/{user_id}``."""
        return self._v2.build_cockpit_panel_payload(user_id)

    def build_cockpit_retrieval_payload(
        self, user_id: str, query: str, top_n: int
    ) -> Dict[str, Any]:
        """Payload for ``GET /cockpit/memory-v2/retrieval/{user_id}``."""
        result = self.v2_get_retrieval_explanation(user_id, query, top_n)
        return {
            "user_id": result["user_id"],
            "query": result["query"],
            "top_reason_codes": result["top_reason_codes"],
            "match_count": result["match_count"],
            "reinforced_count": result["reinforced_count"],
            "suppressed_count": result["suppressed_count"],
            "retrieval_strategy": result["retrieval_strategy"],
            "top_items": result["top_items_with_scores"][:5],
        }
