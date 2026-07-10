"""Tests for reasoning loop orchestration and safeguards."""

import asyncio
from types import SimpleNamespace

from aihub.reasoning_engine import ReasoningEngine
from aihub.task_graph import TaskGraph


async def _fake_execute_ok(_action_type, params, _user_id):
    if params.get("query"):
        return {"ok": True, "total": 1, "context": {"total": 1}}
    return {"ok": True, "total_results": 1, "total_facts": 1}


async def _fake_execute_fail_once(action_type, _params, _user_id):
    if action_type == "reason":
        raise RuntimeError("reason boom")
    return {"ok": True}


def test_reasoning_engine_executes_graph(monkeypatch):
    engine = ReasoningEngine(max_steps=8, timeout_seconds=5)
    monkeypatch.setattr(engine.executor, "execute", _fake_execute_ok)

    result = asyncio.get_event_loop().run_until_complete(
        engine.run(user_id="rx1", message="Co pamiętasz o Pythonie?")
    )

    assert result["steps_executed"] >= 1
    assert "graph" in result
    assert "status_counts" in result
    assert result["status_counts"]["completed"] >= 1


def test_reasoning_engine_respects_max_steps(monkeypatch):
    graph = TaskGraph()
    graph.add_task("reason", payload={"message": "x"}, priority=1)
    graph.add_task("memory_query", payload={"query": "x"}, priority=2)
    graph.add_task("research", payload={"query": "x"}, priority=3)

    def _fake_plan(**_kwargs):
        return SimpleNamespace(graph=graph, summary={"tasks_total": len(graph.nodes)})

    engine = ReasoningEngine(max_steps=1, timeout_seconds=10)
    monkeypatch.setattr("aihub.reasoning_engine.build_task_graph", _fake_plan)
    monkeypatch.setattr(engine.executor, "execute", _fake_execute_ok)

    result = asyncio.get_event_loop().run_until_complete(
        engine.run(user_id="rx2", message="x")
    )

    assert result["steps_executed"] == 1
    assert result["status_counts"]["pending"] >= 1


def test_reasoning_engine_handles_executor_crash(monkeypatch):
    graph = TaskGraph()
    graph.add_task("reason", payload={"message": "x"}, priority=1)

    def _fake_plan(**_kwargs):
        return SimpleNamespace(graph=graph, summary={"tasks_total": len(graph.nodes)})

    engine = ReasoningEngine(max_steps=4, timeout_seconds=10)
    monkeypatch.setattr("aihub.reasoning_engine.build_task_graph", _fake_plan)
    monkeypatch.setattr(engine.executor, "execute", _fake_execute_fail_once)

    result = asyncio.get_event_loop().run_until_complete(
        engine.run(user_id="rx3", message="x")
    )

    assert result["ok"] is False
    assert len(result["errors"]) >= 1
    assert result["status_counts"]["failed"] >= 1
