"""Provider execution with timeout, retry, classification, and reserve failover."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Protocol

from aihub.chat_contracts import ChatMessage, ModelResponse, ProviderUsage, ToolCallRequest
from aihub.config import GROQ_MODEL, LLM_MODEL_NAME, LLM_PRIMARY_PROVIDER, LLM_RESERVE_PROVIDER, LLM_STREAMING_ENABLED, OLLAMA_LLM_MODEL
from aihub.chat_stream_session import stream_session_active
from aihub.llm.failover_policy import (
    failure_class_for_error,
    is_failover_eligible,
    max_retries_before_failover,
    parse_retry_after,
)
from aihub.llm.provider_registry import provider_candidate_names, reserve_provider_names
from aihub.llm.provider_types import ProviderChatRequest, ProviderError, ProviderToolSpec
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
    if isinstance(exc, ProviderError):
        code = failure_class_for_error(exc)
        retryable = bool(exc.retryable)
        if exc.status_code in {401, 402, 403}:
            retryable = False
        if code == "rate_limit":
            return "provider_rate_limit", True, ProviderRateLimitError
        if code == "timeout":
            return "provider_timeout", True, ProviderTimeoutError
        return code, retryable, ProviderExecutionError

    # Preserve typed provider execution failures (e.g. non-text structured content).
    if isinstance(exc, ProviderExecutionError):
        code = str(exc.info.code or "provider_error")
        if code == "provider_timeout":
            return code, True, ProviderTimeoutError
        if code == "provider_rate_limit":
            return code, True, ProviderRateLimitError
        return code, bool(exc.retryable), ProviderExecutionError

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
    ra = parse_retry_after(exc)
    if ra is not None:
        return ra
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
    """Normalize provider output. Content is extracted, never ``str(dict)``."""
    from aihub.response_runtime_guard import extract_assistant_text

    if isinstance(raw, ModelResponse):
        # Provider adapters already return str content; keep as-is (no post-filter).
        return raw
    if isinstance(raw, dict):
        message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
        content_raw = raw.get("content")
        if content_raw in (None, ""):
            content_raw = message.get("content")
        # Root-cause fix: extract text leaves; ChatTurnContext-shaped dicts have none → "".
        content = extract_assistant_text(content_raw)
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
        # Structured payload with no text leaves and no tools is unusable for chat —
        # raise retryable so failover can switch providers (honest empty ≠ silent dump).
        if (
            not content
            and not tool_calls
            and isinstance(content_raw, (dict, list))
            and content_raw not in ([], {})
        ):
            raise ProviderExecutionError(
                "Provider zwrócił treść bez wyodrębnialnego tekstu asystenta.",
                retryable=True,
                code="provider_non_text_content",
                internal_detail=type(content_raw).__name__,
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
            content=extract_assistant_text(getattr(raw, "content", "")),
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


def _attempt_record(
    *,
    provider: str,
    model: str,
    ok: bool,
    status_code: int | None,
    failure_class: str,
    retry_count: int,
    latency_ms: float,
    error_code: str = "",
) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "ok": ok,
        "status_code": status_code,
        "failure_class": failure_class,
        "retry_count": retry_count,
        "duration_ms": round(latency_ms, 2),
        "status": "ok" if ok else (failure_class or error_code or "error"),
        "error_code": error_code,
    }


class ProviderExecutionService:
    """Single canonical LLM call path with DeepInfra → Groq → Ollama failover."""

    def __init__(
        self,
        primary: SupportsGenerate,
        reserve: SupportsGenerate | None = None,
        reserves: list[SupportsGenerate] | None = None,
    ) -> None:
        self._primary = primary
        self._reserves: list[SupportsGenerate] = []
        if reserves:
            self._reserves.extend(reserves)
        elif reserve is not None:
            self._reserves.append(reserve)
        # Backward compat: single-provider attribute
        self._provider = primary

    def _providers_to_try(self) -> list[SupportsGenerate]:
        seen: set[str] = set()
        ordered: list[SupportsGenerate] = []
        for provider in [self._primary, *self._reserves]:
            name = str(getattr(provider, "provider_name", "")).lower()
            if name and name in seen:
                continue
            if name:
                seen.add(name)
            ordered.append(provider)
        return ordered

    @property
    def provider_name(self) -> str:
        return str(
            getattr(
                self._primary,
                "provider_name",
                getattr(self._primary, "name", "mock"),
            )
        )

    def _default_model_for(self, provider: SupportsGenerate) -> str:
        name = str(getattr(provider, "provider_name", "") or "").lower()
        if name == "groq":
            return GROQ_MODEL
        if name == "ollama":
            return OLLAMA_LLM_MODEL
        return LLM_MODEL_NAME

    async def _call_provider_once(
        self,
        provider: SupportsGenerate,
        *,
        req: ProviderChatRequest,
        budget: float,
    ) -> ModelResponse:
        generate = provider.generate
        raw = await asyncio.wait_for(generate(req), timeout=budget)
        default_model = self._default_model_for(provider)
        pname = str(getattr(provider, "provider_name", "provider"))
        return normalize_model_response(raw, provider_name=pname, default_model=default_model)

    async def _execute_on_provider(
        self,
        provider: SupportsGenerate,
        *,
        messages: list[ChatMessage],
        tools: list[ProviderToolSpec],
        environment: RuntimeEnvironment,
        cancelled: bool,
        remaining_s: float | None,
        trace: TraceBuilder | None,
        attempt_offset: int,
        max_tokens: int | None = None,
    ) -> tuple[ModelResponse, list[ProviderAttempt], list[dict[str, Any]]]:
        if cancelled:
            raise TurnCancelledError(internal_detail="provider_call")

        tools = tools or []
        use_stream = stream_session_active() and LLM_STREAMING_ENABLED and not tools
        default_model = self._default_model_for(provider)
        pname = str(getattr(provider, "provider_name", "provider"))

        req = ProviderChatRequest(
            messages=messages,
            model=default_model,
            tools=tools,
            stream=use_stream,
            max_tokens=max_tokens,
        )

        attempts: list[ProviderAttempt] = []
        trace_attempts: list[dict[str, Any]] = []
        last_err: BaseException | None = None
        retry_idx = 0

        while True:
            if cancelled:
                raise TurnCancelledError(internal_detail="provider_retry")
            budget = environment.provider_total_timeout_s
            if remaining_s is not None:
                budget = min(budget, max(0.5, remaining_s))
            started = time.monotonic()
            try:
                model = await self._call_provider_once(provider, req=req, budget=budget)
                latency = (time.monotonic() - started) * 1000.0
                attempt = ProviderAttempt(
                    attempt=attempt_offset + len(attempts) + 1,
                    provider=model.provider or pname,
                    model=model.model,
                    status="ok",
                    latency_ms=latency,
                )
                attempts.append(attempt)
                rec = _attempt_record(
                    provider=model.provider or pname,
                    model=model.model,
                    ok=True,
                    status_code=200,
                    failure_class="",
                    retry_count=retry_idx,
                    latency_ms=latency,
                )
                trace_attempts.append(rec)
                if trace:
                    trace.add_provider_attempt(rec)
                return model, attempts, trace_attempts
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                latency = (time.monotonic() - started) * 1000.0
                if isinstance(exc, ProviderError):
                    status_code = int(exc.status_code or 0) or None
                    fclass = failure_class_for_error(exc)
                    code = fclass
                else:
                    code, _, _ = classify_provider_error(exc)
                    status_code = None
                    fclass = code

                retry_after = _parse_retry_after(exc)
                attempt = ProviderAttempt(
                    attempt=attempt_offset + len(attempts) + 1,
                    provider=pname,
                    model=default_model,
                    status=code,
                    latency_ms=latency,
                    error_code=code,
                    retry_after_s=retry_after,
                )
                attempts.append(attempt)
                rec = _attempt_record(
                    provider=pname,
                    model=default_model,
                    ok=False,
                    status_code=status_code,
                    failure_class=fclass,
                    retry_count=retry_idx,
                    latency_ms=latency,
                    error_code=code,
                )
                trace_attempts.append(rec)
                if trace:
                    trace.add_provider_attempt(rec)

                allowed = max_retries_before_failover(exc)
                if retry_idx < allowed:
                    retry_idx += 1
                    base = retry_after if retry_after is not None else min(8.0, 0.4 * (2 ** (retry_idx - 1)))
                    delay = base + random.uniform(0, 0.25 * base)
                    await asyncio.sleep(delay)
                    continue
                raise exc

        raise ProviderExecutionError(
            "Provider nie odpowiedział.",
            internal_detail=str(last_err or ""),
            cause=last_err,
        )

    def _merge_provider_trace(
        self,
        trace: TraceBuilder | None,
        *,
        trace_attempts: list[dict[str, Any]],
        final_provider: str,
        final_model: str,
        final_ok: bool,
        failover_happened: bool,
        total_ms: float,
    ) -> None:
        if trace is None:
            return
        trace.merge(
            {
                "provider_primary": LLM_PRIMARY_PROVIDER,
                "provider_reserve": LLM_RESERVE_PROVIDER,
                "provider_reserve_chain": reserve_provider_names(),
                "provider_candidates": provider_candidate_names(),
                "provider_attempt_count": len(trace_attempts),
                "provider_attempts": trace_attempts,
                "provider_failover_happened": failover_happened,
                "provider_selected_final": final_provider,
                "provider_final_model": final_model,
                "provider_final_ok": final_ok,
                "provider_total_duration_ms": round(total_ms, 2),
            }
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
        max_tokens: int | None = None,
    ) -> ProviderExecutionResult:
        env = environment or RuntimeEnvironment()
        started_total = time.monotonic()
        all_attempts: list[ProviderAttempt] = []
        all_trace_attempts: list[dict[str, Any]] = []
        providers_to_try = self._providers_to_try()

        last_err: BaseException | None = None
        failover_happened = False

        for idx, provider in enumerate(providers_to_try):
            if idx > 0:
                failover_happened = True
            try:
                model, attempts, trace_attempts = await self._execute_on_provider(
                    provider,
                    messages=messages,
                    tools=tools or [],
                    environment=env,
                    cancelled=cancelled,
                    remaining_s=remaining_s,
                    trace=trace,
                    attempt_offset=len(all_attempts),
                    max_tokens=max_tokens,
                )
                all_attempts.extend(attempts)
                all_trace_attempts.extend(trace_attempts)
                total_ms = (time.monotonic() - started_total) * 1000.0
                self._merge_provider_trace(
                    trace,
                    trace_attempts=all_trace_attempts,
                    final_provider=model.provider or str(getattr(provider, "provider_name", "")),
                    final_model=model.model,
                    final_ok=True,
                    failover_happened=failover_happened,
                    total_ms=total_ms,
                )
                return ProviderExecutionResult(
                    ok=True,
                    content=model.content,
                    provider=model.provider,
                    model=model.model,
                    finish_reason=model.finish_reason,
                    tool_calls=[tc.model_dump() for tc in model.tool_calls],
                    usage=model.usage.model_dump() if model.usage else {},
                    latency_ms=total_ms,
                    attempts=all_attempts,
                )
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                # Preserve attempts recorded before exception (already in trace via add_provider_attempt)
                if trace is not None:
                    for item in list(getattr(trace, "_data", {}).get("provider_attempts", []) or []):
                        if item not in all_trace_attempts:
                            all_trace_attempts.append(item)
                if idx >= len(providers_to_try) - 1 or not is_failover_eligible(exc):
                    break
                log.info(
                    "provider.failover from=%s to=%s class=%s",
                    getattr(provider, "provider_name", "?"),
                    getattr(providers_to_try[idx + 1], "provider_name", "?"),
                    failure_class_for_error(exc),
                )
                continue

        total_ms = (time.monotonic() - started_total) * 1000.0
        final_provider = str(getattr(providers_to_try[-1], "provider_name", self.provider_name))
        self._merge_provider_trace(
            trace,
            trace_attempts=all_trace_attempts,
            final_provider=final_provider,
            final_model=self._default_model_for(providers_to_try[-1]),
            final_ok=False,
            failover_happened=failover_happened,
            total_ms=total_ms,
        )

        if isinstance(last_err, ProviderError):
            code, retryable, err_cls = classify_provider_error(last_err)
            raise err_cls(
                last_err.message or "Błąd providera.",
                code=code,
                retryable=retryable,
                internal_detail=str(last_err)[:800],
                cause=last_err,
            ) from last_err

        code, retryable, err_cls = classify_provider_error(last_err or Exception("unknown"))
        raise err_cls(
            "Provider chwilowo niedostępny." if retryable else "Błąd providera.",
            code=code,
            retryable=retryable,
            internal_detail=str(last_err)[:800] if last_err else "",
            cause=last_err,
        )
