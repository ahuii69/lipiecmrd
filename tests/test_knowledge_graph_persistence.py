"""Tests for Knowledge Graph persistence and DB reload."""

from aihub.db import fetch_all, fetch_one
from aihub.memory_engine import add_episode, add_fact
from aihub.psyche_engine import ensure_user


def test_kg_node_persisted(isolated_db):
    uid = "kg_persist_user"
    ensure_user(uid)

    episode_id = add_episode(uid, "Rozmowa o Pythonie", meta={"intent": "chat"})
    fact_id = add_fact(
        uid,
        "Python wspiera async/await",
        tags=["tech"],
        meta={"source_episode": episode_id},
    )

    node_row = fetch_one("SELECT id, node_type, user_id FROM knowledge_nodes WHERE id=?", (fact_id,))
    assert node_row is not None
    assert node_row["node_type"] == "fact"
    assert node_row["user_id"] == uid

    edge_rows = fetch_all(
        "SELECT relation, source, target FROM knowledge_edges WHERE target=?",
        (fact_id,),
    )
    relations = {r["relation"] for r in edge_rows}
    assert "user_fact" in relations
    assert "episode_fact" in relations


def test_kg_load_from_db(isolated_db):
    uid = "kg_reload_user"
    ensure_user(uid)

    fact_id = add_fact(uid, "Fakt do reloadu KG", tags=["test"], meta={})

    import aihub.knowledge_graph as kg_mod

    kg_mod._graph.nodes.clear()
    kg_mod._graph.edges.clear()
    kg_mod._graph.relation_index.clear()

    assert fact_id not in kg_mod._graph.nodes

    kg_mod.load_from_db()

    assert fact_id in kg_mod._graph.nodes
