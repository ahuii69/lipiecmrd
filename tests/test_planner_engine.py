"""Tests for planner engine task graph generation."""

from aihub.planner_engine import build_task_graph, plan


def test_planner_never_returns_empty_graph():
    result = build_task_graph(message="", user_id="u1")

    assert result.graph is not None
    assert len(result.graph.nodes) >= 2  # reason + fallback memory_query
    assert result.summary["tasks_total"] == len(result.graph.nodes)


def test_planner_builds_research_learn_action_paths():
    msg = "Sprawdź https://example.com i zapamiętaj że lubię Pythona oraz zapisz: notes.txt :: test"
    result = build_task_graph(message=msg, user_id="u2")

    types = {n.task_type for n in result.graph.nodes.values()}
    assert "reason" in types
    assert "research" in types
    assert "learn" in types
    assert "action" in types


def test_planner_dependencies_point_to_reason():
    msg = "Co pamiętasz o mnie?"
    result = build_task_graph(message=msg, user_id="u3")

    reason_nodes = [n for n in result.graph.nodes.values() if n.task_type == "reason"]
    assert len(reason_nodes) == 1
    reason_id = reason_nodes[0].task_id

    for node in result.graph.nodes.values():
        if node.task_type == "reason":
            continue
        assert reason_id in node.depends_on


def test_legacy_plan_api_returns_non_empty_list():
    tasks = plan("Wyszukaj informacje o FastAPI")
    assert isinstance(tasks, list)
    assert len(tasks) >= 1
    assert all("type" in t for t in tasks)
