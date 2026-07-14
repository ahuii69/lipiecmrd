"""Typed models for Adaptive Learning + Self-Model + Long-Horizon Intelligence."""

from __future__ import annotations

from typing import Any, Literal  # noqa: I001 — Literal used by DelayedFeedbackEvent

from pydantic import BaseModel, Field

Scope = Literal[
    "global",
    "user",
    "session",
    "strategy",
    "provider",
    "tool",
    "research",
    "persona",
    "planner",
    "pragmatics",
]
Polarity = Literal["positive", "negative", "mixed", "neutral"]
EvidenceKind = Literal["observed", "strongly_inferred", "weakly_inferred", "inferred", "uncertain", "unknown"]
TaskStatus = Literal[
    "proposed",
    "active",
    "blocked",
    "completed",
    "abandoned",
    "paused",
    "superseded",
]


class TurnOutcomeEvaluation(BaseModel):
    turn_id: str
    user_id: str
    session_id: str = ""
    request_id: str = ""
    correlation_id: str = ""
    runtime_mode: str = "live"
    primary_intent: str = "unknown"
    intent_confidence: float = 0.5
    ambiguity_score: float = 0.0
    conversation_state: str = ""
    selected_strategy: str = "contextual"
    strategy_confidence: float = 0.5
    planner_used: bool = False
    reasoning_used: bool = False
    web_used: bool = False
    tools_used: bool = False
    provider_used: str = ""
    provider_fallback_used: bool = False
    critic_score: float | None = None
    critic_revision_happened: bool = False
    response_critic_score: float | None = None
    final_response_quality: float = 0.5
    user_satisfaction_signal: float = 0.0
    immediate_user_signal: float = 0.0
    delayed_user_signal: float = 0.0
    correction_signal: float = 0.0
    rejection_signal: float = 0.0
    continuation_signal: float = 0.0
    acceptance_signal: float = 0.0
    correction_detected: bool = False
    rejection_detected: bool = False
    acceptance_detected: bool = False
    continuation_detected: bool = False
    task_completion_signal: float = 0.0
    factual_grounding_score: float = 0.5
    style_match_score: float = 0.5
    intent_match_score: float = 0.5
    verbosity_match_score: float = 0.5
    memory_usefulness_score: float = 0.5
    psyche_alignment_score: float = 0.5
    planner_quality_score: float = 0.5
    tool_success_score: float = 0.5
    tool_execution_score: float = 0.5
    research_quality_score: float = 0.5
    latency_score: float = 0.5
    cost_score: float = 0.5
    overall_reward: float = 0.0
    confidence: float = 0.4
    reason_codes: list[str] = Field(default_factory=list)
    degraded: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    delayed_feedback_applied: bool = False
    message_preview: str = ""
    response_preview: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CausalAttribution(BaseModel):
    attribution_id: str = ""
    turn_id: str = ""
    factor: str
    contribution_score: float = 0.0
    confidence: float = 0.3
    evidence: str = ""
    evidence_kind: EvidenceKind = "weakly_inferred"
    positive_or_negative: Polarity = "neutral"
    polarity: Polarity = "neutral"
    attribution_type: EvidenceKind = "weakly_inferred"
    corrective_action: str = ""
    scope: Scope = "user"
    expiry: float | None = None
    expires_at: float | None = None


class LearnedLesson(BaseModel):
    lesson_id: str
    user_id: str = ""
    session_id: str = ""
    task_id: str = ""
    scope: Scope = "user"
    trigger_turn_id: str = ""
    source_turn_ids: list[str] = Field(default_factory=list)
    category: str = "general"
    statement: str
    machine_action: str = ""
    machine_action_payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.4
    evidence_count: int = 1
    positive_evidence_count: int = 0
    negative_evidence_count: int = 0
    positive_examples: list[str] = Field(default_factory=list)
    negative_examples: list[str] = Field(default_factory=list)
    applicable_intents: list[str] = Field(default_factory=list)
    applicable_strategies: list[str] = Field(default_factory=list)
    applicable_tools: list[str] = Field(default_factory=list)
    applicable_providers: list[str] = Field(default_factory=list)
    applicable_domains: list[str] = Field(default_factory=list)
    applicable_conversation_states: list[str] = Field(default_factory=list)
    reinforcement_count: int = 1
    contradiction_count: int = 0
    success_rate: float = 0.5
    created_at: float = 0.0
    updated_at: float = 0.0
    last_used_at: float | None = None
    expires_at: float | None = None
    suppressed: bool = False
    archived: bool = False
    version: int = 1
    content_hash: str = ""


class TraitObservation(BaseModel):
    value: Any = None
    confidence: float = 0.25
    evidence_count: int = 0
    positive_evidence: int = 0
    negative_evidence: int = 0
    last_updated: float = 0.0
    decay: float = 0.02
    source_turn_ids: list[str] = Field(default_factory=list)


class UserModelV2(BaseModel):
    user_id: str
    preferred_tone: TraitObservation = Field(default_factory=TraitObservation)
    preferred_verbosity: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value="medium")
    )
    preferred_structure: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value="free")
    )
    preferred_technical_depth: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value=0.55)
    )
    humour_style: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value=0.45)
    )
    sarcasm_tolerance: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value=0.5)
    )
    profanity_tolerance: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value=0.5)
    )
    directness: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value=0.55)
    )
    correction_preference: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value="direct")
    )
    planning_preference: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value="light")
    )
    research_preference: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value="when_needed")
    )
    examples_preference: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value=0.4)
    )
    format_preference: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value="natural")
    )
    uncertainty_preference: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value="honest")
    )
    ask_before_action_preference: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value=True)
    )
    preferred_response_pace: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value="normal")
    )
    preferred_level_of_explanation: TraitObservation = Field(
        default_factory=lambda: TraitObservation(value=0.55)
    )
    version: int = 1
    updated_at: float = 0.0


class RuntimeSelfModel(BaseModel):
    deployment_id: str = "default"
    version: int = 1
    strong_domains: list[str] = Field(default_factory=list)
    weak_domains: list[str] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    strategy_success: dict[str, float] = Field(default_factory=dict)
    provider_success: dict[str, float] = Field(default_factory=dict)
    research_success: float = 0.5
    planner_success: float = 0.5
    typical_errors: list[str] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)
    confidence_calibration: dict[str, float] = Field(default_factory=dict)
    known_outages: list[str] = Field(default_factory=list)
    preferred_paths: list[str] = Field(default_factory=list)
    hallucination_risk_by_domain: dict[str, float] = Field(default_factory=dict)
    cost_latency_by_path: dict[str, dict[str, float]] = Field(default_factory=dict)
    sample_counts: dict[str, int] = Field(default_factory=dict)
    updated_at: float = 0.0


class FailurePattern(BaseModel):
    failure_id: str
    user_id: str = ""
    category: str
    trigger: str
    context: str = ""
    root_cause: str = ""
    evidence: str = ""
    affected_module: str = ""
    corrective_action: str = ""
    recurrence_count: int = 1
    last_seen: float = 0.0
    resolved: bool = False
    resolution_turn_id: str = ""
    confidence: float = 0.5
    content_hash: str = ""


class SuccessPattern(BaseModel):
    success_id: str
    user_id: str = ""
    category: str
    pattern: str
    context: str = ""
    evidence: str = ""
    reinforcement_count: int = 1
    success_rate: float = 0.7
    last_seen: float = 0.0
    confidence: float = 0.5
    content_hash: str = ""


class LongHorizonTask(BaseModel):
    task_id: str
    user_id: str
    session_id: str = ""
    title: str
    objective: str = ""
    constraints: list[str] = Field(default_factory=list)
    accepted_decisions: list[str] = Field(default_factory=list)
    rejected_decisions: list[str] = Field(default_factory=list)
    current_stage: str = "init"
    completed_steps: list[str] = Field(default_factory=list)
    pending_steps: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    last_action: str = ""
    next_best_action: str = ""
    status: TaskStatus = "active"
    confidence: float = 0.5
    created_at: float = 0.0
    updated_at: float = 0.0


class ConfidenceCalibration(BaseModel):
    raw_confidence: float
    calibrated_confidence: float
    calibration_delta: float = 0.0
    calibration_source: str = "rule"
    calibration_sample_count: int = 0


class DelayedFeedbackEvent(BaseModel):
    feedback_id: str = ""
    feedback_turn_id: str
    target_turn_id: str
    user_id: str
    session_id: str = ""
    feedback_type: str = "generic"
    polarity: Polarity = "neutral"
    kind: str = "generic"
    confidence: float = 0.5
    evidence: str = ""
    affected_dimensions: list[str] = Field(default_factory=list)
    explicit_or_inferred: Literal["explicit", "inferred"] = "inferred"
    reason_codes: list[str] = Field(default_factory=list)
    text_preview: str = ""
    created_at: float = 0.0


class LearningTurnResult(BaseModel):
    outcome: TurnOutcomeEvaluation | None = None
    attributions: list[CausalAttribution] = Field(default_factory=list)
    lessons_persisted: int = 0
    lessons_rejected: int = 0
    lesson_candidates: int = 0
    self_model_updated: bool = False
    user_model_updated: bool = False
    failure_recorded: bool = False
    success_recorded: bool = False
    goal_progress_updated: bool = False
    long_horizon_task_id: str = ""
    delayed_feedback: DelayedFeedbackEvent | None = None
    calibration: ConfidenceCalibration | None = None
    degraded: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    timing_ms: float = 0.0
