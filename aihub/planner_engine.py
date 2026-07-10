#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Planner engine for autonomous agent execution.

Builds a dependency-aware TaskGraph from:
- user message
- memory context
- knowledge graph context
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from aihub.task_graph import TaskGraph

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


@dataclass
class PlanResult:
    """Planner output wrapper."""

    graph: TaskGraph
    summary: Dict[str, Any]


class PlannerEngine:
    """Task-graph planner.

    Planner always returns at least one executable task and never emits an
    empty graph.
    """

    def __init__(self, max_tasks: int = 12):
        self.max_tasks = max(3, int(max_tasks))

    def _detect_intents(self, message: str) -> Dict[str, bool]:
        m = (message or "").lower()
        return {
            "research": any(
                k in m for k in ["sprawdź", "wyszukaj", "zbadaj", "research", "find"]
            ),
            "memory_query": any(
                k in m
                for k in ["pamiętasz", "przypomnij", "co wiesz", "powiedz mi", "jak"]
            )
            or "?" in m,
            "learn": any(
                k in m
                for k in [
                    "zapamiętaj",
                    "zawsze",
                    "nigdy",
                    "lubię",
                    "preferuję",
                    "mam na imię",
                ]
            ),
            "action": any(
                k in m
                for k in ["zapisz", "utwórz", "fetch", "snapshot", "backup", "wykonaj"]
            )
            or bool(URL_RE.search(message or "")),
        }

    def _build_action_payload(self, message: str) -> Dict[str, Any]:
        m = (message or "").lower()
        url_match = URL_RE.search(message or "")

        if url_match:
            return {
                "tool": "web_fetch",
                "params": {"url": url_match.group(0)},
                "instruction": message,
            }

        if "snapshot" in m or "backup" in m or "kopia" in m:
            return {
                "tool": "snapshot",
                "params": {"reason": "planner:auto"},
                "instruction": message,
            }

        if "zapisz" in m:
            # best-effort extraction: "zapisz: path :: content"
            path = "agent_note.txt"
            content = message
            if "::" in message and ":" in message:
                try:
                    left, right = message.split("::", 1)
                    _, p = left.split(":", 1)
                    path = p.strip() or path
                    content = right.strip() or content
                except ValueError as exc:
                    logger.debug("Planner fs_write shorthand parse failed; using raw message: %s", exc)

            return {
                "tool": "fs_write",
                "params": {
                    "path": path,
                    "content": content,
                    "overwrite": True,
                },
                "instruction": message,
            }

        return {
            "tool": "fs_write",
            "params": {
                "path": "agent_action.txt",
                "content": message,
                "overwrite": True,
            },
            "instruction": message,
        }

    def _extract_knowledge_context(
        self, message: str, *, user_id: str
    ) -> Dict[str, Any]:
        try:
            from aihub.knowledge_graph import query_nodes

            hits = query_nodes(message, limit=8, user_id=user_id)
            return {
                "hits": [
                    {
                        "node_id": h.node_id,
                        "type": h.node_type,
                        "confidence": float(h.confidence),
                        "content": h.content,
                    }
                    for h in hits
                ]
            }
        except (
            ImportError,
            RuntimeError,
            ValueError,
            TypeError,
            AttributeError,
            OSError,
        ):
            logger.debug("PlannerEngine: knowledge context fetch failed", exc_info=True)
            return {"hits": []}

    def _extract_selected_goal(
        self, goal_context: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(goal_context, dict):
            return None
        goal = goal_context.get("selected_goal")
        if not isinstance(goal, dict):
            return None
        if not goal.get("goal_id"):
            return None
        return goal

    def _extract_execution_hint(
        self, goal_context: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not isinstance(goal_context, dict):
            return {}
        hint = goal_context.get("execution_hint")
        if not isinstance(hint, dict):
            return {}
        return dict(hint)

    def _goal_priority_boost(self, selected_goal: Optional[Dict[str, Any]]) -> int:
        if not selected_goal:
            return 0
        urgency = float(selected_goal.get("urgency", 0.5) or 0.5)
        priority = float(selected_goal.get("priority", 0.5) or 0.5)
        return int(max(0, min(15, round((urgency * 0.6 + priority * 0.4) * 15))))

    def build_task_graph(
        self,
        message: str,
        memory_context: Optional[Dict[str, Any]] = None,
        knowledge_context: Optional[Dict[str, Any]] = None,
        goal_context: Optional[Dict[str, Any]] = None,
        user_id: str = "default",
        max_tasks_override: Optional[int] = None,
    ) -> PlanResult:
        """Create task graph from message + contexts.

        Graph includes tasks of types:
        - reason
        - memory_query
        - research
        - learn
        - action
        """
        safe_msg = (message or "").strip()
        if not safe_msg:
            safe_msg = "Użytkownik nie podał treści — wykonaj diagnozę kontekstu"

        _max_tasks = max_tasks_override if max_tasks_override is not None else self.max_tasks
        memory_ctx = dict(memory_context or {})
        inline_goal_context = (
            memory_ctx.get("_goal_context")
            if isinstance(memory_ctx.get("_goal_context"), dict)
            else None
        )
        kg_ctx = dict(
            knowledge_context
            or self._extract_knowledge_context(safe_msg, user_id=user_id)
        )
        intents = self._detect_intents(safe_msg)
        resolved_goal_context = goal_context or inline_goal_context
        selected_goal = self._extract_selected_goal(resolved_goal_context)
        execution_hint = self._extract_execution_hint(resolved_goal_context)
        goal_type = str(selected_goal.get("goal_type", "")) if selected_goal else ""
        goal_boost = self._goal_priority_boost(selected_goal)
        planning_bias = str(execution_hint.get("planning_bias", "")).strip().lower()
        research_intensity = str(
            execution_hint.get("research_intensity", "general")
        ).strip().lower()
        create_followups = bool(execution_hint.get("create_followups", True))

        if planning_bias == "research_heavy":
            intents["research"] = True
        elif planning_bias in {"task_focused", "maintenance"}:
            intents["action"] = True

        if selected_goal is not None:
            if goal_type in {"research_goal", "information_need"}:
                intents["research"] = True
            if goal_type in {"task", "user_intent_goal", "system_goal"}:
                intents["action"] = True
            if goal_type in {"learning_goal", "long_term_goal"}:
                intents["learn"] = True
                intents["memory_query"] = True

        graph = TaskGraph()

        reason_id = graph.add_task(
            task_type="reason",
            payload={
                "message": safe_msg,
                "memory_total": int(memory_ctx.get("total", 0) or 0),
                "knowledge_hits": len(kg_ctx.get("hits", [])),
                "user_id": user_id,
                "goal_id": selected_goal.get("goal_id", "") if selected_goal else "",
                "goal_type": goal_type,
                "goal_planning_bias": planning_bias,
                "goal_research_intensity": research_intensity,
                "goal_create_followups": create_followups,
            },
            priority=1,
            metadata={"phase": "analysis"},
        )

        planned = 1

        if intents["memory_query"] and planned < _max_tasks:
            graph.add_task(
                task_type="memory_query",
                payload={
                    "query": safe_msg,
                    "limit": 12,
                },
                depends_on=[reason_id],
                priority=max(2, 10 - goal_boost),
                metadata={"phase": "retrieval"},
            )
            planned += 1

        # Research also when memory appears sparse.
        memory_total = int(memory_ctx.get("total", 0) or 0)
        if (intents["research"] or memory_total == 0) and planned < _max_tasks:
            research_type = "general" if memory_total > 0 else "broad"
            if research_intensity in {"deep", "general", "light"}:
                research_type = "deep" if research_intensity == "deep" else "general"
            if selected_goal is not None and goal_type in {
                "research_goal",
                "information_need",
            }:
                research_type = (
                    "deep"
                    if float(selected_goal.get("urgency", 0.5) or 0.5) >= 0.65
                    else "general"
                )

            graph.add_task(
                task_type="research",
                payload={
                    "query": safe_msg,
                    "research_type": research_type,
                    "goal_id": selected_goal.get("goal_id", "")
                    if selected_goal
                    else "",
                },
                depends_on=[reason_id],
                priority=max(3, 20 - goal_boost),
                metadata={"phase": "enrichment"},
            )
            planned += 1

        if intents["learn"] and planned < _max_tasks:
            graph.add_task(
                task_type="learn",
                payload={
                    "fact": safe_msg,
                    "tags": ["planner", "user_signal"],
                    "goal_id": selected_goal.get("goal_id", "")
                    if selected_goal
                    else "",
                },
                depends_on=[reason_id],
                priority=max(4, 30 - goal_boost),
                metadata={"phase": "learning"},
            )
            planned += 1

        if intents["action"] and planned < _max_tasks:
            graph.add_task(
                task_type="action",
                payload=self._build_action_payload(safe_msg),
                depends_on=[reason_id],
                priority=max(5, 40 - goal_boost),
                metadata={"phase": "execution"},
            )
            planned += 1

        if selected_goal is not None and planned < _max_tasks:
            has_memory_query = any(
                n.task_type == "memory_query" for n in graph.nodes.values()
            )
            if not has_memory_query:
                graph.add_task(
                    task_type="memory_query",
                    payload={
                        "query": safe_msg,
                        "limit": 8,
                        "goal_id": selected_goal.get("goal_id", ""),
                    },
                    depends_on=[reason_id],
                    priority=max(2, 12 - goal_boost),
                    metadata={"phase": "goal_followup"},
                )
                planned += 1

        # Hard fallback: planner must never be empty/analysis-only.
        if len(graph.nodes) == 1:
            graph.add_task(
                task_type="memory_query",
                payload={"query": safe_msg, "limit": 8},
                depends_on=[reason_id],
                priority=15,
                metadata={"phase": "fallback"},
            )

        summary = {
            "user_id": user_id,
            "message": safe_msg,
            "intents": intents,
            "memory_total": memory_total,
            "knowledge_hits": len(kg_ctx.get("hits", [])),
            "tasks_total": len(graph.nodes),
            "goal_id": selected_goal.get("goal_id", "") if selected_goal else "",
            "goal_type": goal_type,
            "goal_boost": goal_boost,
            "goal_planning_bias": planning_bias,
            "goal_research_intensity": research_intensity,
            "goal_create_followups": create_followups,
        }

        logger.info(
            "PlannerEngine: user=%s tasks=%d intents=%s",
            user_id,
            len(graph.nodes),
            ",".join([k for k, v in intents.items() if v]) or "none",
        )

        return PlanResult(graph=graph, summary=summary)


_planner = PlannerEngine()


def build_task_graph(
    message: str,
    memory_context: Optional[Dict[str, Any]] = None,
    knowledge_context: Optional[Dict[str, Any]] = None,
    goal_context: Optional[Dict[str, Any]] = None,
    user_id: str = "default",
) -> PlanResult:
    """Public planner API returning a task graph."""
    return _planner.build_task_graph(
        message=message,
        memory_context=memory_context,
        knowledge_context=knowledge_context,
        goal_context=goal_context,
        user_id=user_id,
    )


def plan(text: str) -> List[Dict[str, Any]]:
    """Backward-compatible API used by older call sites.

    Returns a list of dicts with type/payload/priority for legacy consumers.
    """
    result = build_task_graph(text)
    out: List[Dict[str, Any]] = []
    for node in result.graph.nodes.values():
        if node.task_type == "reason":
            continue
        out.append(
            {
                "type": node.task_type,
                "payload": node.payload,
                "priority": node.priority,
                "depends_on": list(node.depends_on),
            }
        )
    out.sort(key=lambda x: (x["priority"], len(x["depends_on"])))
    return out
