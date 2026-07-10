#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Validated tool execution router with policy + schema guards."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from aihub.chat_contracts import ToolCallRequest, ToolCallResult
from aihub.tools.policies import can_call_tool
from aihub.tools.types import ToolDefinition, ToolExecutionContext

logger = logging.getLogger(__name__)


def _normalize_tool_name(name: str) -> str:
    """Normalize tool names to canonical format.

    Maps aliases to their canonical names:
    - 'debug_info' -> 'system.debug_info'
    - 'health' -> 'system.health'
    - 'status' -> 'runtime.status'
    - 'last_events' -> 'debug.last_events'
    - 'fetch_url' / 'web_fetch' / 'web.fetch' -> 'web.fetch_url' (LLM drift)
    - 'research_url' -> 'research.url'
    - 'query' -> 'research.query'; 'url' -> 'web.fetch_url' (short LLM names)

    Idempotent: already-canonical names pass through unchanged.
    """
    s = str(name or "").strip()
    if not s:
        return s

    # Dotted names that are *not* canonical (LLM / legacy drift)
    if s == "web.fetch":
        return "web.fetch_url"
    if s == "web.ingest":
        return "web.ingest_url"

    if "." in s:
        return s

    alias_map = {
        # system / debug
        "debug_info": "system.debug_info",
        "health": "system.health",
        "last_events": "debug.last_events",
        # runtime
        "status": "runtime.status",
        "get_capabilities": "runtime.get_capabilities",
        "trace_last_cycle": "runtime.trace_last_cycle",
        # psyche
        "reflect": "psyche.reflect",
        "analyze_sentiment": "psyche.analyze_sentiment",
        "evolve_state": "psyche.evolve_state",
        # memory
        "search": "memory.search",
        "get_context": "memory.get_context",
        "add_fact": "memory.add_fact",
        "add_episode": "memory.add_episode",
        "process_turn": "memory.process_turn",
        # planner / reasoning
        "preview": "planner.preview",
        "build_task_graph": "planner.build_task_graph",
        "run_preview": "reasoning.run_preview",
        # agent
        "run_cycle": "agent.run_cycle",
        # fs
        "read_file": "fs.read_file",
        "write_file": "fs.write_file",
        # snapshot
        "create_snapshot": "snapshot.create",
        # web / research: canonical dotted names; bare names are common LLM mistakes
        "fetch_url": "web.fetch_url",
        "web_fetch": "web.fetch_url",
        "web_fetch_url": "web.fetch_url",
        "ingest_url": "web.ingest_url",
        "web_ingest": "web.ingest_url",
        "web_ingest_url": "web.ingest_url",
        "research_url": "research.url",
        # LLM drift: short names (OpenAI-style / generic)
        "query": "research.query",
        "url": "web.fetch_url",
        # image generation (LLM drift)
        "image_generate": "image.generate",
        "generate_image": "image.generate",
        "image_gen": "image.generate",
    }
    return alias_map.get(s, s)


class ToolRegistryProtocol(Protocol):
    def get(self, name: str) -> Any: ...


class ToolRouter:
    """Routes model-emitted tool calls to registered adapters."""

    def __init__(self, registry: ToolRegistryProtocol) -> None:
        self._registry = registry

    async def execute(
        self,
        call: ToolCallRequest,
        ctx: ToolExecutionContext,
    ) -> ToolCallResult:
        started = time.monotonic()

        normalized_name = _normalize_tool_name(call.name)
        try:
            tool = self._registry.get(normalized_name)
        except KeyError as exc:
            return ToolCallResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                ok=False,
                error=str(exc),
                latency_ms=(time.monotonic() - started) * 1000.0,
            )

        assert isinstance(tool, ToolDefinition)

        confirmed = bool(call.arguments.get("_confirmed", False))
        decision = can_call_tool(
            tool,
            mode=ctx.mode,
            include_debug=ctx.include_debug,
            policy_overrides=dict(ctx.policy_overrides or {}),
            confirmed=confirmed,
        )
        if not decision.allowed:
            return ToolCallResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                ok=False,
                error=f"policy_blocked: {decision.reason}",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )

        try:
            model_in = tool.input_model.model_validate(call.arguments)
        except ValidationError as exc:
            return ToolCallResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                ok=False,
                error=f"input_validation_error: {exc}",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )

        try:
            result_obj = await asyncio.wait_for(
                tool.handler(ctx, model_in),
                timeout=float(tool.timeout_seconds),
            )
            validated = tool.output_model.model_validate(result_obj)
            payload = validated.model_dump()
            return ToolCallResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                ok=True,
                output=payload,
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
        except asyncio.TimeoutError:
            return ToolCallResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                ok=False,
                error="tool_timeout",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
        except ValidationError as exc:
            return ToolCallResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                ok=False,
                error=f"output_validation_error: {exc}",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
        except httpx.HTTPError as exc:
            # web.fetch_url / research.url: 403/404/timeouts — avoid traceback noise.
            logger.warning("tool HTTP error (%s): %s", call.name, exc)
            return ToolCallResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                ok=False,
                error=f"tool_error: {exc}",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
        except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
            logger.error("tool execution failed: %s", exc, exc_info=True)
            return ToolCallResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                ok=False,
                error=f"tool_error: {exc}",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
