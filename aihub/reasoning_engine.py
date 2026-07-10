#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Reasoning engine with iterative task-graph execution loop."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from aihub.agent_executor import AgentExecutor
from aihub.planner_engine import build_task_graph
from aihub.task_graph import (
    COMPLETED,
    FAILED,
    PENDING,
    RUNNING,
    SKIPPED,
    TaskGraph,
    TaskNode,
)

logger = logging.getLogger(__name__)


class ReasoningEngine:
    """Autonomous reasoning loop.

    Loop semantics:
    - start with planner-generated graph
    - while tasks pending:
      - execute next ready task
      - update context from result
      - optionally generate follow-up task(s)
    """

    def __init__(self, max_steps: int = 16, timeout_seconds: float = 25.0):
        self.max_steps = max(1, int(max_steps))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.executor = AgentExecutor()

    def _map_task_to_action(self, task_type: str) -> str:
        mapping = {
            "memory_query": "query",
            "query": "query",
            "learn": "learn",
            "research": "research",
            "action": "action",
            "reason": "reason",
        }
        return mapping.get(task_type, task_type)

    def _graph_has_type(self, graph: TaskGraph, task_type: str) -> bool:
        for n in graph.nodes.values():
            if n.task_type == task_type and n.status in {PENDING, RUNNING, COMPLETED}:
                return True
        return False

    def _update_context(
        self, context: Dict[str, Any], node: TaskNode, result: Dict[str, Any]
    ) -> None:
        context.setdefault("history", [])
        context["history"].append(
            {
                "task_id": node.task_id,
                "task_type": node.task_type,
                "ok": bool(result.get("ok", False)),
                "result": result,
            }
        )

        if node.task_type in {"memory_query", "query"}:
            total = int(result.get("total", 0) or 0)
            context["last_memory_total"] = total
            if "context" in result and isinstance(result["context"], dict):
                context["memory_context"] = result["context"]

        if node.task_type == "research":
            context["last_research_results"] = int(result.get("total_results", 0) or 0)
            context["last_research_facts"] = int(result.get("total_facts", 0) or 0)

        if node.task_type == "learn" and result.get("ok"):
            context["learned"] = context.get("learned", 0) + 1

    def _generate_next_step(
        self,
        graph: TaskGraph,
        node: TaskNode,
        result: Dict[str, Any],
        message: str,
        goal_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Generate additional follow-up task when runtime context indicates need.

        Returns created task_id or None.
        """
        hint = (
            goal_context.get("execution_hint", {})
            if isinstance(goal_context, dict)
            else {}
        )
        if isinstance(hint, dict) and hint.get("create_followups") is False:
            return None

        research_intensity = (
            str(hint.get("research_intensity", "general"))
            if isinstance(hint, dict)
            else "general"
        )

        # 1) If query found nothing and no research planned yet, add research.
        if (
            node.task_type in {"memory_query", "query"}
            and int(result.get("total", 0) or 0) == 0
        ):
            if not self._graph_has_type(graph, "research"):
                followup_type = "broad"
                if research_intensity in {"deep", "general", "light"}:
                    followup_type = (
                        "deep" if research_intensity == "deep" else "general"
                    )
                return graph.add_task(
                    task_type="research",
                    payload={"query": message, "research_type": followup_type},
                    priority=18,
                    metadata={
                        "generated_by": "reasoning_loop",
                        "trigger": "empty_memory",
                    },
                )

        # 2) If research found facts and there is no learn task, add learn summary.
        if node.task_type == "research" and int(result.get("total_facts", 0) or 0) > 0:
            if not self._graph_has_type(graph, "learn"):
                summary = (
                    f"Research summary for '{message}': "
                    f"{int(result.get('total_results', 0) or 0)} results, "
                    f"{int(result.get('total_facts', 0) or 0)} facts"
                )
                return graph.add_task(
                    task_type="learn",
                    payload={
                        "fact": summary,
                        "tags": ["reasoning", "research_summary"],
                    },
                    priority=32,
                    metadata={
                        "generated_by": "reasoning_loop",
                        "trigger": "research_success",
                    },
                )

        return None

    async def _execute_node(self, node: TaskNode, user_id: str) -> Dict[str, Any]:
        action = self._map_task_to_action(node.task_type)
        params = dict(node.payload or {})
        return await self.executor.execute(action, params, user_id)

    async def run(
        self,
        user_id: str,
        message: str,
        memory_context: Optional[Dict[str, Any]] = None,
        knowledge_context: Optional[Dict[str, Any]] = None,
        goal_context: Optional[Dict[str, Any]] = None,
        prebuilt_graph: Optional[TaskGraph] = None,
        planner_summary: Optional[Dict[str, Any]] = None,
        max_steps: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute full reasoning loop for a user message."""
        steps_limit = self.max_steps if max_steps is None else max(1, int(max_steps))
        timeout = (
            self.timeout_seconds
            if timeout_seconds is None
            else max(1.0, float(timeout_seconds))
        )

        start = time.monotonic()
        resolved_goal_context = dict(goal_context or {})
        if not resolved_goal_context and isinstance(memory_context, dict):
            inline_goal = memory_context.get("_goal_context")
            if isinstance(inline_goal, dict):
                resolved_goal_context = dict(inline_goal)

        context: Dict[str, Any] = {
            "memory_context": dict(memory_context or {}),
            "knowledge_context": dict(knowledge_context or {}),
            "goal_context": resolved_goal_context,
            "history": [],
        }

        if prebuilt_graph is None:
            plan_result = build_task_graph(
                message=message,
                memory_context=context["memory_context"],
                knowledge_context=context["knowledge_context"],
                goal_context=context["goal_context"],
                user_id=user_id,
            )
            graph = plan_result.graph
            resolved_planner_summary = dict(plan_result.summary)
            plan_source = "planner"
        else:
            graph = prebuilt_graph
            resolved_planner_summary = dict(planner_summary or {})
            if not resolved_planner_summary:
                resolved_planner_summary = {
                    "tasks_total": len(graph.nodes),
                    "source": "prebuilt_graph",
                }
            plan_source = "prebuilt_graph"

        executed = 0
        errors: list[Dict[str, str]] = []
        generated = 0
        timed_out = False
        runtime_generated_task_ids: list[str] = []
        planned_task_ids = [n.task_id for n in graph.nodes.values()]

        logger.info(
            "ReasoningEngine.run: user=%s tasks=%d max_steps=%d timeout=%.1fs plan_source=%s",
            user_id,
            len(graph.nodes),
            steps_limit,
            timeout,
            plan_source,
        )

        while graph.has_pending():
            if executed >= steps_limit:
                logger.warning("Reasoning loop reached max_steps=%d", steps_limit)
                break

            elapsed = time.monotonic() - start
            if elapsed > timeout:
                timed_out = True
                logger.warning("Reasoning loop timeout after %.2fs", elapsed)
                break

            node = graph.next_ready_task()
            if node is None:
                # pending tasks exist but none ready => dependency deadlock or failed ancestors
                logger.warning(
                    "Reasoning loop deadlock: pending tasks without ready node"
                )
                break

            try:
                result = await self._execute_node(node, user_id)
                ok = bool(result.get("ok", False))
                if ok:
                    graph.mark_complete(node.task_id, result=result, success=True)
                else:
                    err = str(result.get("error", "execution failed"))
                    graph.mark_complete(
                        node.task_id, result=result, success=False, error=err
                    )
                    errors.append(
                        {
                            "task_id": node.task_id,
                            "task_type": node.task_type,
                            "error": err,
                        }
                    )

                self._update_context(context, node, result)

                follow = self._generate_next_step(
                    graph,
                    node,
                    result,
                    message,
                    goal_context=resolved_goal_context,
                )
                if follow:
                    generated += 1
                    runtime_generated_task_ids.append(follow)
                    logger.debug("Reasoning loop generated follow-up task=%s", follow)

            except (RuntimeError, ValueError, TypeError, OSError, KeyError) as e:
                logger.error(
                    "Reasoning task crashed: id=%s type=%s err=%s",
                    node.task_id,
                    node.task_type,
                    e,
                    exc_info=True,
                )
                graph.mark_complete(
                    node.task_id,
                    result={"ok": False, "error": str(e)},
                    success=False,
                    error=str(e),
                )
                errors.append(
                    {
                        "task_id": node.task_id,
                        "task_type": node.task_type,
                        "error": str(e),
                    }
                )

            executed += 1

        duration_ms = (time.monotonic() - start) * 1000.0
        final = graph.serialize()
        status_counts = {
            PENDING: 0,
            RUNNING: 0,
            COMPLETED: 0,
            FAILED: 0,
            SKIPPED: 0,
        }
        for n in graph.nodes.values():
            status_counts[n.status] = status_counts.get(n.status, 0) + 1

        executed_task_ids = [
            str(h.get("task_id"))
            for h in context.get("history", [])
            if h.get("task_id")
        ]

        return {
            "ok": len(errors) == 0,
            "user_id": user_id,
            "message": message,
            "steps_executed": executed,
            "steps_generated": generated,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
            "errors": errors,
            "status_counts": status_counts,
            "planner_summary": resolved_planner_summary,
            "plan_source": plan_source,
            "planned_task_ids": planned_task_ids,
            "executed_task_ids": executed_task_ids,
            "runtime_generated_task_ids": runtime_generated_task_ids,
            "context": context,
            "graph": final,
        }


_reasoning_engine = ReasoningEngine()


async def run_reasoning_loop(
    user_id: str,
    message: str,
    memory_context: Optional[Dict[str, Any]] = None,
    knowledge_context: Optional[Dict[str, Any]] = None,
    goal_context: Optional[Dict[str, Any]] = None,
    prebuilt_graph: Optional[TaskGraph] = None,
    planner_summary: Optional[Dict[str, Any]] = None,
    max_steps: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Public API wrapper for reasoning loop execution."""
    return await _reasoning_engine.run(
        user_id=user_id,
        message=message,
        memory_context=memory_context,
        knowledge_context=knowledge_context,
        goal_context=goal_context,
        prebuilt_graph=prebuilt_graph,
        planner_summary=planner_summary,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
    )
