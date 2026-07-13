"""Canonical turn models for the chat pipeline."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ExecutionMode(str, Enum):
    PRODUCTION = "production"
    TEST = "test"
    AUDIT = "audit"


class TurnStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EffectType(str, Enum):
    MEMORY_V1 = "memory_v1"
    MEMORY_V2 = "memory_v2"
    PSYCHE_EVOLVE = "psyche_evolve"
    PSYCHE_V2_OUTCOME = "psyche_v2_outcome"
    EXPERIENCE = "experience"
    REFLECTION = "reflection"
    TRANSCRIPT = "transcript"
    TURN_COMPLETED_EVENT = "turn_completed_event"


class RuntimeEnvironment(BaseModel):
    """Trusted execution policy — never derived from user-controlled message text."""

    mode: ExecutionMode = ExecutionMode.PRODUCTION
    allow_side_effects: bool = True
    allow_write_backs: bool = True
    allow_transcript: bool = True
    allow_experience_lookup: bool = True
    provider_max_attempts: int = Field(default=3, ge=1, le=8)
    provider_connect_timeout_s: float = Field(default=5.0, ge=0.1, le=60.0)
    provider_read_timeout_s: float = Field(default=60.0, ge=1.0, le=600.0)
    provider_total_timeout_s: float = Field(default=90.0, ge=1.0, le=600.0)
    turn_deadline_s: float = Field(default=180.0, ge=5.0, le=900.0)
    tool_max_calls_per_round: int = Field(default=8, ge=1, le=32)
    tool_max_calls_per_turn: int = Field(default=16, ge=1, le=64)
    tool_max_iterations: int = Field(default=6, ge=1, le=20)
    tool_max_argument_bytes: int = Field(default=64_000, ge=1024, le=2_000_000)
    tool_max_result_bytes: int = Field(default=120_000, ge=1024, le=5_000_000)
    tool_default_timeout_s: float = Field(default=30.0, ge=1.0, le=300.0)

    @classmethod
    def from_explicit_mode(cls, mode: str | ExecutionMode | None) -> "RuntimeEnvironment":
        if isinstance(mode, ExecutionMode):
            em = mode
        else:
            raw = (mode or "production").strip().lower()
            if raw == "audit":
                em = ExecutionMode.AUDIT
            elif raw == "test":
                em = ExecutionMode.TEST
            else:
                em = ExecutionMode.PRODUCTION
        if em == ExecutionMode.AUDIT:
            return cls(
                mode=em,
                allow_side_effects=False,
                allow_write_backs=False,
                allow_transcript=False,
                allow_experience_lookup=False,
            )
        return cls(mode=em)


class PrincipalIdentity(BaseModel):
    account_id: str = ""
    username: str = ""
    tenant_id: str = ""
    role: str = ""
    user_id: str = ""


class TurnDecision(BaseModel):
    selected_strategy: str = "instant"
    intent: str = "chat"
    web_decision: str = "off"
    research_required: bool = False
    handoff_to_agent: bool = False
    grounding_mode: str = "direct"
    raw: dict[str, Any] = Field(default_factory=dict)


class ProviderAttempt(BaseModel):
    attempt: int
    provider: str = ""
    model: str = ""
    status: str = ""
    latency_ms: float = 0.0
    error_code: str = ""
    retry_after_s: float | None = None


class ProviderExecutionResult(BaseModel):
    ok: bool
    content: str = ""
    provider: str = ""
    model: str = ""
    finish_reason: str = "stop"
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    attempts: list[ProviderAttempt] = Field(default_factory=list)
    used_fallback: bool = False
    error: dict[str, Any] | None = None


class ToolExecutionResult(BaseModel):
    tool_call_id: str
    name: str
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    latency_ms: float = 0.0
    side_effecting: bool = False
    truncated: bool = False
    result_bytes: int = 0
    result_hash: str = ""


class WebGroundingResult(BaseModel):
    executed: bool = False
    required: bool = False
    grounded: bool = False
    source_count: int = 0
    quality: str = "none"
    operation: str = ""
    detail: str = ""
    sources: list[dict[str, Any]] = Field(default_factory=list)
    raw_summary: dict[str, Any] = Field(default_factory=dict)


class MemoryReadResult(BaseModel):
    lookup_happened: bool = False
    substantive: bool = False
    stm_included: bool = False
    match_count: int = 0
    brief: str = ""
    used: dict[str, Any] = Field(default_factory=dict)
    v2_snapshot: dict[str, Any] = Field(default_factory=dict)
    memory_context: dict[str, Any] = Field(default_factory=dict)


class PsycheSnapshot(BaseModel):
    v1: dict[str, Any] = Field(default_factory=dict)
    v2: dict[str, Any] = Field(default_factory=dict)
    behavior: dict[str, Any] = Field(default_factory=dict)
    brief: str = ""


class WriteBackResult(BaseModel):
    effect_type: str
    attempted: bool = False
    succeeded: bool = False
    skipped_duplicate: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class TurnCompletion(BaseModel):
    turn_id: str
    status: TurnStatus = TurnStatus.SUCCEEDED
    response_text: str = ""
    provider: str = ""
    model: str = ""
    grounding_mode: str = "direct"
    write_backs: list[WriteBackResult] = Field(default_factory=list)
    duration_ms: float = 0.0
    trace: dict[str, Any] = Field(default_factory=dict)


@dataclass
class CancellationState:
    cancelled: bool = False
    reason: str = ""

    def raise_if_cancelled(self, turn_id: str = "") -> None:
        if self.cancelled:
            from aihub.turn.errors import TurnCancelledError

            raise TurnCancelledError(turn_id=turn_id, internal_detail=self.reason)


@dataclass
class TurnContext:
    """Canonical object representing one chat turn across the entire pipeline."""

    turn_id: str
    request_id: str
    correlation_id: str
    user_id: str
    session_id: str
    message: str
    history: list[Any] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    mode: str = "chat"
    include_debug: bool = False
    principal: PrincipalIdentity = field(default_factory=PrincipalIdentity)
    environment: RuntimeEnvironment = field(default_factory=RuntimeEnvironment)
    started_at: float = field(default_factory=time.time)
    deadline_at: float = 0.0
    idempotency_key: str = ""
    cancellation: CancellationState = field(default_factory=CancellationState)
    capabilities: list[Any] = field(default_factory=list)
    memory: MemoryReadResult = field(default_factory=MemoryReadResult)
    psyche: PsycheSnapshot = field(default_factory=PsycheSnapshot)
    decision: TurnDecision = field(default_factory=TurnDecision)
    provider_state: dict[str, Any] = field(default_factory=dict)
    tool_state: dict[str, Any] = field(default_factory=list)  # type: ignore[assignment]
    web: WebGroundingResult = field(default_factory=WebGroundingResult)
    completion: Optional[TurnCompletion] = None
    tool_policy_overrides: dict[str, Any] = field(default_factory=dict)
    input_via_stt: bool = False
    status: TurnStatus = TurnStatus.PENDING
    errors: list[dict[str, Any]] = field(default_factory=list)
    experience_signal: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.deadline_at:
            self.deadline_at = self.started_at + float(self.environment.turn_deadline_s)
        if self.tool_state is None or isinstance(self.tool_state, list):
            # normalize accidental default
            if not isinstance(self.tool_state, dict):
                self.tool_state = {"results": list(self.tool_state or []), "call_count": 0}

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.deadline_at - time.time())

    @property
    def skip_write_backs(self) -> bool:
        return (
            not self.environment.allow_write_backs
            or self.environment.mode == ExecutionMode.AUDIT
        )


def new_turn_id() -> str:
    return str(uuid.uuid4())


def stable_idempotency_key(
    *,
    user_id: str,
    session_id: str,
    message: str,
    attached_file_ids: list[str] | None = None,
    client_key: str | None = None,
) -> str:
    if client_key and client_key.strip():
        return client_key.strip()[:256]
    import hashlib

    digest = hashlib.sha256(
        f"{user_id}\0{session_id}\0{message}\0{','.join(attached_file_ids or [])}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"auto-{digest[:40]}"
