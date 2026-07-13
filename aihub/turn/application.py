"""ChatTurnApplicationService — canonical entry for one chat turn."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from aihub.chat_contracts import ChatTurnInput, ChatTurnResult
from aihub.turn.concurrency import async_session_turn_lock
from aihub.turn.errors import TurnConflictError, TurnRuntimeError
from aihub.turn.idempotency import begin_or_reuse_turn, complete_turn, ensure_turn_schema
from aihub.turn.models import (
    ExecutionMode,
    PrincipalIdentity,
    RuntimeEnvironment,
    TurnContext,
    new_turn_id,
    stable_idempotency_key,
)
from aihub.turn.trace import TraceBuilder

log = logging.getLogger(__name__)


def resolve_environment(turn: ChatTurnInput) -> RuntimeEnvironment:
    """Trusted mode only from explicit field — never from user_id/message."""
    explicit = getattr(turn, "runtime_mode", None)
    return RuntimeEnvironment.from_explicit_mode(explicit)


def build_turn_context(
    turn: ChatTurnInput,
    *,
    turn_id: str | None = None,
    environment: RuntimeEnvironment | None = None,
) -> TurnContext:
    env = environment or resolve_environment(turn)
    request_id = str(getattr(turn, "request_id", None) or uuid.uuid4())
    correlation_id = str(getattr(turn, "correlation_id", None) or request_id)
    client_key = getattr(turn, "idempotency_key", None)
    idem = stable_idempotency_key(
        user_id=turn.user_id,
        session_id=turn.session_id,
        message=turn.message,
        attached_file_ids=list(turn.attached_file_ids or []),
        client_key=client_key,
    )
    tid = turn_id or str(getattr(turn, "turn_id", None) or "") or new_turn_id()
    principal = PrincipalIdentity(user_id=turn.user_id)
    return TurnContext(
        turn_id=tid,
        request_id=request_id,
        correlation_id=correlation_id,
        user_id=turn.user_id,
        session_id=turn.session_id,
        message=turn.message,
        history=list(turn.history or []),
        attachments=list(turn.attached_file_ids or []),
        mode=str(turn.mode or "chat"),
        include_debug=bool(turn.include_debug),
        principal=principal,
        environment=env,
        idempotency_key=idem,
        tool_policy_overrides=dict(turn.tool_policy_overrides or {}),
        input_via_stt=bool(getattr(turn, "input_via_stt", False)),
    )


class ChatTurnApplicationService:
    """HTTP → pipeline entry. Owns idempotency + session lock; delegates core to TurnOps."""

    def __init__(self, ops: Any) -> None:
        self._ops = ops

    async def execute(self, turn: ChatTurnInput) -> ChatTurnResult:
        ensure_turn_schema()
        env = resolve_environment(turn)
        ctx = build_turn_context(turn, environment=env)
        trace = TraceBuilder(
            turn_id=ctx.turn_id,
            request_id=ctx.request_id,
            correlation_id=ctx.correlation_id,
        )

        gate = begin_or_reuse_turn(
            turn_id=ctx.turn_id,
            idempotency_key=ctx.idempotency_key,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            request_id=ctx.request_id,
            correlation_id=ctx.correlation_id,
        )
        if gate["action"] == "reuse":
            cached = gate.get("result") or {}
            return ChatTurnResult.model_validate(cached)
        if gate["action"] == "conflict":
            raise TurnConflictError(turn_id=str(gate.get("turn_id") or ctx.turn_id))
        # retry/started may reuse prior turn_id
        ctx.turn_id = str(gate["turn_id"])

        # Inject turn_id into payload for downstream ops / durable jobs
        if hasattr(turn, "model_copy"):
            turn = turn.model_copy(
                update={
                    "turn_id": ctx.turn_id,
                    "idempotency_key": ctx.idempotency_key,
                    "request_id": ctx.request_id,
                    "correlation_id": ctx.correlation_id,
                    "runtime_mode": env.mode.value,
                }
            )

        started = time.monotonic()
        try:
            async with async_session_turn_lock(
                user_id=ctx.user_id,
                session_id=ctx.session_id,
                turn_id=ctx.turn_id,
                timeout_s=min(45.0, env.turn_deadline_s),
                lease_s=env.turn_deadline_s + 30.0,
            ):
                # Bind context onto ops for single-turn_id write-backs
                self._ops._active_turn_ctx = ctx  # noqa: SLF001
                self._ops._active_trace_builder = trace  # noqa: SLF001
                result = await self._ops.run_turn_core(turn)
                # Stamp canonical ids into trace
                if isinstance(result.trace, dict):
                    result.trace.setdefault("schema_version", "turn-trace-v1")
                    result.trace["turn_id"] = ctx.turn_id
                    result.trace["request_id"] = ctx.request_id
                    result.trace["correlation_id"] = ctx.correlation_id
                    result.trace["idempotency_key"] = ctx.idempotency_key
                    result.trace["runtime_mode"] = env.mode.value
                    result.trace.setdefault(
                        "duration_ms",
                        round((time.monotonic() - started) * 1000.0, 2),
                    )
                complete_turn(
                    ctx.turn_id,
                    status="succeeded",
                    result=result.model_dump(),
                )
                return result
        except TurnRuntimeError as exc:
            complete_turn(
                ctx.turn_id,
                status="failed",
                error=exc.info.to_trace_dict(),
            )
            raise
        except Exception as exc:
            complete_turn(
                ctx.turn_id,
                status="failed",
                error={"type": type(exc).__name__, "error": str(exc)[:800]},
            )
            raise
        finally:
            self._ops._active_turn_ctx = None  # noqa: SLF001
            self._ops._active_trace_builder = None  # noqa: SLF001
