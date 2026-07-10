#!/usr/bin/env python3
"""
Psyche V2 models and domain entities.

Provides rich, typed representations for all psyche V2 constructs.
"""

from pydantic import BaseModel, Field
from typing import Any

from aihub.memory_psyche_contracts import (
    PsycheEventType,
    PsycheMode,
    BehaviorRuleTriggerType,
)


class PsycheV2Profile(BaseModel):
    """Long-term personality traits and relational stance."""

    user_id: str
    core_directness: float = 0.5
    core_patience: float = 0.5
    core_curiosity: float = 0.5
    core_caution: float = 0.5
    core_assertiveness: float = 0.5
    core_formality: float = 0.5
    core_warmth: float = 0.5
    core_initiative: float = 0.5
    core_skepticism: float = 0.5
    core_creativity: float = 0.5
    relation_trust: float = 0.5
    relation_familiarity: float = 0.5
    relation_sync: float = 0.5
    relation_friction: float = 0.0
    relation_warmth: float = 0.5
    relation_directness_tolerance: float = 0.5
    relation_collaboration_confidence: float = 0.5
    relation_interaction_quality_ema: float = 0.5
    stress_load: float = 0.0
    confidence_baseline: float = 0.5
    adaptation_velocity: float = 0.2
    last_reflection_ts: float | None = None
    updated_ts: float


class PsycheV2State(BaseModel):
    """Current transient psychological state."""

    user_id: str
    mood: float = 0.5
    energy: float = 0.5
    focus: float = 0.5
    pressure: float = 0.0
    stability: float = 0.5
    certainty: float = 0.5
    social_openness: float = 0.5
    task_aggression: float = 0.5
    verbosity_bias: float = 0.5
    tool_bias: float = 0.5
    web_bias: float = 0.5
    current_mode: PsycheMode = "neutral"
    pending_mode: str = ""
    mode_streak: int = 0
    pressure_smoothed: float = 0.0
    updated_ts: float


class PsycheV2Event(BaseModel):
    """Record of a psyche-affecting event."""

    id: str
    user_id: str
    event_type: PsycheEventType
    delta: dict[str, float] = Field(default_factory=dict)
    reason_text: str
    source_ref: str | None = None
    created_ts: float


class PsycheV2BehaviorRule(BaseModel):
    """Conditional behavior adjustment rule."""

    id: str
    user_id: str
    rule_name: str
    trigger: dict[str, Any] = Field(default_factory=dict)
    behavior_adjustment: dict[str, float] = Field(default_factory=dict)
    priority: int = 0
    is_active: bool = True
    created_ts: float
    updated_ts: float


class PsycheV2Habit(BaseModel):
    """Learned behavioral habit from repeated patterns."""

    id: str
    user_id: str
    habit_name: str
    habit_type: str
    intensity: float = 0.0
    reinforcement_count: int = 0
    last_reinforced_ts: float
    context: dict[str, Any] = Field(default_factory=dict)
    created_ts: float
    updated_ts: float


class PsycheV2SnapshotResponse(BaseModel):
    """Complete psyche snapshot for API consumption."""

    user_id: str
    profile: PsycheV2Profile
    state: PsycheV2State
    active_rules: list[PsycheV2BehaviorRule] = Field(default_factory=list)
    active_habits: list[PsycheV2Habit] = Field(default_factory=list)
    recent_events: list[PsycheV2Event] = Field(default_factory=list)
    derived_policy: dict[str, Any] = Field(default_factory=dict)


class PsycheV2RelationsResponse(BaseModel):
    """Relation dynamics snapshot."""

    user_id: str
    trust: float = 0.5
    friction: float = 0.0
    warmth: float = 0.5
    directness_tolerance: float = 0.5
    collaboration_confidence: float = 0.5
    familiarity: float = 0.5
    sync: float = 0.5


class PsycheV2HabitsResponse(BaseModel):
    """Active habits for user."""

    user_id: str
    habits: list[PsycheV2Habit] = Field(default_factory=list)
    total_count: int = 0


class PsycheV2ReflectResponse(BaseModel):
    """Result of psyche reflection operation."""

    user_id: str
    events_analyzed: int = 0
    profile_updated: bool = False
    state_updated: bool = False
    new_rules_created: int = 0
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    reflection_summary: str = ""
    reflected_at: float
