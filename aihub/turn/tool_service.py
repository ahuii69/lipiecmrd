"""Production ToolExecutionService — validation, limits, timeouts, idempotency."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from aihub.chat_contracts import ToolCallRequest, ToolCallResult
from aihub.tools.router import ToolRouter, _normalize_tool_name
from aihub.tools.types import ToolExecutionContext
from aihub.turn.errors import ToolArgumentsError, ToolExecutionError, ToolTimeoutError, TurnCancelledError
from aihub.turn.idempotency import claim_effect, finish_effect
from aihub.turn.models import RuntimeEnvironment, ToolExecutionResult
from aihub.turn.trace import TraceBuilder, tool_result_summary

log = logging.getLogger(__name__)


def parse_tool_arguments(raw: Any, *, max_bytes: int) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        blob = json.dumps(raw, ensure_ascii=False)
        if len(blob.encode("utf-8")) > max_bytes:
            raise ToolArgumentsError("Argumenty narzędzia przekraczają limit rozmiaru.")
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        if len(text.encode("utf-8")) > max_bytes:
            raise ToolArgumentsError("Argumenty narzędzia przekraczają limit rozmiaru.")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolArgumentsError("Nieprawidłowy JSON argumentów narzędzia.") from exc
        if not isinstance(parsed, dict):
            raise ToolArgumentsError("Argumenty narzędzia muszą być obiektem JSON.")
        return parsed
    raise ToolArgumentsError("Nieobsługiwany typ argumentów narzędzia.")


def truncate_tool_output(output: dict[str, Any], *, max_bytes: int) -> tuple[dict[str, Any], bool]:
    try:
        raw = json.dumps(output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = str(output)
    encoded = raw.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return output, False
    preview = encoded[: max(0, max_bytes - 80)].decode("utf-8", errors="replace")
    return {
        "truncated": True,
        "preview": preview,
        "original_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }, True


class ToolExecutionService:
    def __init__(self, router: ToolRouter) -> None:
        self._router = router

    async def execute_one(
        self,
        call: ToolCallRequest,
        ctx: ToolExecutionContext,
        *,
        environment: RuntimeEnvironment | None = None,
        turn_id: str = "",
        cancelled: bool = False,
        remaining_s: float | None = None,
        trace: TraceBuilder | None = None,
    ) -> ToolExecutionResult:
        env = environment or RuntimeEnvironment()
        if cancelled:
            raise TurnCancelledError(internal_detail=f"tool:{call.name}")

        started = time.monotonic()
        name = _normalize_tool_name(call.name)
        try:
            args = parse_tool_arguments(call.arguments, max_bytes=env.tool_max_argument_bytes)
        except ToolArgumentsError as exc:
            result = ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=name,
                ok=False,
                error=str(exc),
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
            if trace:
                trace.add_tool_attempt(result.model_dump())
            return result

        normalized = ToolCallRequest(
            tool_call_id=call.tool_call_id,
            name=name,
            arguments=args,
        )

        # Side-effect tools: claim idempotency slot when turn_id present
        side_effecting = False
        try:
            tool_def = self._router._registry.get(name)  # noqa: SLF001 — intentional
            side_effecting = not bool(getattr(tool_def, "read_only", False))
        except Exception:
            side_effecting = True

        effect_key = f"tool:{name}:{call.tool_call_id}"
        if turn_id and side_effecting:
            claimed = claim_effect(turn_id, effect_key)
            if claimed.get("action") == "skip":
                cached = claimed.get("result") or {}
                return ToolExecutionResult(
                    tool_call_id=call.tool_call_id,
                    name=name,
                    ok=bool(cached.get("ok", True)),
                    output=dict(cached.get("output") or {}),
                    error=cached.get("error"),
                    latency_ms=0.0,
                    side_effecting=True,
                )

        timeout = env.tool_default_timeout_s
        if remaining_s is not None:
            timeout = min(timeout, max(0.5, remaining_s))

        try:
            tr: ToolCallResult = await asyncio.wait_for(
                self._router.execute(normalized, ctx),
                timeout=timeout,
            )
            output, truncated = truncate_tool_output(
                dict(tr.output or {}), max_bytes=env.tool_max_result_bytes
            )
            result = ToolExecutionResult(
                tool_call_id=tr.tool_call_id,
                name=tr.name or name,
                ok=bool(tr.ok),
                output=output,
                error=tr.error,
                latency_ms=float(tr.latency_ms or (time.monotonic() - started) * 1000.0),
                side_effecting=side_effecting,
                truncated=truncated,
                result_bytes=int(tool_result_summary(output)["bytes"]),
                result_hash=str(tool_result_summary(output)["sha256"]),
            )
            if turn_id and side_effecting:
                finish_effect(
                    turn_id,
                    effect_key,
                    ok=result.ok,
                    result={
                        "ok": result.ok,
                        "output": result.output,
                        "error": result.error,
                    },
                    error=result.error,
                )
            if trace:
                trace.add_tool_attempt(result.model_dump())
            return result
        except asyncio.TimeoutError as exc:
            if turn_id and side_effecting:
                finish_effect(turn_id, effect_key, ok=False, error="timeout")
            err = ToolTimeoutError(internal_detail=name, cause=exc, turn_id=turn_id)
            result = ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=name,
                ok=False,
                error=str(err),
                latency_ms=(time.monotonic() - started) * 1000.0,
                side_effecting=side_effecting,
            )
            if trace:
                trace.add_tool_attempt(result.model_dump())
            return result
        except Exception as exc:  # noqa: BLE001
            if turn_id and side_effecting:
                finish_effect(turn_id, effect_key, ok=False, error=str(exc)[:500])
            result = ToolExecutionResult(
                tool_call_id=call.tool_call_id,
                name=name,
                ok=False,
                error=str(exc)[:800],
                latency_ms=(time.monotonic() - started) * 1000.0,
                side_effecting=side_effecting,
            )
            if trace:
                trace.add_tool_attempt(result.model_dump())
            return result

    async def execute_round(
        self,
        calls: list[ToolCallRequest],
        ctx: ToolExecutionContext,
        *,
        environment: RuntimeEnvironment | None = None,
        turn_id: str = "",
        call_count_so_far: int = 0,
        cancelled: bool = False,
        remaining_s: float | None = None,
        trace: TraceBuilder | None = None,
    ) -> list[ToolExecutionResult]:
        env = environment or RuntimeEnvironment()
        if len(calls) > env.tool_max_calls_per_round:
            raise ToolExecutionError(
                f"Zbyt wiele wywołań narzędzi w rundzie ({len(calls)}).",
                code="tool_round_limit",
            )
        if call_count_so_far + len(calls) > env.tool_max_calls_per_turn:
            raise ToolExecutionError(
                "Przekroczono limit narzędzi w turze.",
                code="tool_turn_limit",
            )
        results: list[ToolExecutionResult] = []
        for call in calls:
            results.append(
                await self.execute_one(
                    call,
                    ctx,
                    environment=env,
                    turn_id=turn_id,
                    cancelled=cancelled,
                    remaining_s=remaining_s,
                    trace=trace,
                )
            )
        return results
