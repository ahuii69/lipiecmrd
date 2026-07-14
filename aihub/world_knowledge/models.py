"""Typed models for Evidence Memory, Knowledge Graph, Execution Graph."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ClaimType = Literal[
    "fact",
    "user_preference",
    "user_statement",
    "opinion",
    "hypothesis",
    "assumption",
    "inference",
    "prediction",
    "instruction",
    "decision",
    "rejection",
    "constraint",
    "task_state",
    "system_capability",
]
ClaimStatus = Literal[
    "proposed",
    "supported",
    "verified",
    "disputed",
    "superseded",
    "expired",
    "rejected",
    "retracted",
]
SourceType = Literal[
    "user_statement",
    "conversation",
    "memory",
    "web_page",
    "official_document",
    "research_result",
    "tool_result",
    "database",
    "file",
    "system_observation",
    "model_inference",
]
ConflictType = Literal[
    "direct_negation",
    "changed_over_time",
    "scope_mismatch",
    "unit_mismatch",
    "entity_mismatch",
    "stale_information",
    "source_disagreement",
    "user_correction",
    "superseded_decision",
]
ExecutionStatus = Literal[
    "pending",
    "running",
    "blocked",
    "completed",
    "failed",
    "cancelled",
    "waiting_user",
]
NodeType = Literal[
    "reason",
    "research",
    "tool",
    "verify",
    "wait",
    "branch",
    "summarize",
    "user_input_required",
    "rollback",
]


class EvidenceRecord(BaseModel):
    evidence_id: str
    turn_id: str = ""
    user_id: str = ""
    session_id: str = ""
    task_id: str = ""
    source_type: SourceType = "user_statement"
    source_uri: str = ""
    source_title: str = ""
    source_domain: str = ""
    source_author: str = ""
    source_published_at: float | None = None
    retrieved_at: float = 0.0
    content_hash: str = ""
    excerpt: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    reliability_score: float = 0.5
    freshness_score: float = 0.5
    relevance_score: float = 0.5
    corroboration_score: float = 0.0
    overall_score: float = 0.5
    language: str = "pl"
    is_primary_source: bool = False
    is_user_provided: bool = False
    is_tool_result: bool = False
    is_inference: bool = False
    expires_at: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = 0.0


class KnowledgeEntity(BaseModel):
    entity_id: str
    canonical_name: str
    entity_type: str = "concept"
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    scope: str = "user"
    user_id: str = ""
    confidence: float = 0.5
    created_at: float = 0.0
    updated_at: float = 0.0
    merged_into_entity_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeClaim(BaseModel):
    claim_id: str
    subject_entity_id: str = ""
    predicate: str = ""
    object_entity_id: str = ""
    literal_value: str = ""
    value_type: str = "text"
    claim_type: ClaimType = "fact"
    scope: str = "user"
    user_id: str = ""
    session_id: str = ""
    task_id: str = ""
    confidence: float = 0.4
    status: ClaimStatus = "proposed"
    valid_from: float | None = None
    valid_until: float | None = None
    observed_at: float = 0.0
    last_verified_at: float | None = None
    verification_due_at: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    supporting_evidence_count: int = 0
    contradicting_evidence_count: int = 0
    source_diversity_count: int = 0
    content_hash: str = ""
    statement: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    version: int = 1


class KnowledgeRelation(BaseModel):
    relation_id: str
    subject_entity_id: str
    predicate: str
    object_entity_id: str
    claim_id: str = ""
    confidence: float = 0.5
    valid_from: float | None = None
    valid_until: float | None = None
    status: str = "active"
    created_at: float = 0.0
    updated_at: float = 0.0


class ClaimConflict(BaseModel):
    conflict_id: str
    claim_a_id: str
    claim_b_id: str
    conflict_type: ConflictType = "source_disagreement"
    severity: float = 0.5
    confidence: float = 0.5
    resolution_status: str = "open"
    preferred_claim_id: str = ""
    resolution_reason: str = ""
    created_at: float = 0.0
    resolved_at: float | None = None


class ExecutionNode(BaseModel):
    node_id: str
    node_type: NodeType = "tool"
    action: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    expected_effects: list[str] = Field(default_factory=list)
    validation_rules: list[str] = Field(default_factory=list)
    retry_policy: str = "transient"
    rollback_action: str = ""
    idempotency_key: str = ""
    status: ExecutionStatus = "pending"
    attempt_count: int = 0
    started_at: float | None = None
    completed_at: float | None = None
    result_summary: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    error: str = ""


class ExecutionGraph(BaseModel):
    execution_id: str
    task_id: str = ""
    goal_id: str = ""
    user_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    status: ExecutionStatus = "pending"
    nodes: list[ExecutionNode] = Field(default_factory=list)
    edges: list[dict[str, str]] = Field(default_factory=list)
    current_node: str = ""
    completed_nodes: list[str] = Field(default_factory=list)
    failed_nodes: list[str] = Field(default_factory=list)
    blocked_nodes: list[str] = Field(default_factory=list)
    replan_count: int = 0
    lease_owner: str = ""
    lease_until: float | None = None
    created_at: float = 0.0
    updated_at: float = 0.0


class KnowledgeContextPack(BaseModel):
    entities: list[KnowledgeEntity] = Field(default_factory=list)
    claims: list[KnowledgeClaim] = Field(default_factory=list)
    relations: list[KnowledgeRelation] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    conflicts: list[ClaimConflict] = Field(default_factory=list)
    disputed_claims: list[str] = Field(default_factory=list)
    stale_claims: list[str] = Field(default_factory=list)
    evidence_quality: float = 0.5
    source_diversity: float = 0.0
    evidence_gaps: list[str] = Field(default_factory=list)
    verification_required: bool = False
    graph_path_hints: list[str] = Field(default_factory=list)
    hops: int = 0
    reason_codes: list[str] = Field(default_factory=list)
    degraded: bool = False


class KnowledgeTurnResult(BaseModel):
    context: KnowledgeContextPack | None = None
    entities_upserted: int = 0
    claims_upserted: int = 0
    evidence_upserted: int = 0
    relations_upserted: int = 0
    conflicts_found: int = 0
    claims_superseded: int = 0
    execution_id: str = ""
    writeback_succeeded: bool = False
    degraded: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    timing_ms: float = 0.0
