"""Per-request SSE stream session (ContextVar) for live /chat/turn UX.

Set by :mod:`aihub.chat_api` for ``stream=true``; read by :mod:`aihub.chat_runtime`
and providers. No-ops when session is unset (normal JSON /chat/turn).
"""

from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass
from typing import Any, Final, Optional

STREAM_END: Final[object] = object()


@dataclass
class ChatStreamSession:
    queue: asyncio.Queue
    text_accum: str = ""
    from_provider: bool = False

    def append_provider_delta(self, piece: str) -> None:
        if not piece:
            return
        self.text_accum += piece
        self.from_provider = True


CHAT_STREAM_SESSION: contextvars.ContextVar[Optional[ChatStreamSession]] = (
    contextvars.ContextVar("chat_stream_session", default=None)
)


def stream_session_active() -> bool:
    return CHAT_STREAM_SESSION.get() is not None


def current_stream_session() -> Optional[ChatStreamSession]:
    return CHAT_STREAM_SESSION.get()


async def chat_stream_emit(event: dict[str, Any]) -> None:
    sess = CHAT_STREAM_SESSION.get()
    if sess is None:
        return
    await sess.queue.put(dict(event))


async def emit_status(stage: str, *, label_pl: str) -> None:
    await chat_stream_emit(
        {"type": "status", "stage": stage, "label_pl": label_pl},
    )


async def emit_tool_event(name: str, status: str) -> None:
    await chat_stream_emit({"type": "tool", "name": name, "status": status})


async def emit_memory_used(*, count: int) -> None:
    await chat_stream_emit(
        {"type": "memory", "used": True, "count": max(0, int(count))},
    )
