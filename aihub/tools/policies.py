#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Centralized tool access policy engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from aihub.config import CHAT_DEBUG_TOOLS_ENABLED
from aihub.tools.types import ToolDefinition, ToolMode


@dataclass
class ToolPolicyDecision:
    allowed: bool
    reason: str


def can_view_tool(
    tool: ToolDefinition,
    *,
    mode: ToolMode,
    include_debug: bool,
    policy_overrides: Dict[str, Any],
) -> ToolPolicyDecision:
    if not tool.enabled:
        return ToolPolicyDecision(False, "tool disabled")

    if mode not in tool.visibility:
        return ToolPolicyDecision(False, f"tool not visible for mode={mode}")

    debug_enabled = bool(include_debug or CHAT_DEBUG_TOOLS_ENABLED)
    if tool.capability_group == "debug" and not debug_enabled:
        return ToolPolicyDecision(False, "debug tools disabled")

    if mode == "readonly" and not tool.read_only:
        return ToolPolicyDecision(False, "readonly mode blocks mutating tools")

    if (
        mode == "chat"
        and tool.capability_group in {"fs", "system"}
        and not tool.read_only
    ):
        allow_sensitive = bool(policy_overrides.get("allow_sensitive_mutations", False))
        if not allow_sensitive:
            return ToolPolicyDecision(False, "sensitive mutations blocked in chat mode")

    return ToolPolicyDecision(True, "allowed")


def can_call_tool(
    tool: ToolDefinition,
    *,
    mode: ToolMode,
    include_debug: bool,
    policy_overrides: Dict[str, Any],
    confirmed: bool,
) -> ToolPolicyDecision:
    view = can_view_tool(
        tool,
        mode=mode,
        include_debug=include_debug,
        policy_overrides=policy_overrides,
    )
    if not view.allowed:
        return view

    if tool.requires_confirmation and not confirmed:
        return ToolPolicyDecision(False, "tool requires confirmation")

    return ToolPolicyDecision(True, "allowed")
