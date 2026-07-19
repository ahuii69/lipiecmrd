"""Provider failover classification and bounded retry policy."""

from __future__ import annotations

import re
from typing import Any

from aihub.llm.provider_types import ProviderError

_RETRY_AFTER_IN_MSG = re.compile(
    r"(?i)try again in\s+([0-9]+(?:\.[0-9]+)?)\s*s",
)


def failure_class_for_error(exc: BaseException) -> str:
    if isinstance(exc, ProviderError):
        code = str(exc.code or "").lower()
        status = int(exc.status_code or 0)
        if status == 402 or "balance" in code or "payment" in code:
            return "insufficient_balance"
        if status == 401 or "auth" in code or "api_key" in code:
            return "auth"
        if status == 403:
            return "forbidden"
        if status == 408 or "timeout" in code:
            return "timeout"
        if status == 429 or "rate" in code:
            return "rate_limit"
        if status >= 500:
            return "server_error"
        if code in {"invalid_json", "invalid_response", "malformed"}:
            return "malformed_response"
        if "transport" in code or "connection" in code:
            return "transport"
        return code or "provider_error"
    # TurnRuntimeError / ProviderExecutionError carry .info.code
    info = getattr(exc, "info", None)
    info_code = str(getattr(info, "code", "") or "").strip()
    if info_code:
        return info_code
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "connection" in msg or "network" in msg:
        return "transport"
    return "provider_error"


def is_failover_eligible(exc: BaseException) -> bool:
    """True when reserve provider should be attempted."""
    if isinstance(exc, ProviderError):
        status = int(exc.status_code or 0)
        code = str(exc.code or "").lower()
        if status in {401, 402, 403, 408, 429} or status >= 500:
            return True
        if code in {
            "invalid_json",
            "invalid_response",
            "malformed",
            "timeout",
            "transport",
            "missing_api_key",
            "provider_non_text_content",
        }:
            return True
        if not exc.retryable and status in {400, 404, 422}:
            return False
        if "empty" in code or code == "empty_response":
            return True
        return bool(exc.retryable) or status == 0
    info = getattr(exc, "info", None)
    if info is not None:
        code = str(getattr(info, "code", "") or "")
        if code == "provider_non_text_content":
            return True
        if bool(getattr(info, "retryable", False)) and str(
            getattr(info, "category", "") or ""
        ) == "provider":
            return True
    name = type(exc).__name__.lower()
    if "timeout" in name or isinstance(exc, TimeoutError):
        return True
    msg = str(exc).lower()
    if any(t in msg for t in ("connection", "network", "dns", "unavailable")):
        return True
    return False


def max_retries_before_failover(exc: BaseException) -> int:
    """Retries on the same provider before switching to reserve."""
    if isinstance(exc, ProviderError):
        status = int(exc.status_code or 0)
        if status in {401, 402, 403}:
            return 0
        if status == 429:
            return 1
        if status in {408} or status >= 500:
            return 1
        code = str(exc.code or "").lower()
        if code in {"timeout", "transport"}:
            return 1
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return 1
    return 0


def parse_retry_after(exc: BaseException) -> float | None:
    msg = str(exc)
    m = _RETRY_AFTER_IN_MSG.search(msg)
    if m:
        try:
            return max(0.5, float(m.group(1)) + 0.5)
        except (TypeError, ValueError):
            return None
    details = getattr(exc, "details", None) or {}
    if isinstance(details, dict):
        headers = details.get("headers") or details.get("response_headers")
        if isinstance(headers, dict):
            raw = headers.get("Retry-After") or headers.get("retry-after")
            if raw is not None:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return None
    headers = getattr(exc, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        raw = headers.get("Retry-After") or headers.get("retry-after")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
    return None


def empty_model_response(response: Any) -> bool:
    content = str(getattr(response, "content", "") or "").strip()
    tool_calls = list(getattr(response, "tool_calls", []) or [])
    return not content and not tool_calls
