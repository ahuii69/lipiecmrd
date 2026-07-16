#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
COMPREHENSIVE TOOL REGISTRY AUDIT AND CONSISTENCY TESTS

Purpose:
- Verify ALL tools are correctly exposed in each mode
- Verify aliases normalize correctly
- Verify policy filtering works
- Verify no tool not found errors
- Verify trace fields present with correct values
"""

from __future__ import annotations

import httpx
import pytest

from aihub.chat_contracts import ToolCallRequest
from aihub.tools.policies import can_view_tool
from aihub.tools.registry import get_tool_registry
from aihub.tools.router import ToolRouter, _normalize_tool_name
from aihub.tools.types import ToolExecutionContext


class TestToolRegistryCompleteness:
    """Verify all tools are registered with correct metadata."""

    def test_all_tools_have_required_fields(self) -> None:
        """Every tool must have name, visibility, read_only, enabled."""
        registry = get_tool_registry()

        # Access internal tools dict for audit
        for name, tool in registry._tools.items():
            assert tool.name, f"Tool {name} has no name"
            assert tool.visibility, f"Tool {name} has no visibility"
            assert isinstance(tool.read_only, bool), f"Tool {name}.read_only not bool"
            assert isinstance(tool.enabled, bool), f"Tool {name}.enabled not bool"
            assert tool.handler is not None, f"Tool {name} has no handler"

    def test_no_duplicate_tool_names(self) -> None:
        """Each tool name should be unique."""
        registry = get_tool_registry()
        names = list(registry._tools.keys())
        assert len(names) == len(set(names)), "Duplicate tool names found"

    def test_tools_grouped_by_namespace(self) -> None:
        """Tools should follow <group>.<name> pattern with few exceptions."""
        registry = get_tool_registry()
        expected_groups = {
            "memory",
            "goal",
            "planner",
            "reasoning",
            "agent",
            "research",
            "web",
            "psyche",
            "runtime",
            "fs",
            "snapshot",
            "system",
            "debug",
            "image",
            "knowledge",
            "consistency",
        }
        tool_groups = {name.split(".")[0] for name in registry._tools.keys()}
        assert tool_groups.issubset(expected_groups | {"snapshot"})


class TestToolVisibilityFiltering:
    """Verify tools are correctly exposed by mode."""

    def test_chat_mode_includes_30_tools(self) -> None:
        """Chat mode: baseline 27 + memory.list_procedures + knowledge.lookup + consistency.check."""
        registry = get_tool_registry()
        cap = registry.list_capabilities(mode="chat", include_debug=False)
        names = {c.name for c in cap}

        # Should NOT include debug-only tools
        assert "system.debug_info" not in names, "debug-only tool in chat"
        assert "debug.last_events" not in names, "debug-only tool in chat"

        # Should NOT include mutating system/fs tools without override
        # (fs.write_file and snapshot.create are blocked by policy)
        assert "fs.write_file" not in names, "mutating fs tool without override"
        assert "snapshot.create" not in names, "mutating system tool without override"

        # Should include standard tools
        assert "memory.search" in names
        assert "memory.list_procedures" in names
        assert "knowledge.lookup" in names
        assert "consistency.check" in names
        assert "runtime.status" in names
        assert "system.health" in names

        assert len(names) == 30, f"Chat mode has {len(names)} tools, expected 30"

    def test_debug_mode_includes_35_tools(self) -> None:
        """Debug mode: all chat tools + debug-only + sensitive mutations (+3 new capabilities)."""
        registry = get_tool_registry()
        cap = registry.list_capabilities(mode="debug", include_debug=True)
        names = {c.name for c in cap}

        # Should include debug-only tools
        assert "system.debug_info" in names, "debug tool missing in debug mode"
        assert "debug.last_events" in names, "debug tool missing in debug mode"

        # Should include all mutating tools in debug mode
        assert "fs.write_file" in names, "fs.write_file should be in debug mode"
        assert "snapshot.create" in names, "snapshot.create should be in debug mode"

        assert len(names) == 35, f"Debug mode has {len(names)} tools, expected 35"

    def test_readonly_mode_respects_readonly_flag(self) -> None:
        """Readonly mode should only include read_only=True tools."""
        registry = get_tool_registry()
        cap = registry.list_capabilities(mode="readonly", include_debug=False)
        names = {c.name for c in cap}

        # Verify all returned tools have read_only=True
        for tool in registry._tools.values():
            if tool.name in names:
                assert tool.read_only, f"Tool {tool.name} is not read_only but exposed"

    def test_agent_mode_includes_all_non_debug(self) -> None:
        """Agent mode should include all non-debug-only tools."""
        registry = get_tool_registry()
        cap = registry.list_capabilities(mode="agent", include_debug=False)
        names = {c.name for c in cap}

        # Should NOT include debug-only
        assert "debug.last_events" not in names
        assert "system.debug_info" not in names

        # Should include agent-accessible tools
        assert "agent.run_cycle" in names
        assert "memory.search" in names


class TestToolAliasNormalization:
    """Verify alias normalization works end-to-end."""

    def test_normalize_debug_info_alias(self) -> None:
        """'debug_info' should normalize to 'system.debug_info'."""
        assert _normalize_tool_name("debug_info") == "system.debug_info"

    def test_normalize_health_alias(self) -> None:
        """'health' should normalize to 'system.health'."""
        assert _normalize_tool_name("health") == "system.health"

    def test_normalize_status_alias(self) -> None:
        """'status' should normalize to 'runtime.status'."""
        assert _normalize_tool_name("status") == "runtime.status"

    def test_normalize_last_events_alias(self) -> None:
        """'last_events' should normalize to 'debug.last_events'."""
        assert _normalize_tool_name("last_events") == "debug.last_events"

    def test_normalize_idempotent_for_canonical(self) -> None:
        """Canonical names should pass through unchanged."""
        canonical = [
            "system.debug_info",
            "system.health",
            "runtime.status",
            "memory.search",
            "research.query",
            "web.fetch_url",
        ]
        for name in canonical:
            assert _normalize_tool_name(name) == name

    def test_normalize_empty_safe(self) -> None:
        """Normalize should handle empty/none safely."""
        assert _normalize_tool_name("") == ""
        assert _normalize_tool_name(None) == ""

    def test_normalize_unknown_passthrough(self) -> None:
        """Unknown single-word names should pass through (no auto-prefix)."""
        # If someone sends "unknown_tool", should NOT become "something.unknown_tool"
        assert _normalize_tool_name("unknown_tool") == "unknown_tool"

    def test_normalize_fetch_url_alias(self) -> None:
        assert _normalize_tool_name("fetch_url") == "web.fetch_url"
        assert _normalize_tool_name("web_fetch") == "web.fetch_url"
        assert _normalize_tool_name("web.fetch") == "web.fetch_url"

    def test_normalize_web_ingest_alias(self) -> None:
        assert _normalize_tool_name("ingest_url") == "web.ingest_url"
        assert _normalize_tool_name("web_ingest") == "web.ingest_url"
        assert _normalize_tool_name("web.ingest") == "web.ingest_url"

    def test_normalize_research_url_alias(self) -> None:
        assert _normalize_tool_name("research_url") == "research.url"

    def test_normalize_query_alias_to_research_query(self) -> None:
        assert _normalize_tool_name("query") == "research.query"

    def test_normalize_url_alias_to_web_fetch_url(self) -> None:
        assert _normalize_tool_name("url") == "web.fetch_url"


class TestToolRouterExecution:
    """Verify router correctly executes tools after normalization."""

    @pytest.mark.anyio
    async def test_router_finds_system_debug_info_by_canonical(self) -> None:
        """Router should find system.debug_info using canonical name."""
        router = ToolRouter(get_tool_registry())
        ctx = ToolExecutionContext(
            user_id="audit_user",
            session_id="session1",
            mode="debug",
            include_debug=True,
        )

        call = ToolCallRequest(
            tool_call_id="call1",
            name="system.debug_info",
            arguments={},
        )

        result = await router.execute(call, ctx)
        # Should find the tool (may be blocked by policy, but not "tool not found")
        assert result.name == "system.debug_info"
        # In debug mode should be allowed
        if result.error:
            assert "tool not found" not in result.error.lower()

    @pytest.mark.anyio
    async def test_router_finds_system_debug_info_by_alias(self) -> None:
        """Router should find system.debug_info using 'debug_info' alias."""
        router = ToolRouter(get_tool_registry())
        ctx = ToolExecutionContext(
            user_id="audit_user",
            session_id="session1",
            mode="debug",
            include_debug=True,
        )

        call = ToolCallRequest(
            tool_call_id="call2",
            name="debug_info",  # Using alias
            arguments={},
        )

        result = await router.execute(call, ctx)
        assert result.name == "debug_info"  # Returns original name from call
        # Normalized name should resolve OK
        if result.error:
            assert "tool not found" not in result.error.lower()

    @pytest.mark.anyio
    async def test_router_finds_runtime_status(self) -> None:
        """Router should find runtime.status using canonical name."""
        router = ToolRouter(get_tool_registry())
        ctx = ToolExecutionContext(
            user_id="audit_user",
            session_id="session1",
            mode="chat",
            include_debug=False,
        )

        call = ToolCallRequest(
            tool_call_id="call3",
            name="runtime.status",
            arguments={},
        )

        result = await router.execute(call, ctx)
        assert result.name == "runtime.status"

    @pytest.mark.anyio
    async def test_router_finds_runtime_status_by_status_alias(self) -> None:
        """Router should find runtime.status using 'status' alias."""
        router = ToolRouter(get_tool_registry())
        ctx = ToolExecutionContext(
            user_id="audit_user",
            session_id="session1",
            mode="debug",
            include_debug=True,
        )

        call = ToolCallRequest(
            tool_call_id="call_status_alias",
            name="status",  # Using alias
            arguments={},
        )

        result = await router.execute(call, ctx)
        assert result.name == "status"  # Returns original name from call
        # Normalized name should resolve OK
        if result.error:
            assert (
                "tool not found" not in result.error.lower()
            ), f"Status alias should normalize to runtime.status, but got: {result.error}"

    @pytest.mark.anyio
    async def test_router_finds_debug_last_events_by_alias(self) -> None:
        """Router should find debug.last_events using 'last_events' alias."""
        router = ToolRouter(get_tool_registry())
        ctx = ToolExecutionContext(
            user_id="audit_user",
            session_id="session1",
            mode="debug",
            include_debug=True,
        )

        call = ToolCallRequest(
            tool_call_id="call_last_events_alias",
            name="last_events",  # Using alias (model may emit this)
            arguments={},
        )

        result = await router.execute(call, ctx)
        assert result.name == "last_events"  # Returns original name from call
        # Normalized name should resolve OK
        if result.error:
            assert (
                "tool not found" not in result.error.lower()
            ), f"last_events alias should normalize to debug.last_events, but got: {result.error}"

    @pytest.mark.anyio
    async def test_router_unknown_tool_gives_tool_not_found(self) -> None:
        """Router should return 'tool not found' for unknown tool."""
        router = ToolRouter(get_tool_registry())
        ctx = ToolExecutionContext(
            user_id="audit_user",
            session_id="session1",
            mode="debug",
            include_debug=True,
        )

        call = ToolCallRequest(
            tool_call_id="call_unknown",
            name="nonexistent_tool",  # Not registered, not aliased
            arguments={},
        )

        result = await router.execute(call, ctx)
        assert result.name == "nonexistent_tool"
        # Should have error with "tool not found"
        assert result.error is not None
        assert "tool not found" in result.error.lower()
        assert result.ok is False

    @pytest.mark.anyio
    async def test_router_resolves_fetch_url_alias_to_web_fetch_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Models often emit bare 'fetch_url'; it must map to web.fetch_url."""

        async def _fake_fetch(_user_id: str, url: str) -> dict:
            return {
                "ok": True,
                "url": url,
                "status": 200,
                "headers": {},
                "bytes": 12,
                "text": "<html>ok</html>",
            }

        monkeypatch.setattr("aihub.web_tools.fetch_url", _fake_fetch)

        router = ToolRouter(get_tool_registry())
        ctx = ToolExecutionContext(
            user_id="audit_fetch_alias",
            session_id="s1",
            mode="chat",
            include_debug=False,
        )
        call = ToolCallRequest(
            tool_call_id="cfetch1",
            name="fetch_url",
            arguments={"url": "https://example.com/"},
        )
        result = await router.execute(call, ctx)
        assert result.ok is True
        assert result.error is None
        assert result.output is not None
        assert result.output.get("ok") is True
        body = result.output.get("result") or {}
        assert "example.com" in str(body.get("url", ""))

    @pytest.mark.anyio
    async def test_router_maps_httpx_error_from_web_fetch_to_tool_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """403/timeout from fetch must not crash the turn — return ok=False."""

        async def _raise_403(_user_id: str, _url: str) -> dict:
            req = httpx.Request("GET", "https://example.com/")
            raise httpx.HTTPStatusError(
                "forbidden",
                request=req,
                response=httpx.Response(403, request=req),
            )

        monkeypatch.setattr("aihub.tools.registry.fetch_url", _raise_403)

        router = ToolRouter(get_tool_registry())
        ctx = ToolExecutionContext(
            user_id="audit_fetch_httpx",
            session_id="s1",
            mode="chat",
            include_debug=False,
        )
        call = ToolCallRequest(
            tool_call_id="cfetch403",
            name="web.fetch_url",
            arguments={"url": "https://example.com/"},
        )
        result = await router.execute(call, ctx)
        assert result.ok is False
        assert result.error is not None
        assert "403" in result.error or "forbidden" in result.error.lower()


class TestPolicyBlockingDebugInChat:
    """Verify policy correctly blocks debug-only tools in chat mode."""

    def test_system_debug_info_blocked_in_chat(self) -> None:
        """system.debug_info should be blocked in chat mode."""
        registry = get_tool_registry()
        tool = registry.get("system.debug_info")

        decision = can_view_tool(
            tool,
            mode="chat",
            include_debug=False,
            policy_overrides={},
        )
        assert not decision.allowed, "Debug tool should be blocked in chat"

    def test_system_debug_info_allowed_in_debug(self) -> None:
        """system.debug_info should be allowed in debug mode."""
        registry = get_tool_registry()
        tool = registry.get("system.debug_info")

        decision = can_view_tool(
            tool,
            mode="debug",
            include_debug=True,
            policy_overrides={},
        )
        assert decision.allowed, "Debug tool should be allowed in debug mode"

    def test_debug_last_events_blocked_in_chat(self) -> None:
        """debug.last_events should be blocked in chat mode."""
        registry = get_tool_registry()
        tool = registry.get("debug.last_events")

        decision = can_view_tool(
            tool,
            mode="chat",
            include_debug=False,
            policy_overrides={},
        )
        assert not decision.allowed, "Debug tool should be blocked in chat"

    def test_memory_search_allowed_in_chat(self) -> None:
        """memory.search should be allowed in chat mode."""
        registry = get_tool_registry()
        tool = registry.get("memory.search")

        decision = can_view_tool(
            tool,
            mode="chat",
            include_debug=False,
            policy_overrides={},
        )
        assert decision.allowed, "Memory search should be allowed in chat"


class TestNoOrphanTools:
    """Verify no tools are orphaned or unreachable."""

    def test_every_tool_findable_via_registry(self) -> None:
        """Every tool in registry should be retrievable by exact name."""
        registry = get_tool_registry()

        # Get all tools via internal dict
        all_tools = registry._tools
        for name, tool in all_tools.items():
            # Should be findable by name
            retrieved = registry.get(name)
            assert retrieved.name == tool.name

    def test_every_tool_in_some_visibility(self) -> None:
        """Every tool must be visible in at least one mode."""
        registry = get_tool_registry()

        for tool in registry._tools.values():
            # Tool must have visibility list with at least one mode
            assert tool.visibility, f"Tool {tool.name} has empty visibility"
            assert any(
                mode in tool.visibility
                for mode in ["chat", "agent", "readonly", "debug"]
            ), f"Tool {tool.name} has unknown visibility modes"


class TestTraceEtap9aFields:
    """Verify trace contains all ETAP 9A fields."""

    @pytest.mark.anyio
    async def test_turn_result_has_all_etap9a_fields(self) -> None:
        """ChatTurnResult trace should have all ETAP 9A fields."""
        from aihub.chat_contracts import ChatTurnInput
        from aihub.chat_runtime import get_chat_runtime

        runtime = get_chat_runtime()
        turn = ChatTurnInput(
            user_id="audit_user",
            session_id="session1",
            message="test message",
            history=[],
            mode="chat",
            include_debug=False,
        )

        result = await runtime.run_turn(turn)
        trace = result.model_dump()["trace"]

        required_fields = [
            "selected_strategy",
            "reason_codes",
            "degraded",
            "memory_lookup_happened",
            "psyche_snapshot_happened",
            "research_was_required",
            "experience_write_back_attempted",
            "experience_write_back_succeeded",
        ]

        for field in required_fields:
            assert field in trace, f"Missing trace field: {field}"

    @pytest.mark.anyio
    async def test_etap9a_fields_have_correct_defaults_in_chat(self) -> None:
        """ETAP 9A fields should have sensible defaults for chat mode."""
        from aihub.chat_contracts import ChatTurnInput
        from aihub.chat_runtime import get_chat_runtime

        runtime = get_chat_runtime()
        turn = ChatTurnInput(
            user_id="audit_user",
            session_id="session1",
            message="test",
            history=[],
            mode="chat",
            include_debug=False,
        )

        result = await runtime.run_turn(turn)
        trace = result.model_dump()["trace"]

        # Chat mode (strategy_selector is now invoked) should have these defaults
        assert trace["selected_strategy"] in ["instant", "chat", None]
        assert isinstance(trace["reason_codes"], list)
        assert trace["degraded"] is False
        assert trace["memory_lookup_happened"] in [True, False]
        assert trace["psyche_snapshot_happened"] in [True, False]
        assert trace["research_was_required"] is False
        assert trace["experience_write_back_attempted"] in [True, False]


class TestEndToEndToolCall:
    """Verify model tool calls can be executed end-to-end."""

    @pytest.mark.anyio
    async def test_tool_call_memory_search_works(self) -> None:
        """Tool call for memory.search should execute without error."""
        from aihub.chat_contracts import ChatTurnInput
        from aihub.chat_runtime import get_chat_runtime

        runtime = get_chat_runtime()
        # Test that runtime can handle tool calls end-to-end
        # (This is a basic smoke test)
        turn = ChatTurnInput(
            user_id="audit_user",
            session_id="session1",
            message="search for recent facts",
            history=[],
            mode="chat",
            include_debug=False,
        )

        result = await runtime.run_turn(turn)
        assert result is not None
        assert isinstance(result.model_dump()["trace"], dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
