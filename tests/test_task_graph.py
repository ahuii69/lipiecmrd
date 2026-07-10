"""Tests for task graph dependency execution and persistence."""

from aihub.task_graph import COMPLETED, FAILED, PENDING, SKIPPED, TaskGraph


def test_task_graph_dependency_ordering():
    graph = TaskGraph()

    root = graph.add_task("reason", payload={"message": "x"}, priority=1)
    child_a = graph.add_task("memory_query", payload={"query": "x"}, depends_on=[root], priority=10)
    child_b = graph.add_task("research", payload={"query": "x"}, depends_on=[root], priority=20)

    first = graph.next_ready_task()
    assert first is not None
    assert first.task_id == root
    graph.mark_complete(root, result={"ok": True}, success=True)

    second = graph.next_ready_task()
    assert second is not None
    assert second.task_id == child_a
    graph.mark_complete(child_a, result={"ok": True}, success=True)

    third = graph.next_ready_task()
    assert third is not None
    assert third.task_id == child_b
    graph.mark_complete(child_b, result={"ok": True}, success=True)

    assert graph.has_pending() is False


def test_task_graph_failed_dependency_skips_descendants():
    graph = TaskGraph()

    t1 = graph.add_task("reason", payload={})
    t2 = graph.add_task("memory_query", payload={"query": "test"}, depends_on=[t1])
    t3 = graph.add_task("learn", payload={"fact": "abc"}, depends_on=[t2])

    first = graph.next_ready_task()
    assert first is not None
    assert first.task_id == t1
    graph.mark_complete(t1, result={"ok": False}, success=False, error="boom")

    assert graph.nodes[t1].status == FAILED
    assert graph.nodes[t2].status == SKIPPED
    assert graph.nodes[t3].status == SKIPPED
    assert graph.has_pending() is False


def test_task_graph_serialize_deserialize_roundtrip():
    graph = TaskGraph()
    a = graph.add_task("reason", payload={"message": "hello"}, priority=1)
    b = graph.add_task("research", payload={"query": "hello"}, depends_on=[a], priority=5)

    node = graph.next_ready_task()
    assert node is not None
    graph.mark_complete(node.task_id, result={"ok": True, "analysis": {}}, success=True)

    payload = graph.serialize()
    rebuilt = TaskGraph.deserialize(payload)

    assert set(rebuilt.nodes.keys()) == {a, b}
    assert rebuilt.nodes[a].status == COMPLETED
    assert rebuilt.nodes[b].status == PENDING

    ready = rebuilt.next_ready_task()
    assert ready is not None
    assert ready.task_id == b


def test_task_graph_add_task_rejects_missing_dependency():
    graph = TaskGraph()
    try:
        graph.add_task("research", payload={"query": "x"}, depends_on=["missing"])
        assert False, "Expected ValueError for missing dependency"
    except ValueError as e:
        assert "dependency does not exist" in str(e)
