"""Tests for knowledge graph runtime wiring and relations."""

from aihub.memory_engine import add_episode, add_fact
from aihub.psyche_engine import ensure_user


def test_knowledge_graph_user_and_episode_edges():
    uid = "kg_edges_user"
    ensure_user(uid)

    episode_id = add_episode(uid, "Rozmowa o agentach", meta={"intent": "chat"})
    fact_id = add_fact(
        uid,
        "Użytkownik interesuje się agentami AI",
        tags=["interest"],
        meta={"source_episode": episode_id},
    )

    from aihub.knowledge_graph import get_related_nodes

    related_fact = get_related_nodes(fact_id)
    related_ids = {n.node_id for n in related_fact}

    assert uid in related_ids
    assert episode_id in related_ids


def test_knowledge_graph_query_nodes_returns_hits():
    uid = "kg_query_user"
    ensure_user(uid)

    add_fact(uid, "FastAPI obsługuje routing HTTP", tags=["tech"], meta={})

    from aihub.knowledge_graph import query_nodes

    hits = query_nodes("FastAPI", limit=5, user_id=uid)
    assert len(hits) >= 1
    assert any("FastAPI" in h.content for h in hits)


def test_knowledge_graph_query_nodes_respects_user_scope():
    uid_a = "kg_scope_a"
    uid_b = "kg_scope_b"
    ensure_user(uid_a)
    ensure_user(uid_b)

    add_fact(uid_a, "Sekret scope A", tags=["scope"], meta={})
    add_fact(uid_b, "Sekret scope B", tags=["scope"], meta={})

    from aihub.knowledge_graph import query_nodes

    hits_a = query_nodes("Sekret", limit=10, user_id=uid_a)
    hits_b = query_nodes("Sekret", limit=10, user_id=uid_b)

    assert any("scope A" in h.content for h in hits_a)
    assert not any("scope B" in h.content for h in hits_a)
    assert any("scope B" in h.content for h in hits_b)
    assert not any("scope A" in h.content for h in hits_b)


def test_knowledge_graph_query_nodes_requires_user_id():
    from aihub.knowledge_graph import query_nodes

    try:
        query_nodes("anything", limit=5, user_id="")
    except ValueError as exc:
        assert "user_id" in str(exc)
    else:
        raise AssertionError("query_nodes should require non-empty user_id")


def test_knowledge_graph_stats_non_negative():
    from aihub.knowledge_graph import stats

    st = stats()
    assert st["nodes"] >= 0
    assert st["edges"] >= 0
    assert st["relation_types"] >= 0
