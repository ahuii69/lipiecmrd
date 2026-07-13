"""Typed runtime errors for the chat turn pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


ErrorCategory = Literal[
    "validation",
    "conflict",
    "timeout",
    "provider",
    "tool",
    "memory",
    "psyche",
    "agent",
    "web",
    "completion",
    "internal",
    "cancelled",
]


@dataclass
class RuntimeErrorInfo:
    code: str
    category: ErrorCategory
    retryable: bool
    user_safe_message: str
    internal_detail: str = ""
    turn_id: str = ""
    cause: Optional[BaseException] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "retryable": self.retryable,
            "user_safe_message": self.user_safe_message,
            "internal_detail": (self.internal_detail or "")[:500],
            "turn_id": self.turn_id,
            "extra": self.extra,
        }


class TurnRuntimeError(Exception):
    """Base error for turn pipeline."""

    def __init__(
        self,
        *,
        code: str,
        category: ErrorCategory,
        retryable: bool,
        user_safe_message: str,
        internal_detail: str = "",
        turn_id: str = "",
        cause: BaseException | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.info = RuntimeErrorInfo(
            code=code,
            category=category,
            retryable=retryable,
            user_safe_message=user_safe_message,
            internal_detail=internal_detail or str(cause or ""),
            turn_id=turn_id,
            cause=cause,
            extra=dict(extra or {}),
        )
        super().__init__(user_safe_message)

    @property
    def retryable(self) -> bool:
        return self.info.retryable


class TurnValidationError(TurnRuntimeError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(
            code="turn_validation",
            category="validation",
            retryable=False,
            user_safe_message=message,
            **kwargs,
        )


class TurnConflictError(TurnRuntimeError):
    def __init__(self, message: str = "Konflikt tury — ponów za chwilę.", **kwargs: Any) -> None:
        super().__init__(
            code="turn_conflict",
            category="conflict",
            retryable=True,
            user_safe_message=message,
            **kwargs,
        )


class TurnTimeoutError(TurnRuntimeError):
    def __init__(self, message: str = "Tura przekroczyła limit czasu.", **kwargs: Any) -> None:
        super().__init__(
            code="turn_timeout",
            category="timeout",
            retryable=True,
            user_safe_message=message,
            **kwargs,
        )


class TurnCancelledError(TurnRuntimeError):
    def __init__(self, message: str = "Tura anulowana.", **kwargs: Any) -> None:
        super().__init__(
            code="turn_cancelled",
            category="cancelled",
            retryable=False,
            user_safe_message=message,
            **kwargs,
        )


class ProviderExecutionError(TurnRuntimeError):
    def __init__(self, message: str = "Błąd providera.", *, retryable: bool = True, **kwargs: Any) -> None:
        code = kwargs.pop("code", "provider_error")
        super().__init__(
            code=code,
            category="provider",
            retryable=retryable,
            user_safe_message=message,
            **kwargs,
        )


class ProviderTimeoutError(ProviderExecutionError):
    def __init__(self, message: str = "Provider nie odpowiedział na czas.", **kwargs: Any) -> None:
        kwargs.setdefault("code", "provider_timeout")
        kwargs.pop("retryable", None)
        super().__init__(message, retryable=True, **kwargs)


class ProviderRateLimitError(ProviderExecutionError):
    def __init__(self, message: str = "Limit zapytań providera.", **kwargs: Any) -> None:
        kwargs.setdefault("code", "provider_rate_limit")
        kwargs.pop("retryable", None)
        super().__init__(message, retryable=True, **kwargs)


class ToolExecutionError(TurnRuntimeError):
    def __init__(self, message: str = "Błąd narzędzia.", *, retryable: bool = False, **kwargs: Any) -> None:
        super().__init__(
            code=kwargs.pop("code", "tool_error"),
            category="tool",
            retryable=retryable,
            user_safe_message=message,
            **kwargs,
        )


class ToolTimeoutError(ToolExecutionError):
    def __init__(self, message: str = "Narzędzie przekroczyło limit czasu.", **kwargs: Any) -> None:
        super().__init__(message, retryable=True, code="tool_timeout", **kwargs)


class ToolArgumentsError(ToolExecutionError):
    def __init__(self, message: str = "Nieprawidłowe argumenty narzędzia.", **kwargs: Any) -> None:
        super().__init__(message, retryable=False, code="tool_arguments", **kwargs)


class MemoryReadError(TurnRuntimeError):
    def __init__(self, message: str = "Błąd odczytu pamięci.", **kwargs: Any) -> None:
        super().__init__(
            code="memory_read",
            category="memory",
            retryable=True,
            user_safe_message=message,
            **kwargs,
        )


class MemoryWriteError(TurnRuntimeError):
    def __init__(self, message: str = "Błąd zapisu pamięci.", **kwargs: Any) -> None:
        super().__init__(
            code="memory_write",
            category="memory",
            retryable=True,
            user_safe_message=message,
            **kwargs,
        )


class PsycheReadError(TurnRuntimeError):
    def __init__(self, message: str = "Błąd odczytu psyche.", **kwargs: Any) -> None:
        super().__init__(
            code="psyche_read",
            category="psyche",
            retryable=True,
            user_safe_message=message,
            **kwargs,
        )


class PsycheWriteError(TurnRuntimeError):
    def __init__(self, message: str = "Błąd zapisu psyche.", **kwargs: Any) -> None:
        super().__init__(
            code="psyche_write",
            category="psyche",
            retryable=True,
            user_safe_message=message,
            **kwargs,
        )


class AgentHandoffError(TurnRuntimeError):
    def __init__(self, message: str = "Błąd handoff agenta.", **kwargs: Any) -> None:
        super().__init__(
            code="agent_handoff",
            category="agent",
            retryable=False,
            user_safe_message=message,
            **kwargs,
        )


class WebGroundingError(TurnRuntimeError):
    def __init__(self, message: str = "Błąd web grounding.", **kwargs: Any) -> None:
        super().__init__(
            code="web_grounding",
            category="web",
            retryable=True,
            user_safe_message=message,
            **kwargs,
        )


class CompletionWriteBackError(TurnRuntimeError):
    def __init__(self, message: str = "Błąd write-back tury.", **kwargs: Any) -> None:
        super().__init__(
            code="completion_write_back",
            category="completion",
            retryable=True,
            user_safe_message=message,
            **kwargs,
        )


class RuntimeInternalError(TurnRuntimeError):
    def __init__(self, message: str = "Wewnętrzny błąd runtime.", **kwargs: Any) -> None:
        super().__init__(
            code="runtime_internal",
            category="internal",
            retryable=False,
            user_safe_message=message,
            **kwargs,
        )
