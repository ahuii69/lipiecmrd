"""AgentExecutor — dispatches cognitive decisions to actual subsystems."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


class AgentExecutor:
    """Execute actions decided by the CognitiveController."""

    _ACTION_ALIASES = {
        "memory_search": "query",
        "memory_query": "query",
        "execute": "action",
    }

    async def execute(
        self, action_type: str, params: dict, user_id: str
    ) -> Dict[str, Any]:
        normalized_action = self._ACTION_ALIASES.get(action_type, action_type)

        handlers: Dict[str, Callable[[dict, str], Any]] = {
            "query": self._exec_query,
            "learn": self._exec_learn,
            "research": self._exec_research,
            "action": self._exec_action,
            "reason": self._exec_reason,
        }
        handler = handlers.get(normalized_action)

        if handler is None:
            logger.warning("Unknown action_type: %s", action_type)
            return {"ok": False, "error": f"unknown action_type: {action_type}"}

        try:
            return await handler(params or {}, user_id)
        except (
            RuntimeError,
            ValueError,
            TypeError,
            OSError,
            ImportError,
            KeyError,
        ) as e:
            logger.error(
                "AgentExecutor.execute failed: action_type=%s user=%s err=%s",
                normalized_action,
                user_id,
                e,
                exc_info=True,
            )
            return {
                "ok": False,
                "action": normalized_action,
                "error": str(e),
            }

    # ------------------------------------------------------------------
    async def _exec_query(self, params: dict, user_id: str) -> Dict[str, Any]:
        from aihub.memory_engine import retrieve_context

        query = params.get("query", params.get("topic", ""))
        limit = int(params.get("limit", 10))
        ctx = retrieve_context(user_id, query, limit=limit)
        return {
            "ok": True,
            "action": "query",
            "total": ctx.get("total", 0),
            "context": ctx,
        }

    async def _exec_learn(self, params: dict, user_id: str) -> Dict[str, Any]:
        from aihub.memory_core import get_memory_core

        topic = params.get("topic", params.get("fact", params.get("message", "")))
        topic = str(topic or "").strip()
        if not topic:
            return {"ok": False, "action": "learn", "error": "empty learning payload"}

        tags = params.get("tags", ["learned"])
        if not isinstance(tags, list):
            tags = ["learned"]

        node_id = get_memory_core().ingest_fact(
            user_id, topic, tags=list(tags), meta={"source": "agent_executor"}
        )
        return {"ok": True, "action": "learn", "node_id": node_id}

    async def _exec_research(self, params: dict, user_id: str) -> Dict[str, Any]:
        from aihub.research_engine import research

        query = params.get("query", params.get("topic", ""))
        if not str(query or "").strip():
            return {
                "ok": False,
                "action": "research",
                "error": "empty research query",
            }

        result = await research(user_id, query)
        return {"ok": True, "action": "research", **result}

    async def _exec_action(self, params: dict, user_id: str) -> Dict[str, Any]:
        tool = params.get("tool", params.get("action", ""))
        tool_params = params.get("params", {})
        if not isinstance(tool_params, dict):
            tool_params = {}

        # Heuristic fallback from instruction text
        if not tool:
            instruction = str(params.get("instruction", ""))
            if re.search(r"https?://", instruction, re.IGNORECASE):
                tool = "web_fetch"
                if "url" not in tool_params:
                    match = re.search(r"(https?://[^\s]+)", instruction)
                    if match:
                        tool_params["url"] = match.group(1)
            elif "snapshot" in instruction.lower() or "backup" in instruction.lower():
                tool = "snapshot"
            elif "zapisz" in instruction.lower() or "write" in instruction.lower():
                tool = "fs_write"

        if tool == "web_fetch":
            from aihub.web_tools import fetch_url

            result = await fetch_url(user_id, tool_params.get("url", ""))
            return {"ok": True, "action": "tool", "tool": tool, "result": result}

        if tool == "fs_write":
            from aihub.fs_tools import write_file

            result = write_file(
                user_id,
                tool_params.get("path", ""),
                tool_params.get("content", ""),
                overwrite=bool(tool_params.get("overwrite", True)),
            )
            return {"ok": True, "action": "tool", "tool": tool, "result": result}

        if tool in {"snapshot", "system_snapshot"}:
            from aihub.system_ops import create_snapshot

            result = create_snapshot(user_id, tool_params.get("reason", "agent"))
            return {"ok": True, "action": "tool", "tool": tool, "result": result}

        logger.warning("Unknown tool in action: %s", tool)
        return {"ok": False, "error": f"unknown tool: {tool}"}

    async def _exec_reason(self, params: dict, user_id: str) -> Dict[str, Any]:
        """Reasoning step used by task-graph loop.

        Produces structured diagnostic guidance for follow-up tasks.
        """
        message = str(params.get("message", "")).strip()
        memory_total = int(params.get("memory_total", 0) or 0)
        knowledge_hits = int(params.get("knowledge_hits", 0) or 0)

        needs_enrichment = memory_total == 0 and knowledge_hits == 0
        uncertainty = 0.2
        if memory_total == 0:
            uncertainty += 0.25
        if knowledge_hits == 0:
            uncertainty += 0.20
        uncertainty = min(0.95, uncertainty)

        recommendation = "query"
        if needs_enrichment:
            recommendation = "research"
        elif memory_total > 0 and "?" in message:
            recommendation = "query"
        elif any(k in message.lower() for k in ["zapisz", "snapshot", "fetch", "http"]):
            recommendation = "action"

        return {
            "ok": True,
            "action": "reason",
            "user_id": user_id,
            "analysis": {
                "needs_enrichment": needs_enrichment,
                "uncertainty": uncertainty,
                "memory_total": memory_total,
                "knowledge_hits": knowledge_hits,
                "recommendation": recommendation,
            },
        }
