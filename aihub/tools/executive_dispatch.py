"""Bridge Executive / AgentExecutor onto Tool Registry + ToolRouter.

Keeps action surface names aligned with chat tools (``web.fetch_url``,
``fs.write_file``, ``memory.search``, …) instead of a parallel legacy set.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from aihub.chat_contracts import ToolCallRequest
from aihub.tools.registry import get_tool_registry
from aihub.tools.router import ToolRouter, _normalize_tool_name
from aihub.tools.types import ToolExecutionContext

logger = logging.getLogger(__name__)

# Task / action aliases → canonical registry tool names.
EXECUTIVE_TOOL_ALIASES: dict[str, str] = {
    "web.fetch": "web.fetch_url",
    "web_fetch": "web.fetch_url",
    "web.fetch_url": "web.fetch_url",
    "web.ingest": "web.ingest_url",
    "web_ingest": "web.ingest_url",
    "web.ingest_url": "web.ingest_url",
    "fs.write": "fs.write_file",
    "fs_write": "fs.write_file",
    "fs.write_file": "fs.write_file",
    "fs.read": "fs.read_file",
    "fs_read": "fs.read_file",
    "fs.read_file": "fs.read_file",
    "system.snapshot": "snapshot.create",
    "snapshot": "snapshot.create",
    "system_snapshot": "snapshot.create",
    "snapshot.create": "snapshot.create",
    "memory.search": "memory.search",
    "memory.add_fact": "memory.add_fact",
    "memory.add_episode": "memory.add_episode",
    "image.generate": "image.generate",
    "research.query": "research.query",
    "research.url": "research.url",
}


def resolve_executive_tool_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return raw
    mapped = EXECUTIVE_TOOL_ALIASES.get(raw) or EXECUTIVE_TOOL_ALIASES.get(
        raw.replace("-", "_")
    )
    if mapped:
        return mapped
    return _normalize_tool_name(raw)


async def dispatch_executive_tool(
    *,
    user_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    session_id: str = "executive",
    mode: str = "agent",
    confirmed: bool = False,
    include_debug: bool = False,
) -> dict[str, Any]:
    """Execute one registry tool via ToolRouter (policies + schemas)."""
    canonical = resolve_executive_tool_name(tool_name)
    if not canonical:
        return {"ok": False, "error": "empty tool name", "tool": tool_name}

    args = dict(arguments or {})
    if confirmed:
        args["_confirmed"] = True

    registry = get_tool_registry()
    router = ToolRouter(registry)
    call = ToolCallRequest(
        tool_call_id=f"exec_{int(time.time() * 1000)}",
        name=canonical,
        arguments=args,
    )
    ctx = ToolExecutionContext(
        user_id=user_id,
        session_id=session_id,
        mode=mode,  # type: ignore[arg-type]
        include_debug=include_debug,
        policy_overrides={},
    )
    result = await router.execute(call, ctx)
    if not result.ok:
        err = result.error or "tool_failed"
        out: dict[str, Any] = {
            "ok": False,
            "action": "tool",
            "tool": canonical,
            "error": err,
            "latency_ms": result.latency_ms,
        }
        # Align ToolRouter policy blocks with MutationPolicy cockpit shape.
        from aihub.tools.mutation_guard import tool_requires_mutation_confirmation

        if "confirmation" in err.lower() and tool_requires_mutation_confirmation(
            canonical
        ):
            out["requires_confirmation"] = True
            out["message"] = (
                "Ta operacja wymaga jawnego potwierdzenia użytkownika "
                "(zapis pliku / snapshot / usunięcie). "
                "force_agent_execute nie pomija tej polityki."
            )
        return out
    payload = result.output if isinstance(result.output, dict) else {"value": result.output}
    # Unwrap ToolEnvelopeOut when present.
    inner = payload.get("result") if "result" in payload else payload
    return {
        "ok": True,
        "action": "tool",
        "tool": canonical,
        "result": inner if inner is not None else payload,
        "latency_ms": result.latency_ms,
    }
