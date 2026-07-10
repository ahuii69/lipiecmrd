#!/usr/bin/env python3
"""
Memory and Psyche V2 shared contracts and type definitions.

These contracts define the interface between services, API layer, and runtime bridges.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

MemoryStabilityTier = Literal["transient", "developing", "stable"]

SelfConsistencyDecision = Literal["allow", "dampen", "suppress", "promote_later"]

# ─── Memory V2 Type Definitions ─────────────────────────────────────────────

MemoryType = Literal[
    "preference",
    "fact",
    "procedural",
    "autobiographical",
    "relationship",
    "lesson",
]

MemoryScope = Literal[
    "user",
    "session",
    "domain",
    "workflow",
    "interaction",
]

MemorySourceKind = Literal[
    "chat_turn",
    "agent_cycle",
    "explicit_learning",
    "consolidation",
    "reflection",
    "contradiction_resolution",
]

ContradictionState = Literal[
    "none",
    "suspected",
    "superseded",
    "conflicted",
]

DecayBucket = Literal[
    "active",
    "warm",
    "cooling",
    "archive_candidate",
]

MemoryLinkType = Literal[
    "supersedes",
    "contradicts",
    "supports",
    "refines",
    "relates_to",
    "caused_by",
]

ConsolidationType = Literal[
    "episodic_rollup",
    "procedural_extraction",
    "autobiographical_summary",
    "relationship_synthesis",
]

# ─── Psyche V2 Type Definitions ─────────────────────────────────────────────

PsycheEventType = Literal[
    "interaction_start",
    "interaction_complete",
    "tool_success",
    "tool_failure",
    "web_research_triggered",
    "planning_executed",
    "user_feedback_positive",
    "user_feedback_negative",
    "contradiction_detected",
    "confidence_shift",
    "mode_transition",
]

PsycheMode = Literal[
    "neutral",
    "focused",
    "exploratory",
    "cautious",
    "assertive",
    "collaborative",
    "analytical",
]

BehaviorRuleTriggerType = Literal[
    "time_of_day",
    "session_length",
    "stress_threshold",
    "repeated_pattern",
    "user_signal",
    "contradiction_count",
]

# ─── Dataclass Contracts ────────────────────────────────────────────────────


@dataclass
class MemoryV2Contradiction:
    """Represents a detected contradiction between memory items."""

    from_memory_id: str
    to_memory_id: str
    contradiction_type: str
    confidence: float
    reason: str
    created_ts: float


@dataclass
class MemoryV2ConsolidationResult:
    """Result of memory consolidation operation."""

    consolidation_id: str
    consolidation_type: ConsolidationType
    input_memory_ids: list[str]
    output_memory_id: str
    compression_ratio: float
    summary: str
    created_ts: float


@dataclass
class MemoryV2ScoringWeights:
    """Configurable weights for salience scoring."""

    importance: float = 0.24
    recurrence: float = 0.18
    emotional: float = 0.16
    identity_relevance: float = 0.18
    confidence: float = 0.12
    freshness: float = 0.12

    def __post_init__(self):
        total = (
            self.importance
            + self.recurrence
            + self.emotional
            + self.identity_relevance
            + self.confidence
            + self.freshness
        )
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Salience weights must sum to 1.0, got {total}")


@dataclass
class PsycheV2PolicyView:
    """Derived behavior policy from psyche state and profile."""

    user_id: str
    directness: float
    verbosity: float
    caution: float
    initiative: float
    tool_bias: float
    web_bias: float
    confidence_style: float
    response_compression: float
    escalation_bias: float
    reassurance_bias: float
    autonomy_bias: float
    structuredness_bias: float
    relation_trust: float
    relation_familiarity: float
    relation_friction: float
    relation_warmth: float
    current_mode: PsycheMode
    stress_load: float
    derived_at: float


@dataclass
class IdentityBridgeSnapshot:
    """Unified identity view for runtime consumption."""

    user_id: str
    top_preferences: list[dict[str, Any]]
    top_procedures: list[dict[str, Any]]
    active_contradictions_count: int
    active_habits: list[dict[str, Any]]
    relation_trust: float
    relation_familiarity: float
    relation_sync: float
    relation_friction: float
    relation_warmth: float
    behavior_mode: PsycheMode
    stress_load: float
    pressure: float
    autobio_summary: str
    memory_v2_total: int
    psyche_v2_certainty: float
    snapshot_ts: float
    relation_interaction_quality_ema: float = 0.5
    relation_drift_score: float = 0.0
    psyche_drift_score: float = 0.0


@dataclass
class MemoryV2RuntimeContext:
    """Enriched memory context for active runtime injection."""

    loaded: bool
    top_facts: list[dict[str, Any]]
    top_preferences: list[dict[str, Any]]
    top_procedures: list[dict[str, Any]]
    contradiction_alerts: list[str]
    autobiographical_summary: str
    reinforced_patterns: list[str]
    retrieval_reason_codes: list[str]
    confidence_modifier: float
    total_items: int
    stability_tier_counts: dict[str, int] = field(default_factory=dict)
    transient_contradiction_hints: list[str] = field(default_factory=list)
    confidence_modifier_raw: float = 0.0
    self_consistency_notes: list[str] = field(default_factory=list)
    actionable_contradictions_count: int = 0
    transient_contradiction_count: int = 0
    stable_memory_operational: list[dict[str, Any]] = field(default_factory=list)
    transient_memory_operational: list[dict[str, Any]] = field(default_factory=list)
    suppressed_memory_operational: list[dict[str, Any]] = field(default_factory=list)
    promotion_audit: list[dict[str, Any]] = field(default_factory=list)
    consistency_decision: SelfConsistencyDecision = "allow"


@dataclass
class PsycheV2BehaviorContext:
    """Behavior policy context for active runtime injection."""

    loaded: bool
    mode: PsycheMode
    pressure: float
    trust: float
    friction: float
    warmth: float
    directness_bias: float
    reassurance_bias: float
    autonomy_bias: float
    structuredness_bias: float
    tool_bias: float
    web_bias: float
    caution_bias: float
    verbosity_bias: float
    consistency_decision: SelfConsistencyDecision = "allow"
    consistency_reasons: list[str] = field(default_factory=list)
    relation_quality_ema: float = 0.5
    habit_stability_score: float = 0.5
    psyche_drift_score: float = 0.0
    relation_drift_score: float = 0.0
    confidence_style_effective: float = 0.5

