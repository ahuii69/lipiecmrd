"""Tests for capability registry, policy filtering and tool routing."""

from __future__ import annotations

import pytest

from aihub.chat_contracts import ToolCallRequest
from aihub.tools.registry import get_tool_registry
from aihub.tools.router import ToolRouter
from aihub.tools.types import ToolExecutionContext


def test_registry_filters_by_mode_and_debug():
    registry = get_tool_registry()

    readonly_caps = registry.list_capabilities(
        mode="readonly",
        include_debug=False,
        policy_overrides={},
    )
    names = {c.name for c in readonly_caps}

    assert "memory.search" in names
    assert "memory.add_fact" not in names
    assert "fs.write_file" not in names
    assert "debug.last_events" not in names


@pytest.mark.anyio
async def test_tool_router_dispatch_and_validation():
    registry = get_tool_registry()
    router = ToolRouter(registry)

    ctx = ToolExecutionContext(
        user_id="tool_router_user",
        session_id="s1",
        mode="chat",
        include_debug=False,
    )

    bad = await router.execute(
        ToolCallRequest(tool_call_id="1", name="memory.search", arguments={}),
        ctx,
    )
    assert bad.ok is False
    assert "input_validation_error" in (bad.error or "")

    good = await router.execute(
        ToolCallRequest(
            tool_call_id="2",
            name="psyche.analyze_sentiment",
            arguments={"text": "bardzo dobrze"},
        ),
        ctx,
    )
    assert good.ok is True
    assert "sentiment" in good.output["result"]


@pytest.mark.anyio
async def test_policy_blocks_sensitive_and_readonly_mutations():
    registry = get_tool_registry()
    router = ToolRouter(registry)

    chat_ctx = ToolExecutionContext(
        user_id="policy_chat_user",
        session_id="s2",
        mode="chat",
        include_debug=False,
        policy_overrides={},
    )

    blocked_fs = await router.execute(
        ToolCallRequest(
            tool_call_id="3",
            name="fs.write_file",
            arguments={"path": "a.txt", "content": "x", "overwrite": True},
        ),
        chat_ctx,
    )
    assert blocked_fs.ok is False
    assert "policy_blocked" in (blocked_fs.error or "")

    readonly_ctx = ToolExecutionContext(
        user_id="policy_ro_user",
        session_id="s3",
        mode="readonly",
        include_debug=False,
    )
    blocked_mutation = await router.execute(
        ToolCallRequest(
            tool_call_id="4",
            name="memory.add_fact",
            arguments={"fact": "x", "tags": [], "meta": {}},
        ),
        readonly_ctx,
    )
    assert blocked_mutation.ok is False
    assert "policy_blocked" in (blocked_mutation.error or "")
