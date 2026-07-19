"""Per-async-task binding for the active turn on the shared ChatRuntime singleton.

HTTP uses one ``ChatRuntime`` for all sessions. Concurrent turns for different
sessions run as separate asyncio tasks; instance attributes would race.
``ContextVar`` keeps ``turn_id`` / trace builder isolated per task.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_active_turn_ctx_var: ContextVar[Any | None] = ContextVar(
    "aihub_active_turn_ctx", default=None
)
_active_trace_builder_var: ContextVar[Any | None] = ContextVar(
    "aihub_active_trace_builder", default=None
)


def get_active_turn_ctx() -> Any | None:
    return _active_turn_ctx_var.get()


def set_active_turn_ctx(value: Any | None) -> None:
    _active_turn_ctx_var.set(value)


def get_active_trace_builder() -> Any | None:
    return _active_trace_builder_var.get()


def set_active_trace_builder(value: Any | None) -> None:
    _active_trace_builder_var.set(value)
