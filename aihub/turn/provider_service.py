"""Provider execution with timeout, retry, classification — no TypeError sniffing."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Protocol

from aihub.chat_contracts import ChatMessage, ModelResponse, ProviderUsage, ToolCallRequest
from aihub.config import LLM_MODEL_NAME, LLM_STREAMING_ENABLED
from aihub.chat_stream_session import stream_session_active
from aihub.llm.provider_types import ProviderChatRequest, ProviderToolSpec
from aihub.turn.errors import (
    ProviderExecutionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    TurnCancelledError,
)
from aihub.turn.models import ProviderAttempt, ProviderExecutionResult, RuntimeEnvironment
from aihub.turn.trace import TraceBuilder

log = logging.getLogger(__name__)


class SupportsGenerate(Protocol):
    provider_name: str

    async def generate(self, request: ProviderChatRequest) -> ModelResponse: ...


def classify_provider_error(exc: BaseException) -> tuple[str, bool, type[ProviderExecutionError]]:
    """Return (code, retryable, error_class)."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in msg or "timed out" in msg:
        return "provider_timeout", True, ProviderTimeoutError
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return "provider_rate_limit", True, ProviderRateLimitError
    if "401" in msg or "403" in msg or "unauthorized" in msg or "forbidden" in msg or "api key" in msg:
        return "provider_auth", False, ProviderExecutionError
    if "400" in msg or "invalid request" in msg or "validation" in msg:
        return "provider_invalid_request", False, ProviderExecutionError
    if "503" in msg or "502" in msg or "unavailable" in msg or "connection" in msg:
        return "provider_unavailable", True, ProviderExecutionError
    if "cancel" in msg or isinstance(exc, asyncio.CancelledError):
        return "provider_cancelled", False, ProviderExecutionError
    if isinstance(exc, (AttributeError, TypeError, NameError, SyntaxError)):
        return "provider_internal_bug", False, ProviderExecutionError
    if "json" in msg or "malformed" in name or "decode" in msg:
        return "provider_malformed", False, ProviderExecutionError
    return "provider_error", True, ProviderExecutionError


def _parse_retry_after(exc: BaseException) -> float | None:
    headers = getattr(exc, "headers", None) or getattr(exc, "response", None)
    if headers is None:
        return None
    try:
        if hasattr(headers, "get"):
            raw = headers.get("Retry-After") or headers.get("retry-after")
        else:
            raw = None
        if raw is None:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def normalize_model_response(raw: Any, *, provider_name: str, default_model: str) -> ModelResponse:
    if isinstance(raw, ModelResponse):
        return raw
    if isinstance(raw, dict):
        message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
        content = str(raw.get("content") or message.get("content") or "")
        raw_tool_calls = raw.get("tool_calls") or message.get("tool_calls") or []
        tool_calls: list[ToolCallRequest] = []
        for idx, call in enumerate(raw_tool_calls):
            if isinstance(call, ToolCallRequest):
                tool_calls.append(call)
            elif isinstance(call, dict):
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                args = call.get("arguments") or fn.get("arguments") or {}
                if isinstance(args, str):
                    import json

                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {"_raw": args}
                tool_calls.append(
                    ToolCallRequest(
                        tool_call_id=str(call.get("tool_call_id") or call.get("id") or f"tool-{idx}"),
                        name=str(call.get("name") or fn.get("name") or "tool"),
                        arguments=dict(args) if isinstance(args, dict) else {},
                    )
                )
        usage_obj = raw.get("usage")
        usage = usage_obj if isinstance(usage_obj, ProviderUsage) else ProviderUsage()
        return ModelResponse(
            provider=provider_name,
            model=str(raw.get("model") or default_model),
            content=content,
            finish_reason=str(raw.get("finish_reason") or raw.get("stop_reason") or "stop"),
            tool_calls=tool_calls,
            usage=usage,
            latency_ms=float(raw.get("latency_ms") or 0.0),
            raw_response_id=str(raw.get("raw_response_id") or raw.get("id") or ""),
        )
    if all(hasattr(raw, attr) for attr in ("content", "model", "provider")):
        raw_tool_calls = list(getattr(raw, "tool_calls", []) or [])
        tool_calls = []
        for idx, call in enumerate(raw_tool_calls):
            if isinstance(call, ToolCallRequest):
                tool_calls.append(call)
            elif isinstance(call, dict):
                tool_calls.append(
                    ToolCallRequest(
                        tool_call_id=str(call.get("tool_call_id") or call.get("id") or f"tool-{idx}"),
                        name=str(call.get("name") or "tool"),
                        arguments=dict(call.get("arguments") or {}),
                    )
                )
        usage = getattr(raw, "usage", None)
        if not isinstance(usage, ProviderUsage):
            usage = ProviderUsage()
        return ModelResponse(
            provider=str(getattr(raw, "provider", provider_name)),
            model=str(getattr(raw, "model", default_model)),
            content=str(getattr(raw, "content", "") or ""),
            finish_reason=str(getattr(raw, "finish_reason", "stop") or "stop"),
            tool_calls=tool_calls,
            usage=usage,
            latency_ms=float(getattr(raw, "latency_ms", 0.0) or 0.0),
            raw_response_id=str(getattr(raw, "raw_response_id", "") or ""),
        )
    raise ProviderExecutionError(
        "Provider zwrócił nieobsługiwany typ odpowiedzi.",
        retryable=False,
        code="provider_malformed",
        internal_detail=type(raw).__name__,
    )


class ProviderExecutionService:
    def __init__(self, provider: SupportsGenerate) -> None:
        self._provider = provider

    @property
    def provider_name(self) -> str:
        return str(
            getattr(
                self._provider,
                "provider_name",
                getattr(self._provider, "name", "mock"),
            )
        )

    async def execute(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ProviderToolSpec] | None = None,
        environment: RuntimeEnvironment | None = None,
        cancelled: bool = False,
        remaining_s: float | None = None,
        trace: TraceBuilder | None = None,
    ) -> ProviderExecutionResult:
        env = environment or RuntimeEnvironment()
        if cancelled:
            raise TurnCancelledError(internal_detail="provider_call")

        tools = tools or []
        use_stream = stream_session_active() and LLM_STREAMING_ENABLED and not tools
        attempts: list[ProviderAttempt] = []
        last_err: BaseException | None = None
        max_attempts = env.provider_max_attempts

        for attempt_i in range(1, max_attempts + 1):
            if cancelled:
                raise TurnCancelledError(internal_detail="provider_retry")
            budget = env.provider_total_timeout_s
            if remaining_s is not None:
                budget = min(budget, max(0.5, remaining_s))
            req = ProviderChatRequest(
                messages=messages,
                model=LLM_MODEL_NAME,
                tools=tools,
                stream=use_stream,
            )
            started = time.monotonic()
            try:
                # Explicit Protocol call — no TypeError signature sniffing.
                generate = self._provider.generate
                raw = await asyncio.wait_for(generate(req), timeout=budget)
                model = normalize_model_response(
                    raw, provider_name=self.provider_name, default_model=LLM_MODEL_NAME
                )
                latency = (time.monotonic() - started) * 1000.0
                attempt = ProviderAttempt(
                    attempt=attempt_i,
                    provider=model.provider or self.provider_name,
                    model=model.model,
                    status="ok",
                    latency_ms=latency,
                )
                attempts.append(attempt)
                if trace:
                    trace.add_provider_attempt(attempt.model_dump())
                return ProviderExecutionResult(
                    ok=True,
                    content=model.content,
                    provider=model.provider,
                    model=model.model,
                    finish_reason=model.finish_reason,
                    tool_calls=[tc.model_dump() for tc in model.tool_calls],
                    usage=model.usage.model_dump() if model.usage else {},
                    latency_ms=latency,
                    attempts=attempts,
                )
            except Exception as exc:  # noqa: BLE001 — classified below
                from aihub.llm.provider_types import ProviderError as _PE

                # Preserve canonical ProviderError for ChatRuntime fallback path.
                if isinstance(exc, _PE):
                    raise
                last_err = exc
                code, retryable, err_cls = classify_provider_error(exc)
                latency = (time.monotonic() - started) * 1000.0
                retry_after = _parse_retry_after(exc)
                attempt = ProviderAttempt(
                    attempt=attempt_i,
                    provider=self.provider_name,
                    model=LLM_MODEL_NAME,
                    status=code,
                    latency_ms=latency,
                    error_code=code,
                    retry_after_s=retry_after,
                )
                attempts.append(attempt)
                if trace:
                    trace.add_provider_attempt(attempt.model_dump())
                if not retryable or attempt_i >= max_attempts:
                    raise err_cls(
                        "Provider chwilowo niedostępny." if retryable else "Błąd providera.",
                        code=code,
                        retryable=retryable,
                        internal_detail=str(exc)[:800],
                        cause=exc,
                    ) from exc
                # exponential backoff + jitter; honor Retry-After
                base = retry_after if retry_after is not None else min(8.0, 0.4 * (2 ** (attempt_i - 1)))
                delay = base + random.uniform(0, 0.25 * base)
                await asyncio.sleep(delay)

        raise ProviderExecutionError(
            "Provider nie odpowiedział.",
            internal_detail=str(last_err or ""),
            cause=last_err,
        )
