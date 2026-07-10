#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Task graph for autonomous agent planning and execution.

This module provides a production-ready directed acyclic task graph (DAG)
with dependency tracking, task state transitions, and stable serialization.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
SKIPPED = "skipped"
_VALID_STATUSES = {PENDING, RUNNING, COMPLETED, FAILED, SKIPPED}


@dataclass
class TaskNode:
    """Single executable unit in a task graph."""

    task_id: str
    task_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    priority: int = 50
    status: str = PENDING
    result: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)
    retries: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "payload": self.payload,
            "depends_on": list(self.depends_on),
            "priority": int(self.priority),
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "created_ts": float(self.created_ts),
            "updated_ts": float(self.updated_ts),
            "retries": int(self.retries),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskNode":
        status = str(data.get("status", PENDING))
        if status not in _VALID_STATUSES:
            logger.warning("Unknown status during TaskNode.from_dict: %s", status)
            status = PENDING

        return cls(
            task_id=str(data["task_id"]),
            task_type=str(data["task_type"]),
            payload=dict(data.get("payload") or {}),
            depends_on=[str(x) for x in (data.get("depends_on") or [])],
            priority=int(data.get("priority", 50)),
            status=status,
            result=dict(data.get("result") or {}),
            error=str(data.get("error", "")),
            created_ts=float(data.get("created_ts", time.time())),
            updated_ts=float(data.get("updated_ts", time.time())),
            retries=int(data.get("retries", 0)),
            metadata=dict(data.get("metadata") or {}),
        )


class TaskGraph:
    """Directed acyclic graph of task nodes.

    Features:
    - dependency-aware scheduling (`next_ready_task`)
    - deterministic ordering by (priority, created_ts, task_id)
    - safe state transitions and error propagation
    - robust serialize/deserialize roundtrip
    """

    def __init__(self, task_id_prefix: str = "task") -> None:
        self.nodes: Dict[str, TaskNode] = {}
        self.created_ts: float = time.time()
        self.updated_ts: float = self.created_ts
        self.task_id_prefix: str = str(task_id_prefix or "task").strip() or "task"
        self._next_seq: int = 1

    def _touch(self) -> None:
        self.updated_ts = time.time()

    def _make_task_id(
        self,
        task_type: str,
        payload: Dict[str, Any],
        depends_on: List[str],
        priority: int,
    ) -> str:
        del task_type, payload, depends_on, priority
        while True:
            candidate = f"{self.task_id_prefix}-{self._next_seq:04d}"
            self._next_seq += 1
            if candidate not in self.nodes:
                return candidate

    def add_task(
        self,
        task_type: str,
        payload: Optional[Dict[str, Any]] = None,
        depends_on: Optional[List[str]] = None,
        priority: int = 50,
        task_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add a task node and validate its dependencies.

        Args:
            task_type: Logical task type (e.g. "research", "memory_query").
            payload: Task data.
            depends_on: List of task IDs this task depends on.
            priority: Lower value means earlier scheduling.
            task_id: Optional explicit task ID.
            metadata: Arbitrary planner metadata.

        Returns:
            The task ID.
        """
        if not isinstance(task_type, str) or not task_type.strip():
            raise ValueError("task_type must be a non-empty string")

        safe_payload = dict(payload or {})
        deps = [str(x) for x in (depends_on or [])]
        safe_meta = dict(metadata or {})
        safe_priority = int(priority)
        if safe_priority < 0:
            safe_priority = 0

        for dep in deps:
            if dep not in self.nodes:
                raise ValueError(f"dependency does not exist: {dep}")

        final_id = str(task_id).strip() or self._make_task_id(
            task_type=task_type,
            payload=safe_payload,
            depends_on=deps,
            priority=safe_priority,
        )

        if final_id in self.nodes:
            raise ValueError(f"task_id already exists: {final_id}")

        node = TaskNode(
            task_id=final_id,
            task_type=task_type.strip(),
            payload=safe_payload,
            depends_on=deps,
            priority=safe_priority,
            metadata=safe_meta,
        )
        self.nodes[final_id] = node
        self._touch()
        logger.debug(
            "TaskGraph.add_task: id=%s type=%s deps=%d priority=%d",
            final_id,
            node.task_type,
            len(node.depends_on),
            node.priority,
        )
        return final_id

    def _dependencies_satisfied(self, node: TaskNode) -> bool:
        for dep_id in node.depends_on:
            dep = self.nodes.get(dep_id)
            if dep is None:
                return False
            if dep.status != COMPLETED:
                return False
        return True

    def next_ready_task(self) -> Optional[TaskNode]:
        """Return next schedulable pending task, or None if nothing is ready."""
        candidates: List[TaskNode] = []
        for node in self.nodes.values():
            if node.status != PENDING:
                continue
            if self._dependencies_satisfied(node):
                candidates.append(node)

        if not candidates:
            return None

        candidates.sort(key=lambda n: (n.priority, n.task_id))
        chosen = candidates[0]
        chosen.status = RUNNING
        chosen.updated_ts = time.time()
        self._touch()
        logger.debug(
            "TaskGraph.next_ready_task: id=%s type=%s priority=%d",
            chosen.task_id,
            chosen.task_type,
            chosen.priority,
        )
        return chosen

    def mark_complete(
        self,
        task_id: str,
        result: Optional[Dict[str, Any]] = None,
        *,
        success: bool = True,
        error: str = "",
    ) -> None:
        """Mark task as completed or failed with result/error payload."""
        node = self.nodes.get(task_id)
        if node is None:
            raise KeyError(f"task not found: {task_id}")

        node.result = dict(result or {})
        node.error = str(error or "")
        node.status = COMPLETED if success else FAILED
        node.updated_ts = time.time()
        self._touch()

        logger.debug(
            "TaskGraph.mark_complete: id=%s success=%s", node.task_id, success
        )

        # If this task failed, tasks depending on it can never become ready.
        # Mark them as skipped to avoid deadlock loops.
        if not success:
            blocked: Set[str] = set()
            stack = [node.task_id]
            while stack:
                cur = stack.pop()
                for other in self.nodes.values():
                    if other.task_id in blocked:
                        continue
                    if other.status != PENDING:
                        continue
                    if cur in other.depends_on:
                        blocked.add(other.task_id)
                        stack.append(other.task_id)

            for task in blocked:
                dep = self.nodes[task]
                dep.status = SKIPPED
                dep.error = (
                    dep.error
                    or f"skipped because dependency failed: {node.task_id}"
                )
                dep.updated_ts = time.time()
                logger.debug("TaskGraph: auto-skip blocked task=%s", dep.task_id)

            self._touch()

    def has_pending(self) -> bool:
        """Return True if graph still has work that can continue.

        True for pending/running tasks. Completed/failed/skipped-only graph returns False.
        """
        for n in self.nodes.values():
            if n.status in {PENDING, RUNNING}:
                return True
        return False

    def serialize(self) -> Dict[str, Any]:
        """Serialize graph to stable dict representation."""
        return {
            "created_ts": float(self.created_ts),
            "updated_ts": float(self.updated_ts),
            "task_id_prefix": self.task_id_prefix,
            "next_seq": int(self._next_seq),
            "nodes": [
                self.nodes[k].to_dict()
                for k in sorted(self.nodes.keys())
            ],
        }

    @classmethod
    def deserialize(cls, data: Dict[str, Any]) -> "TaskGraph":
        """Rebuild graph from serialized dict and validate integrity."""
        if not isinstance(data, dict):
            raise ValueError("TaskGraph.deserialize expects dict")

        g = cls(task_id_prefix=str(data.get("task_id_prefix", "task")))
        g.created_ts = float(data.get("created_ts", time.time()))
        g.updated_ts = float(data.get("updated_ts", g.created_ts))

        nodes_raw = data.get("nodes")
        if not isinstance(nodes_raw, list):
            raise ValueError("TaskGraph.deserialize: nodes must be a list")

        for item in nodes_raw:
            node = TaskNode.from_dict(dict(item))
            if node.task_id in g.nodes:
                raise ValueError(f"duplicate task_id in graph payload: {node.task_id}")
            g.nodes[node.task_id] = node

        # Validate dependency references
        for node in g.nodes.values():
            for dep in node.depends_on:
                if dep not in g.nodes:
                    raise ValueError(
                        f"task {node.task_id} depends on missing task {dep}"
                    )

        serialized_next_seq = data.get("next_seq")
        if isinstance(serialized_next_seq, int) and serialized_next_seq > 0:
            g._next_seq = serialized_next_seq
        else:
            # backward compatibility for old payloads without next_seq
            max_suffix = 0
            prefix = f"{g.task_id_prefix}-"
            for task_id in g.nodes:
                if task_id.startswith(prefix):
                    suffix = task_id[len(prefix):]
                    if suffix.isdigit():
                        max_suffix = max(max_suffix, int(suffix))
            g._next_seq = max(max_suffix + 1, len(g.nodes) + 1)

        return g
