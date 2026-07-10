#!/usr/bin/env python3
"""
Memory V2 models and domain entities.

Provides rich, typed representations for all memory V2 constructs.
"""

from pydantic import BaseModel, Field
from typing import Any

from aihub.memory_psyche_contracts import (
    MemoryType,
    MemoryScope,
    MemorySourceKind,
    ContradictionState,
    DecayBucket,
    MemoryLinkType,
    ConsolidationType,
    MemoryStabilityTier,
)


class MemoryV2Item(BaseModel):
    """Rich memory item with multi-dimensional scoring and lifecycle management."""

    id: str
    user_id: str
    session_id: str | None = None
    memory_type: MemoryType
    scope: MemoryScope
    title: str
    content: str
    summary: str
    source_kind: MemorySourceKind
    source_ref: str | None = None
    importance_score: float = 0.0
    salience_score: float = 0.0
    emotional_weight: float = 0.0
    recurrence_score: float = 0.0
    confidence_score: float = 0.0
    freshness_score: float = 0.0
    identity_relevance_score: float = 0.0
    relation_relevance_score: float = 0.0
    outcome_reinforcement_score: float = 0.0
    source_reliability_score: float = 0.7
    retrieval_priority_score: float = 0.0
    contradiction_state: ContradictionState = "none"
    valid_from_ts: float | None = None
    valid_to_ts: float | None = None
    last_accessed_ts: float | None = None
    last_reinforced_ts: float | None = None
    reinforcement_count: int = 0
    success_reinforcements: int = 0
    failure_reinforcements: int = 0
    decay_bucket: DecayBucket = "active"
    stability_tier: MemoryStabilityTier = "transient"
    is_pinned: bool = False
    is_archived: bool = False
    is_suppressed: bool = False
    embedding_vector_ref: str | None = None
    created_ts: float
    updated_ts: float


class MemoryV2Link(BaseModel):
    """Link between memory items representing relationships."""

    id: str
    user_id: str
    from_memory_id: str
    to_memory_id: str
    link_type: MemoryLinkType
    weight: float = 0.0
    created_ts: float


class MemoryV2Consolidation(BaseModel):
    """Record of memory consolidation operation."""

    id: str
    user_id: str
    consolidation_type: ConsolidationType
    input_memory_ids: list[str]
    output_memory_id: str
    compression_ratio: float = 0.0
    created_ts: float


class MemoryV2Procedure(BaseModel):
    """Learned procedural pattern from experience."""

    id: str
    user_id: str
    name: str
    trigger_pattern: str
    recommended_strategy: str
    recommended_tools: list[str] = Field(default_factory=list)
    avoid_patterns: list[str] = Field(default_factory=list)
    success_rate: float = 0.0
    failure_rate: float = 0.0
    confidence_score: float = 0.0
    evidence_count: int = 0
    last_validated_ts: float | None = None
    created_ts: float
    updated_ts: float


class MemoryV2Lesson(BaseModel):
    """High-level lesson extracted from patterns."""

    id: str
    user_id: str
    lesson_scope: str
    lesson_text: str
    applies_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    strength_score: float = 0.0
    evidence_count: int = 0
    created_ts: float
    updated_ts: float


class MemoryV2SearchRequest(BaseModel):
    """Request model for memory search."""

    user_id: str
    query: str = ""
    memory_types: list[MemoryType] | None = None
    scopes: list[MemoryScope] | None = None
    min_salience: float = 0.0
    min_confidence: float = 0.0
    exclude_archived: bool = True
    exclude_contradicted: bool = False
    limit: int = 20


class MemoryV2SearchResponse(BaseModel):
    """Response model for memory search."""

    items: list[MemoryV2Item]
    contradictions: list[dict[str, Any]] = Field(default_factory=list)
    related_procedures: list[MemoryV2Procedure] = Field(default_factory=list)
    total_count: int = 0


class MemoryV2SummaryResponse(BaseModel):
    """Aggregated summary of user memory."""

    user_id: str
    total_items: int = 0
    active_items: int = 0
    suppressed_items: int = 0
    reinforced_items: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    by_contradiction_state: dict[str, int] = Field(default_factory=dict)
    by_decay_bucket: dict[str, int] = Field(default_factory=dict)
    top_salient: list[MemoryV2Item] = Field(default_factory=list)
    top_reinforced: list[MemoryV2Item] = Field(default_factory=list)
    top_procedures: list[MemoryV2Procedure] = Field(default_factory=list)
    top_lessons: list[MemoryV2Lesson] = Field(default_factory=list)
    autobiographical_summary: str = ""
    relation_summary: str = ""
    recent_writebacks: list[MemoryV2Item] = Field(default_factory=list)


class MemoryV2RetrievalExplanation(BaseModel):
    """Explanation of memory retrieval reasoning."""

    user_id: str
    query: str
    top_reason_codes: list[str] = Field(default_factory=list)
    match_count: int = 0
    reinforced_count: int = 0
    suppressed_count: int = 0
    top_items_with_scores: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_strategy: str = ""
