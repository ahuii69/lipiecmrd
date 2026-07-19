"""Functional checks: shared ChatRuntime singleton + per-task active turn isolation."""

from __future__ import annotations

import asyncio

import pytest


def test_get_turn_ops_is_get_chat_runtime_singleton():
    from aihub.chat_runtime import get_chat_runtime
    from aihub.turn.ops import get_turn_ops

    a = get_chat_runtime()
    b = get_turn_ops()
    c = get_chat_runtime()
    assert a is b is c
    assert type(a).__name__ == "ChatRuntime"


@pytest.mark.asyncio
async def test_active_turn_ctx_isolated_across_concurrent_tasks():
    """Two concurrent tasks on the same singleton must not overwrite each other's turn_id."""
    from aihub.chat_runtime import get_chat_runtime
    from aihub.turn.models import (
        PrincipalIdentity,
        RuntimeEnvironment,
        TurnContext,
        new_turn_id,
    )

    rt = get_chat_runtime()
    seen: dict[str, str] = {}

    async def worker(label: str) -> None:
        tid = new_turn_id()
        ctx = TurnContext(
            turn_id=tid,
            request_id=f"req-{label}",
            correlation_id=f"cor-{label}",
            user_id=f"u-{label}",
            session_id=f"s-{label}",
            message="hi",
            history=[],
            attachments=[],
            mode="chat",
            include_debug=False,
            principal=PrincipalIdentity(user_id=f"u-{label}"),
            environment=RuntimeEnvironment.from_explicit_mode("standard"),
            idempotency_key=f"idem-{label}",
        )
        rt._active_turn_ctx = ctx
        await asyncio.sleep(0.05)
        active = rt._active_turn_ctx
        assert active is not None
        seen[label] = str(active.turn_id)
        assert active.turn_id == tid
        rt._active_turn_ctx = None

    await asyncio.gather(worker("a"), worker("b"))
    assert seen["a"] != seen["b"]
    assert rt._active_turn_ctx is None
