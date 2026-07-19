#!/usr/bin/env python3

"""Chat runtime facade — thin compatibility layer over the turn pipeline.

Canonical execution path:
  HTTP → ChatTurnApplicationService → TurnOps.run_turn_core → completion/write-backs

``ChatRuntime`` subclasses ``TurnOps`` so monkeypatches on the instance (tests)
still affect the live pipeline object.
"""

from __future__ import annotations

import logging
from typing import Any

from aihub.chat_contracts import ChatTurnInput, ChatTurnResult
from aihub.db import append_event
from aihub.executive_controller import (
    build_agent_cycle_response,
    get_executive_controller,
)
from aihub.response_variants_engine import ResponseVariantsEngine
from aihub.turn.application import ChatTurnApplicationService
from aihub.turn.ops import (
    WEB_REQUIRED_QUERY_KEYWORDS,
    TurnOps,
    get_cached_chat_traces,
    get_last_traces as _ops_get_last_traces,
)
from aihub.turn.ops import _TRACE_CACHE  # noqa: F401
from aihub.turn.provider_service import ProviderExecutionService

logger = logging.getLogger(__name__)


def get_default_provider():
    """Call-time provider resolve.

    Tests may monkeypatch this module attribute OR
    ``aihub.llm.provider_registry.get_default_provider``; both bind.
    """
    from aihub.llm import provider_registry

    return provider_registry.get_default_provider()


__all__ = [
    "ChatRuntime",
    "get_chat_runtime",
    "get_last_traces",
    "get_cached_chat_traces",
    "get_default_provider",
    "get_executive_controller",
    "build_agent_cycle_response",
    "append_event",
    "ResponseVariantsEngine",
    "WEB_REQUIRED_QUERY_KEYWORDS",
    "_TRACE_CACHE",
    "TurnOps",
]


def _resolve_default_provider():
    """Resolve via module-local ``get_default_provider`` (honors monkeypatches)."""
    return get_default_provider()


class ChatRuntime(TurnOps):
    """Facade + TurnOps: application service owns idempotency/locks; ops owns stages."""

    def __init__(self) -> None:
        super().__init__()
        # Rebind after TurnOps: honor registry patches (primary + reserve failover).
        try:
            from aihub.llm import provider_registry

            primary = _resolve_default_provider()
            self._provider_service = provider_registry.build_provider_execution_service(primary=primary)
        except Exception:
            logger.debug("provider refresh skipped", exc_info=True)
        self._app = ChatTurnApplicationService(self)

    async def run_turn(self, turn: ChatTurnInput) -> ChatTurnResult:
        res: ChatTurnResult | None = None
        err: BaseException | None = None
        try:
            res = await self._app.execute(turn)
            self._apply_persona_guard(turn, res)
            return res
        except BaseException as exc:
            err = exc
            raise
        finally:
            mode = str(getattr(turn, "runtime_mode", "") or "").lower()
            if mode != "audit":
                try:
                    from aihub.chat_session_transcript import persist_chat_turn_messages

                    persist_chat_turn_messages(turn, res, err)
                except Exception:
                    logger.exception("chat session transcript persist failed")


_RUNTIME: ChatRuntime | None = None


def get_chat_runtime() -> ChatRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = ChatRuntime()
    else:
        try:
            from aihub.llm import provider_registry

            fresh = _resolve_default_provider()
            if fresh is not None:
                _RUNTIME._provider = fresh
                _RUNTIME._provider_service = provider_registry.build_provider_execution_service(primary=fresh)
        except Exception:
            logger.debug("provider hot-swap skipped", exc_info=True)
    return _RUNTIME


def get_last_traces(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    return _ops_get_last_traces(user_id, limit=limit)
