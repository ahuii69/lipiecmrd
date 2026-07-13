"""Central TraceBuilder + secret redaction for turn pipeline."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

TRACE_SCHEMA_VERSION = "turn-trace-v1"

_SECRET_KEY_RE = re.compile(
    r"(authorization|cookie|api[_-]?key|bearer|proxy[_-]?token|password|"
    r"secret|session[_-]?token|private[_-]?key|vault|csrf)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+[a-z0-9\-._~+/]+=*|sk-[a-z0-9]{16,}|ghp_[a-z0-9]{20,}|"
    r"eyJ[a-z0-9_\-]{20,}\.[a-z0-9_\-]{10,})"
)


def _redact_string(value: str) -> str:
    if _SECRET_VALUE_RE.search(value):
        return "[REDACTED]"
    if len(value) > 4000:
        return value[:4000] + "…[truncated]"
    return value


def redact(obj: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "[MAX_DEPTH]"
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if _SECRET_KEY_RE.search(str(k)):
                out[str(k)] = "[REDACTED]"
            else:
                out[str(k)] = redact(v, depth=depth + 1)
        return out
    if isinstance(obj, list):
        return [redact(x, depth=depth + 1) for x in obj[:200]]
    if isinstance(obj, str):
        return _redact_string(obj)
    return obj


def tool_result_summary(output: Any, *, max_preview: int = 240) -> dict[str, Any]:
    try:
        raw = json.dumps(output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = str(output)
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
    preview = redact(_redact_string(raw[:max_preview]))
    return {
        "bytes": len(raw.encode("utf-8", errors="replace")),
        "sha256": digest,
        "preview": preview,
        "content_type": "application/json",
    }


class TraceBuilder:
    def __init__(
        self,
        *,
        turn_id: str,
        request_id: str = "",
        correlation_id: str = "",
    ) -> None:
        self._started = time.monotonic()
        self._data: dict[str, Any] = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "turn_id": turn_id,
            "request_id": request_id,
            "correlation_id": correlation_id or request_id,
            "provider_attempts": [],
            "tool_attempts": [],
            "memory_sources": [],
            "psyche_snapshot_meta": {},
            "decision": {},
            "outcome": None,
            "errors": [],
            "fallback": False,
            "write_back_status": {},
            "duration_ms": 0.0,
        }

    def set_decision(self, decision: dict[str, Any]) -> None:
        self._data["decision"] = redact(decision)

    def add_provider_attempt(self, attempt: dict[str, Any]) -> None:
        self._data["provider_attempts"].append(redact(attempt))

    def add_tool_attempt(self, attempt: dict[str, Any]) -> None:
        # Never store full tool payloads — summary only.
        safe = dict(attempt)
        if "output" in safe:
            safe["output_summary"] = tool_result_summary(safe.pop("output"))
        if "arguments" in safe:
            safe["arguments"] = redact(safe["arguments"])
        self._data["tool_attempts"].append(redact(safe))

    def set_memory_sources(self, sources: list[Any]) -> None:
        self._data["memory_sources"] = redact(sources)[:50]

    def set_psyche_meta(self, meta: dict[str, Any]) -> None:
        self._data["psyche_snapshot_meta"] = redact(meta)

    def add_error(self, err: dict[str, Any]) -> None:
        self._data["errors"].append(redact(err))

    def set_fallback(self, used: bool) -> None:
        self._data["fallback"] = bool(used)

    def set_write_back(self, effect_type: str, status: dict[str, Any]) -> None:
        self._data["write_back_status"][effect_type] = redact(status)

    def set_outcome(self, outcome: str) -> None:
        self._data["outcome"] = outcome

    def merge(self, extra: dict[str, Any]) -> None:
        for k, v in extra.items():
            if k in {"provider_attempts", "tool_attempts", "errors"}:
                continue
            self._data[k] = redact(v)

    def build(self) -> dict[str, Any]:
        self._data["duration_ms"] = round((time.monotonic() - self._started) * 1000.0, 2)
        return redact(dict(self._data))
