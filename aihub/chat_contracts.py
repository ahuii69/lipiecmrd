#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Stable internal contracts for chat/runtime/provider/tool integration."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ToolMode = Literal["chat", "agent", "readonly", "debug"]
MessageRole = Literal["system", "user", "assistant", "tool"]


class ProviderUsage(BaseModel):
    """Provider token/cost telemetry (when available)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reporting_mode: Literal["provider", "partial", "unavailable"] = "unavailable"


class ToolCallRequest(BaseModel):
    """Single tool request emitted by the model runtime."""

    tool_call_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    """Internal message representation used by provider abstraction."""

    role: MessageRole
    content: str = ""
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: List[ToolCallRequest] = Field(default_factory=list)


class CapabilityDescriptor(BaseModel):
    """Tool/capability contract exposed to LLM/frontend."""

    name: str
    description: str
    capability_group: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    enabled: bool = True
    read_only: bool = False
    requires_confirmation: bool = False
    timeout_seconds: float = 15.0
    visibility: List[ToolMode] = Field(default_factory=lambda: ["chat", "agent"])


class ChatTurnInput(BaseModel):
    """Frontend-facing input for one chat turn."""

    user_id: str = Field(default="default", min_length=1, max_length=128)
    session_id: str = Field(default="default", min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=200_000)
    mode: ToolMode = "chat"
    include_debug: bool = False
    history: List[ChatMessage] = Field(default_factory=list)
    tool_policy_overrides: Dict[str, Any] = Field(default_factory=dict)
    attached_file_ids: List[str] = Field(
        default_factory=list,
        max_length=5,
        description="IDs z POST /chat/upload (max 5 na turę).",
    )
    input_via_stt: bool = Field(
        default=False,
        description="True gdy treść usera weszła z dyktowania (STT) — chip stt-input.",
    )
    # Canonical turn identity / idempotency (optional; server generates when absent)
    turn_id: Optional[str] = Field(default=None, max_length=128)
    idempotency_key: Optional[str] = Field(default=None, max_length=256)
    request_id: Optional[str] = Field(default=None, max_length=128)
    correlation_id: Optional[str] = Field(default=None, max_length=128)
    # Trusted runtime mode — NEVER derived from user_id/message.
    # Distinct from decision_core["execution_mode"] (direct/planner/agentic).
    runtime_mode: Optional[Literal["production", "test", "audit"]] = Field(
        default=None,
        description="Trusted runtime mode; audit disables write-backs.",
    )


class ChatTurnContext(BaseModel):
    """Runtime context assembled before provider invocation."""

    user_id: str
    session_id: str
    mode: ToolMode
    include_debug: bool = False
    memory_context: Dict[str, Any] = Field(default_factory=dict)
    system_context: Dict[str, Any] = Field(default_factory=dict)
    capabilities: List[CapabilityDescriptor] = Field(default_factory=list)


class ToolCallResult(BaseModel):
    """Normalized execution result for a single tool call."""

    tool_call_id: str
    name: str
    ok: bool
    output: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    latency_ms: float = 0.0


class ModelResponse(BaseModel):
    """Provider-normalized model output."""

    provider: str
    model: str
    content: str = ""
    finish_reason: str = "stop"
    tool_calls: List[ToolCallRequest] = Field(default_factory=list)
    usage: ProviderUsage = Field(default_factory=ProviderUsage)
    latency_ms: float = 0.0
    raw_response_id: str = ""


# ── Blocker Verdict ──────────────────────────────────────────────────────
# Unified, explicit blocker model aggregating all pre-exec decision signals.
# Populated by ChatRuntime._evaluate_blocker_verdict() after decision_core.
# If hard==True, run_turn returns early with blocker info (no provider call).

BlockerType = Literal[
    "none",
    "consistency_conflict",  # user message contradicts known facts
    "repeated_failure",  # same failure pattern occurring repeatedly
    "degraded_runtime",  # strategy selector timed out / failed
    "high_risk_path",  # simulation predicts high risk for best action
    "policy_violation_internal",  # policy engine signals avoid/penalize
    "low_confidence_decision",  # confidence extreme low without other signals
    "resource_exhaustion",  # rate limit / tool failure cascade
    "contradictory_memory_state",  # memory state conflicts with current turn
]

BlockerResolution = Literal[
    "allow",  # no blocker, proceed normally
    "caution_pass",  # warn but proceed on same path
    "downgrade",  # reduce strategy aggressiveness (e.g. agentic→contextual)
    "reroute",  # suggest alternative action / handoff bias
    "hard_block",  # full stop, no provider call
]

BlockerScope = Literal[
    "turn",  # affects only this turn
    "session",  # affects remainder of session
    "user",  # user-level restriction
]

BlockerSeverity = Literal[
    "info",  # informational, no gating
    "caution",  # warn user/operator, execution proceeds
    "hard",  # execution blocked, return early
]


class BlockerVerdict(BaseModel):
    """Unified blocker gate evaluated before provider call.

    This is the single source of truth for whether and why a turn
    should be blocked, cautioned, or allowed to proceed.

    resolution determines the execution path:
    - hard_block → return early, NO provider call
    - downgrade  → reduce strategy aggressiveness, then proceed
    - reroute    → adjust handoff bias / alternative action, then proceed
    - caution_pass → warn but proceed normally
    - allow      → no blocker
    """

    blocker_active: bool = False
    blocker_type: BlockerType = "none"
    blocker_scope: BlockerScope = "turn"
    blocker_severity: BlockerSeverity = "info"
    hard: bool = False  # True → execution blocked
    resolution: BlockerResolution = "allow"  # execution path chosen
    reason: str = ""  # human-readable explanation
    source: str = ""  # which subsystem raised it
    recommended_action: str = ""  # what user/operator should do
    contributing_signals: List[str] = Field(  # which decision_core keys contributed
        default_factory=list,
    )
    confidence: float = 0.0  # 0.0–1.0; how certain the blocker is
    # ── DEV/USER presentation ──
    user_message: str = ""  # short, human-friendly for end-user
    dev_message: str = ""  # verbose, diagnostic for operator
    # ── Remediation ──
    remediation_hint: str = ""  # optional fix suggestion
    next_best_action: str = ""  # optional alternative action
    # ── Feedback loop ──
    feedback_applied: bool = False  # True if history escalated/de-escalated
    escalated_from_history: bool = False  # severity was RAISED by past outcomes
    deescalated_from_history: bool = False  # severity was LOWERED by past outcomes
    feedback_detail: str = ""  # why feedback changed the verdict
    # ── Audit ──
    turn_id: str = ""  # turn identifier for tracing
    timestamp: float = 0.0  # evaluation time
    signals_count: int = 0  # how many raw signals contributed

    @staticmethod
    def allow() -> "BlockerVerdict":
        """Factory for the no-blocker baseline."""
        return BlockerVerdict(resolution="allow")


class ChatTurnResult(BaseModel):
    """Final stable payload returned by chat runtime/API."""

    ok: bool
    response_text: str
    model: str
    provider: str
    tool_calls: List[ToolCallRequest] = Field(default_factory=list)
    tool_results: List[ToolCallResult] = Field(default_factory=list)
    selected_mode: ToolMode = "chat"
    usage: ProviderUsage = Field(default_factory=ProviderUsage)
    trace: Dict[str, Any] = Field(default_factory=dict)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    debug: Optional[Dict[str, Any]] = None
    attachments_summary: Optional[str] = None
    context_chips: List[str] = Field(
        default_factory=list,
        description="Krótkie etykiety źródeł odpowiedzi (UI / audyt).",
    )
