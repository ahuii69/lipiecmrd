"""Execution Graph — durable multi-step execution with validation and recovery."""

from __future__ import annotations

import time
import uuid
from typing import Any

from aihub.world_knowledge import store
from aihub.world_knowledge.models import ExecutionGraph, ExecutionNode


_MAX_REPLANS = 2


def build_execution_graph_from_plan(
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    task_id: str = "",
    goal_id: str = "",
    steps: list[dict[str, Any]],
) -> ExecutionGraph:
    """Create graph from planner-like steps. Does not execute."""
    nodes: list[ExecutionNode] = []
    edges: list[dict[str, str]] = []
    prev = ""
    for i, step in enumerate(steps[:12]):
        nid = str(step.get("step_id") or step.get("node_id") or f"n{i+1}")
        deps = list(step.get("dependencies") or ([] if not prev else [prev]))
        tool = str(step.get("tool") or step.get("tool_name") or "")
        action = str(step.get("action") or step.get("title") or f"step_{i+1}")
        idem = str(step.get("idempotency_key") or f"{task_id or turn_id}:{nid}:{action}")[:160]
        node = ExecutionNode(
            node_id=nid,
            node_type=step.get("node_type") or ("tool" if tool else "reason"),
            action=action[:200],
            tool_name=tool[:120],
            arguments=dict(step.get("arguments") or {}),
            dependencies=[str(d) for d in deps],
            preconditions=[str(x) for x in (step.get("preconditions") or [])][:8],
            expected_effects=[str(x) for x in (step.get("expected_effects") or [])][:8],
            validation_rules=[str(x) for x in (step.get("validation") or step.get("validation_rules") or [])][:8],
            retry_policy=str(step.get("retry_policy") or "transient"),
            rollback_action=str(step.get("rollback") or step.get("rollback_action") or ""),
            idempotency_key=idem,
            status="pending",
        )
        nodes.append(node)
        for d in deps:
            edges.append({"from": str(d), "to": nid})
        prev = nid
    graph = ExecutionGraph(
        execution_id=str(uuid.uuid4()),
        task_id=task_id,
        goal_id=goal_id,
        user_id=user_id,
        session_id=session_id,
        turn_id=turn_id,
        status="pending",
        nodes=nodes,
        edges=edges,
        current_node=nodes[0].node_id if nodes else "",
        created_at=time.time(),
        updated_at=time.time(),
    )
    store.save_execution_graph(graph)
    return graph


def mark_node_completed(
    graph: ExecutionGraph,
    node_id: str,
    *,
    summary: str = "",
    evidence_ids: list[str] | None = None,
    validation_ok: bool = False,
) -> ExecutionGraph:
    for n in graph.nodes:
        if n.node_id != node_id:
            continue
        if store.effect_already_done(n.idempotency_key):
            # already done — do not re-record, just sync status
            n.status = "completed"
        else:
            recorded = store.record_effect(
                execution_id=graph.execution_id,
                node_id=node_id,
                idempotency_key=n.idempotency_key,
                summary=summary,
                evidence_id=(evidence_ids or [""])[0] if evidence_ids else "",
            )
            n.status = "completed" if recorded or True else "completed"
            n.completed_at = time.time()
            n.result_summary = summary[:400]
            n.evidence_ids = list(evidence_ids or [])
            if n.validation_rules:
                store.record_validation(
                    execution_id=graph.execution_id,
                    node_id=node_id,
                    rule=n.validation_rules[0],
                    succeeded=validation_ok,
                    evidence_id=(evidence_ids or [""])[0] if evidence_ids else "",
                    detail=summary[:200],
                )
                if not validation_ok:
                    n.status = "failed"
                    n.error = "validation_failed"
                    graph.failed_nodes = list(dict.fromkeys(graph.failed_nodes + [node_id]))
                    store.save_execution_graph(graph)
                    return graph
        if node_id not in graph.completed_nodes:
            graph.completed_nodes.append(node_id)
        break
    # advance
    remaining = [n for n in graph.nodes if n.status == "pending"]
    graph.current_node = remaining[0].node_id if remaining else ""
    graph.status = "completed" if not remaining else "running"
    graph.updated_at = time.time()
    store.save_execution_graph(graph)
    return graph


def fail_node_and_replan(
    graph: ExecutionGraph,
    node_id: str,
    *,
    error: str,
    error_class: str = "transient",
    alternative_tool: str = "",
) -> ExecutionGraph:
    for n in graph.nodes:
        if n.node_id == node_id:
            n.status = "failed"
            n.error = error[:240]
            n.attempt_count += 1
            break
    graph.failed_nodes = list(dict.fromkeys(graph.failed_nodes + [node_id]))
    if error_class in ("auth", "permanent", "rejected") or graph.replan_count >= _MAX_REPLANS:
        graph.status = "failed"
        store.save_execution_graph(graph)
        return graph
    if error_class == "user_input":
        # inject user_input_required and block dependents
        wait_id = f"wait_{node_id}"
        graph.nodes.append(
            ExecutionNode(
                node_id=wait_id,
                node_type="user_input_required",
                action="need_user_input",
                dependencies=[],
                status="pending",
                idempotency_key=f"{graph.execution_id}:{wait_id}",
            )
        )
        graph.blocked_nodes = [
            n.node_id for n in graph.nodes if node_id in (n.dependencies or [])
        ]
        graph.status = "waiting_user"
        graph.current_node = wait_id
        store.save_execution_graph(graph)
        return graph
    # bounded replan: add alternate tool node if provided
    graph.replan_count += 1
    if alternative_tool:
        alt_id = f"alt_{node_id}_{graph.replan_count}"
        deps = []
        for n in graph.nodes:
            if n.node_id == node_id:
                deps = list(n.dependencies)
                break
        graph.nodes.append(
            ExecutionNode(
                node_id=alt_id,
                node_type="tool",
                action=f"retry_with_{alternative_tool}",
                tool_name=alternative_tool,
                dependencies=deps,
                status="pending",
                idempotency_key=f"{graph.execution_id}:{alt_id}",
                retry_policy="transient",
            )
        )
        graph.current_node = alt_id
        graph.status = "running"
    else:
        graph.status = "failed" if error_class == "permanent" else "running"
    store.save_execution_graph(graph)
    return graph


def resume_execution(execution_id: str, *, owner: str = "runtime") -> ExecutionGraph | None:
    """Recover after restart: clear lease, skip completed side effects."""
    graph = store.get_execution_graph(execution_id)
    if graph is None:
        return None
    graph.lease_owner = owner
    graph.lease_until = time.time() + 60.0
    # sync completed from effects table
    for n in graph.nodes:
        if n.idempotency_key and store.effect_already_done(n.idempotency_key):
            if n.status != "completed":
                n.status = "completed"
                if n.node_id not in graph.completed_nodes:
                    graph.completed_nodes.append(n.node_id)
    pending = [n for n in graph.nodes if n.status == "pending"]
    graph.current_node = pending[0].node_id if pending else ""
    if not pending and graph.status not in ("failed", "cancelled"):
        graph.status = "completed"
    elif graph.status in ("pending", "running"):
        graph.status = "running"
    store.save_execution_graph(graph)
    return graph


def should_retry(error_class: str, attempt_count: int) -> bool:
    if error_class in ("auth", "permanent", "rejected", "user_input"):
        return False
    if error_class == "validation":
        return False
    if error_class in ("transient", "timeout", "rate_limit", "5xx"):
        return attempt_count < 3
    return attempt_count < 1
