"""Idempotent turn completion / write-back orchestration (single turn_id)."""

from __future__ import annotations

import logging
from typing import Any

from aihub.turn.idempotency import claim_effect, finish_effect
from aihub.turn.models import EffectType, TurnContext, WriteBackResult
from aihub.turn.trace import TraceBuilder

log = logging.getLogger(__name__)


class TurnCompletionService:
    """Exactly-once write-backs keyed by turn_id + effect_type."""

    def run_effect(
        self,
        ctx: TurnContext,
        effect_type: str | EffectType,
        fn,
        *,
        trace: TraceBuilder | None = None,
    ) -> WriteBackResult:
        et = effect_type.value if isinstance(effect_type, EffectType) else str(effect_type)
        if ctx.skip_write_backs:
            wb = WriteBackResult(effect_type=et, attempted=False, succeeded=False)
            if trace:
                trace.set_write_back(et, {"skipped": "audit_or_disabled"})
            return wb

        claimed = claim_effect(ctx.turn_id, et)
        if claimed.get("action") == "skip":
            wb = WriteBackResult(
                effect_type=et,
                attempted=True,
                succeeded=True,
                skipped_duplicate=True,
                detail=dict(claimed.get("result") or {}),
            )
            if trace:
                trace.set_write_back(et, {"skipped_duplicate": True})
            return wb

        try:
            result = fn()
            detail = result if isinstance(result, dict) else {"result": result}
            finish_effect(ctx.turn_id, et, ok=True, result=detail)
            wb = WriteBackResult(
                effect_type=et, attempted=True, succeeded=True, detail=detail
            )
            if trace:
                trace.set_write_back(et, {"succeeded": True})
            return wb
        except Exception as exc:  # noqa: BLE001
            log.warning("turn effect failed turn=%s type=%s: %s", ctx.turn_id, et, exc)
            finish_effect(ctx.turn_id, et, ok=False, error=str(exc)[:800])
            wb = WriteBackResult(
                effect_type=et, attempted=True, succeeded=False, error=str(exc)[:800]
            )
            if trace:
                trace.set_write_back(et, {"succeeded": False, "error": str(exc)[:200]})
            return wb

    def durable_turn_completed(
        self,
        ctx: TurnContext,
        *,
        response_text: str,
        intent: str,
        metadata: dict[str, Any],
        reflection: dict[str, Any],
        trace: TraceBuilder | None = None,
    ) -> WriteBackResult:
        def _do() -> dict[str, Any]:
            from aihub.durable_jobs import execute_turn_completed_inline

            # Ensure reflection context uses the SAME turn_id
            reflection_payload = dict(reflection)
            context = dict(reflection_payload.get("context") or {})
            context["turn_id"] = ctx.turn_id
            reflection_payload["context"] = context
            return execute_turn_completed_inline(
                turn_id=ctx.turn_id,
                user_id=ctx.user_id,
                user_message=ctx.message,
                assistant_message=response_text or "",
                intent=intent,
                metadata=metadata,
                reflection=reflection_payload,
            )

        return self.run_effect(ctx, EffectType.EXPERIENCE, _do, trace=trace)

    def memory_v2_outcome(
        self,
        ctx: TurnContext,
        *,
        response_text: str,
        strategy: str,
        grounding_mode: str,
        tool_calls_count: int,
        tool_successes: int,
        tool_failures: int,
        contradictions_present: int,
        memory_matches: int,
        degraded: bool,
        fallback: bool,
        trace: TraceBuilder | None = None,
    ) -> WriteBackResult:
        def _do() -> dict[str, Any]:
            from aihub.memory_core import get_memory_core

            return get_memory_core().record_chat_outcome(
                user_id=ctx.user_id,
                turn_id=ctx.turn_id,
                query_text=ctx.message or "",
                response_text=response_text,
                strategy=strategy,
                grounding_mode=grounding_mode,
                tool_calls_count=tool_calls_count,
                tool_successes=tool_successes,
                tool_failures=tool_failures,
                contradictions_present=contradictions_present,
                memory_matches=memory_matches,
                degraded=degraded,
                fallback=fallback,
            )

        return self.run_effect(ctx, EffectType.MEMORY_V2, _do, trace=trace)

    def psyche_v2_outcome(
        self,
        ctx: TurnContext,
        *,
        outcome_kind: str,
        context: dict[str, Any],
        trace: TraceBuilder | None = None,
    ) -> WriteBackResult:
        def _do() -> dict[str, Any]:
            from aihub.psyche_core import get_psyche_core

            return get_psyche_core().v2_service.apply_outcome_event(
                user_id=ctx.user_id,
                outcome_kind=outcome_kind if outcome_kind != "blocked" else "failure",
                source_ref=ctx.turn_id,
                context=context,
            )

        return self.run_effect(ctx, EffectType.PSYCHE_V2_OUTCOME, _do, trace=trace)
