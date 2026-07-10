#!/usr/bin/env python3
"""
Memory V2 API Router.

REST endpoints are thin adapters over :class:`aihub.memory_core.MemoryCanonicalCore`.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ConfigDict, model_validator

from aihub.db import now_ts
from aihub.memory_core import get_memory_core
from aihub.memory_psyche_contracts import MemoryScope, MemorySourceKind, MemoryType
from aihub.memory_v2_models import (
    MemoryV2Item,
    MemoryV2Procedure,
    MemoryV2SearchRequest,
    MemoryV2SearchResponse,
    MemoryV2SummaryResponse,
)
from aihub.memory_v2_repository import get_memory_item, update_memory_item

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory/v2", tags=["memory-v2"])


class CreateMemoryRequest(BaseModel):
    """Request to create a Memory V2 item.

    The canonical API still exposes strict domain fields, but accepts legacy/simple
    callers too: ``source`` is treated as ``source_kind`` and missing ``scope``
    defaults to ``user``. This keeps old clients/front panels working without
    creating a second write path.
    """

    model_config = ConfigDict(extra="ignore")

    user_id: str
    memory_type: MemoryType
    scope: MemoryScope = "user"
    title: str
    content: str
    source_kind: MemorySourceKind = "explicit_learning"
    source_ref: str | None = None
    session_id: str | None = None
    importance_score: float = 0.5
    emotional_weight: float = 0.0
    confidence_score: float = 0.7

    @model_validator(mode="before")
    @classmethod
    def _compat_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if not out.get("source_kind") and out.get("source"):
            raw = str(out.get("source") or "").strip().lower()
            aliases = {
                "smoke": "explicit_learning",
                "manual": "explicit_learning",
                "chat": "chat_turn",
                "chat_turn": "chat_turn",
                "web": "explicit_learning",
                "research": "explicit_learning",
                "tool": "agent_cycle",
                "agent": "agent_cycle",
                "system": "reflection",
                "explicit_learning": "explicit_learning",
                "consolidation": "consolidation",
                "reflection": "reflection",
                "contradiction_resolution": "contradiction_resolution",
            }
            out["source_kind"] = aliases.get(raw, "explicit_learning")
            if not out.get("source_ref"):
                out["source_ref"] = str(data.get("source"))
        if not out.get("scope"):
            out["scope"] = "user"
        scope_aliases = {"global": "user", "default": "user", "personal": "user"}
        out["scope"] = scope_aliases.get(str(out.get("scope") or "").strip().lower(), out.get("scope"))
        return out


class CreateMemoryResponse(BaseModel):
    """Response for memory creation."""

    success: bool
    memory_id: str | None = None
    salience_score: float | None = None
    contradiction_detected: bool = False


class ContextPackRequest(BaseModel):
    """Build one budgeted memory pack for prompt/trace/frontend."""

    user_id: str
    query: str = ""
    limit: int = Field(default=24, ge=1, le=80)
    max_chars: int = Field(default=8000, ge=1000, le=50000)
    include_graph: bool = True


class ProcessIndexJobsRequest(BaseModel):
    """Retry pending/stale/failed Memory V2 vector indexing jobs."""

    user_id: str | None = None
    limit: int = Field(default=50, ge=1, le=1000)


class ConsolidationResponse(BaseModel):
    """Response for consolidation operation."""

    consolidated: bool
    consolidation_id: str | None = None
    input_count: int = 0
    output_memory_id: str | None = None
    compression_ratio: float = 0.0
    summary: str = ""
    reason: str | None = None


@router.get("/summary/{user_id}", response_model=MemoryV2SummaryResponse)
async def get_memory_summary(user_id: str) -> MemoryV2SummaryResponse:
    """Get comprehensive memory summary for user."""
    try:
        core = get_memory_core()
        summary = core.v2_get_summary(user_id)
        logger.info(
            "Memory summary requested for user %s: %s items",
            user_id,
            summary.total_items,
        )
        return summary
    except Exception as e:
        logger.error("Failed to get memory summary: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/search", response_model=MemoryV2SearchResponse)
async def search_memory(request: MemoryV2SearchRequest) -> MemoryV2SearchResponse:
    """Search user memories with filters and ranking."""
    try:
        core = get_memory_core()
        response = core.v2_search(request)
        logger.info(
            "Memory search for user %s: %s results",
            request.user_id,
            response.total_count,
        )
        return response
    except Exception as e:
        logger.error("Failed to search memory: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/context-pack")
async def build_context_pack(request: ContextPackRequest) -> dict[str, Any]:
    """Build canonical context pack used by prompt, trace and frontend memory dock."""
    try:
        pack = get_memory_core().build_context_pack(
            request.user_id,
            request.query,
            limit=request.limit,
            max_chars=request.max_chars,
            include_graph=request.include_graph,
        )
        return pack.model_dump(mode="json")
    except Exception as e:
        logger.error("Failed to build memory context pack: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/index-jobs")
async def get_index_job_summary(user_id: str | None = None) -> dict[str, Any]:
    """Return durable Memory V2 vector-indexing outbox status."""
    try:
        return get_memory_core().v2_index_job_summary(user_id=user_id)
    except Exception as e:
        logger.error("Failed to get Memory V2 index job summary: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/index-jobs/process")
async def process_index_jobs(request: ProcessIndexJobsRequest) -> dict[str, Any]:
    """Retry due Memory V2 vector-indexing jobs now."""
    try:
        return get_memory_core().v2_process_index_jobs(
            user_id=request.user_id,
            limit=request.limit,
        )
    except Exception as e:
        logger.error("Failed to process Memory V2 index jobs: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/item", response_model=CreateMemoryResponse)
async def create_memory_item(request: CreateMemoryRequest) -> CreateMemoryResponse:
    """Create new memory item."""
    try:
        core = get_memory_core()
        item = core.v2_create_item(
            user_id=request.user_id,
            memory_type=request.memory_type,
            scope=request.scope,
            title=request.title,
            content=request.content,
            source_kind=request.source_kind,
            source_ref=request.source_ref,
            session_id=request.session_id,
            importance_score=request.importance_score,
            emotional_weight=request.emotional_weight,
            confidence_score=request.confidence_score,
        )

        if not item:
            raise HTTPException(status_code=500, detail="Failed to create memory item")

        return CreateMemoryResponse(
            success=True,
            memory_id=item.id,
            salience_score=item.salience_score,
            contradiction_detected=(item.contradiction_state != "none"),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to create memory item: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/consolidate/{user_id}", response_model=ConsolidationResponse)
async def consolidate_memory(user_id: str) -> ConsolidationResponse:
    """Trigger memory consolidation for user."""
    try:
        result = get_memory_core().v2_consolidate_user_memory(user_id)
        return ConsolidationResponse(**result)
    except Exception as e:
        logger.error("Failed to consolidate memory: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/procedures/{user_id}", response_model=list[MemoryV2Procedure])
async def get_procedures(user_id: str, limit: int = 20) -> list[MemoryV2Procedure]:
    """Get learned procedures for user."""
    try:
        procedures = get_memory_core().v2_list_procedures(user_id, limit=limit)
        logger.info(
            "Procedures requested for user %s: %s found", user_id, len(procedures)
        )
        return procedures
    except Exception as e:
        logger.error("Failed to get procedures: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/contradictions/{user_id}", response_model=list[MemoryV2Item])
async def get_contradictions(user_id: str, limit: int = 50) -> list[MemoryV2Item]:
    """Get memories with contradiction state."""
    try:
        contradicted = get_memory_core().v2_list_contradictions(user_id, limit=limit)
        logger.info(
            "Contradictions requested for user %s: %s found",
            user_id,
            len(contradicted),
        )
        return contradicted
    except Exception as e:
        logger.error("Failed to get contradictions: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/autobio/{user_id}", response_model=dict[str, str])
async def get_autobiographical_summary(user_id: str) -> dict[str, str]:
    """Get autobiographical summary for user."""
    try:
        summary = get_memory_core().v2_autobiographical_summary(
            user_id, max_memories=30
        )
        return {"user_id": user_id, "summary": summary}
    except Exception as e:
        logger.error("Failed to get autobiographical summary: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/autobio/compact/{user_id}", response_model=dict[str, Any])
async def compact_autobiographical_memories(
    user_id: str,
    min_episode_count: int = 3,
) -> dict[str, Any]:
    """Compact repetitive episodic memories into autobiographical summaries."""
    try:
        result = get_memory_core().v2_compact_autobio(
            user_id, min_episode_count=min_episode_count
        )
        logger.info(
            "Compacted %s episodic memories for user %s",
            result["compacted_count"],
            user_id,
        )
        return result
    except Exception as e:
        logger.error("Failed to compact autobiographical memories: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


class ForgettingSweepResponse(BaseModel):
    """Response for forgetting sweep operation."""

    ok: bool
    evaluated_count: int = 0
    suppressed_count: int = 0
    threshold: float = 0.15


@router.post("/forgetting/{user_id}", response_model=ForgettingSweepResponse)
async def run_forgetting_sweep(
    user_id: str, threshold: float = 0.15
) -> ForgettingSweepResponse:
    """Run controlled forgetting sweep for user."""
    try:
        result = get_memory_core().v2_run_forgetting_sweep(user_id, threshold)
        logger.info(
            "Forgetting sweep for user %s: suppressed %s",
            user_id,
            result["suppressed_count"],
        )
        return ForgettingSweepResponse(
            ok=result["ok"],
            evaluated_count=result["evaluated_count"],
            suppressed_count=result["suppressed_count"],
            threshold=result["threshold"],
        )
    except Exception as e:
        logger.error("Failed to run forgetting sweep: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


class RetrievalExplanationResponse(BaseModel):
    """Response for retrieval explanation."""

    user_id: str
    query: str
    top_reason_codes: list[str] = Field(default_factory=list)
    match_count: int = 0
    reinforced_count: int = 0
    suppressed_count: int = 0
    top_items_with_scores: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_strategy: str = ""


@router.get("/retrieval-explain/{user_id}", response_model=RetrievalExplanationResponse)
async def get_retrieval_explanation(
    user_id: str, query: str = "", top_n: int = 10
) -> RetrievalExplanationResponse:
    """Get explanation of memory retrieval reasoning."""
    try:
        result = get_memory_core().v2_get_retrieval_explanation(user_id, query, top_n)
        logger.info(
            "Retrieval explanation for user %s: %s matches",
            user_id,
            result["match_count"],
        )
        return RetrievalExplanationResponse(**result)
    except Exception as e:
        logger.error("Failed to get retrieval explanation: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


class ArchiveMemoryItemBody(BaseModel):
    """Mark a Memory V2 item archived (soft delete for retrieval)."""

    user_id: str
    memory_id: str


@router.post("/item/archive")
async def archive_memory_item(body: ArchiveMemoryItemBody) -> dict[str, Any]:
    try:
        item = get_memory_item(body.memory_id, body.user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="not_found")
        item.is_archived = True
        item.updated_ts = float(now_ts())
        ok = update_memory_item(item)
        return {"ok": bool(ok), "is_archived": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to archive memory item: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


class SuppressMemoryItemBody(BaseModel):
    user_id: str
    memory_id: str
    suppressed: bool = True


class PinMemoryItemBody(BaseModel):
    user_id: str
    memory_id: str
    pinned: bool = True


@router.post("/item/suppress")
async def suppress_memory_item(body: SuppressMemoryItemBody) -> dict[str, Any]:
    try:
        item = get_memory_item(body.memory_id, body.user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="not_found")
        item.is_suppressed = bool(body.suppressed)
        item.updated_ts = float(now_ts())
        ok = update_memory_item(item)
        return {"ok": bool(ok), "is_suppressed": item.is_suppressed}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to suppress memory item: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/item/pin")
async def pin_memory_item(body: PinMemoryItemBody) -> dict[str, Any]:
    try:
        item = get_memory_item(body.memory_id, body.user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="not_found")
        item.is_pinned = bool(body.pinned)
        item.updated_ts = float(now_ts())
        ok = update_memory_item(item)
        return {"ok": bool(ok), "is_pinned": item.is_pinned}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to pin memory item: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
