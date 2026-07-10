"""WIREUP SPRINT — tests for all newly wired modules.

FAZA 1: Vector dense boost in retrieve_context
FAZA 2: Psyche modulation + learning throttle
FAZA 5: Knowledge graph feeding from add_fact / add_episode
FAZA 6: Attention filtering in agent_tick
"""

import asyncio
from unittest.mock import patch

from aihub.psyche_engine import ensure_user

# ---------------------------------------------------------------------------
# FAZA 2: Psyche modulation
# ---------------------------------------------------------------------------


class TestPsycheModulation:
    """Psyche state affects importance/confidence scoring."""

    def test_get_psyche_modulation_defaults(self, isolated_db):
        """No psyche state → neutral modulation."""
        from aihub.memory_engine import _get_psyche_modulation

        mod = _get_psyche_modulation("no_such_user_xyz")
        assert mod["imp_mod"] == 0.0
        assert mod["conf_mod"] == 0.0
        assert mod["max_facts"] == 3

    def test_get_psyche_modulation_returns_dict(self, isolated_db):
        """Existing user → modulation dict with valid keys."""
        uid = "pmod_user"
        ensure_user(uid)
        from aihub.memory_engine import _get_psyche_modulation

        mod = _get_psyche_modulation(uid)
        assert "imp_mod" in mod
        assert "conf_mod" in mod
        assert "max_facts" in mod
        assert 1 <= mod["max_facts"] <= 3

    def test_low_energy_reduces_max_facts(self, isolated_db):
        """Low energy → max_facts=1."""
        uid = "low_energy_user"
        ensure_user(uid)
        from aihub.db import upsert_psyche

        upsert_psyche(uid, 0.5, 0.20, 0.5, "ziomek", 0.65, {})
        from aihub.memory_engine import _get_psyche_modulation

        mod = _get_psyche_modulation(uid)
        assert mod["max_facts"] == 1

    def test_high_focus_allows_3_facts(self, isolated_db):
        """High focus/energy → max_facts=3."""
        uid = "high_focus_user"
        ensure_user(uid)
        from aihub.db import upsert_psyche

        upsert_psyche(uid, 0.6, 0.80, 0.80, "ziomek", 0.65, {})
        from aihub.memory_engine import _get_psyche_modulation

        mod = _get_psyche_modulation(uid)
        assert mod["max_facts"] == 3

    def test_importance_with_psyche_mod(self, isolated_db):
        """Psyche modulation shifts importance score."""
        from aihub.memory_engine import _importance_from_text

        base = _importance_from_text("normalny tekst")
        boosted = _importance_from_text("normalny tekst", psyche_mod=0.05)
        reduced = _importance_from_text("normalny tekst", psyche_mod=-0.05)
        assert boosted > base
        assert reduced < base

    def test_confidence_with_psyche_mod(self, isolated_db):
        """Psyche modulation shifts confidence score."""
        from aihub.memory_engine import _confidence_from_text

        base = _confidence_from_text("jestem programistą")
        boosted = _confidence_from_text("jestem programistą", psyche_mod=0.05)
        reduced = _confidence_from_text("jestem programistą", psyche_mod=-0.05)
        assert boosted > base
        assert reduced < base

    def test_process_turn_respects_throttle(self, isolated_db):
        """Low energy psyche → fewer facts extracted per turn."""
        uid = "throttle_user"
        ensure_user(uid)
        from aihub.db import upsert_psyche

        upsert_psyche(uid, 0.5, 0.20, 0.5, "ziomek", 0.65, {})

        from aihub.memory_engine import process_turn

        result = process_turn(
            uid,
            "Jestem programistą Python, lubię Pythona, mam kota",
            "Fajnie!",
            "chat",
            {},
        )
        # max_facts=1 with low energy, so at most 1 fact
        assert len(result["fact_ids"]) <= 1


# ---------------------------------------------------------------------------
# FAZA 5: Knowledge graph feeding
# ---------------------------------------------------------------------------


class TestKnowledgeGraphFeeding:
    """add_fact / add_episode → knowledge_graph.add_node."""

    def test_add_fact_feeds_knowledge_graph(self, isolated_db):
        """Adding a fact calls knowledge_graph.add_node."""
        uid = "kg_fact_user"
        ensure_user(uid)
        from aihub import knowledge_graph
        from aihub.memory_engine import add_fact

        before = knowledge_graph.stats()["nodes"]
        add_fact(uid, "Python jest super", tags=["tech"], meta={"source": "test"})
        after = knowledge_graph.stats()["nodes"]
        assert after >= before + 1

    def test_add_episode_feeds_knowledge_graph(self, isolated_db):
        """Adding an episode calls knowledge_graph.add_node."""
        uid = "kg_ep_user"
        ensure_user(uid)
        from aihub import knowledge_graph
        from aihub.memory_engine import add_episode

        before = knowledge_graph.stats()["nodes"]
        add_episode(uid, "Rozmowa o programowaniu", meta={"intent": "chat"})
        after = knowledge_graph.stats()["nodes"]
        assert after >= before + 1

    def test_knowledge_graph_node_type_fact(self, isolated_db):
        """Fact node → node_type=='fact'."""
        uid = "kg_type_user"
        ensure_user(uid)
        from aihub.memory_engine import _id_for, add_fact

        node_id = _id_for("Unikalny fakt testowy", uid, "L2")
        add_fact(uid, "Unikalny fakt testowy", tags=["test"], meta={})

        from aihub import knowledge_graph

        node = knowledge_graph._graph.nodes.get(node_id)
        assert node is not None
        assert node.node_type == "fact"

    def test_knowledge_graph_node_type_episode(self, isolated_db):
        """Episode node → node_type=='episode'."""
        uid = "kg_type_ep_user"
        ensure_user(uid)
        from aihub.memory_engine import _id_for, add_episode

        content = "Unikalny epizod testowy"
        node_id = _id_for(content, uid, "L1")
        add_episode(uid, content, meta={"intent": "test"})

        from aihub import knowledge_graph

        node = knowledge_graph._graph.nodes.get(node_id)
        assert node is not None
        assert node.node_type == "episode"

    def test_knowledge_graph_failure_is_silent(self, isolated_db):
        """If KG import fails, add_fact still works."""
        uid = "kg_fail_user"
        ensure_user(uid)
        from aihub.memory_engine import add_fact

        with patch(
            "aihub.memory_engine._feed_knowledge_graph",
            side_effect=RuntimeError("boom"),
        ):
            # _feed_knowledge_graph is wrapped in try/except so this
            # patch of the function itself raising won't be caught inside it.
            # Instead patch the import path
            pass

        # Simpler approach: just ensure add_fact succeeds even if KG is broken
        with patch("aihub.knowledge_graph.add_node", side_effect=Exception("broken")):
            nid = add_fact(
                uid, "Fakt mimo błędu KG", tags=["test"], meta={"source": "test"}
            )
            assert nid  # fact was still created


# ---------------------------------------------------------------------------
# FAZA 1: Vector dense boost
# ---------------------------------------------------------------------------


class TestVectorDenseBoost:
    """retrieve_context includes dense_hits field."""

    def test_retrieve_context_has_dense_hits_key(self, isolated_db):
        """Response always includes dense_hits (may be empty)."""
        uid = "dense_user"
        ensure_user(uid)
        from aihub.memory_engine import retrieve_context

        result = retrieve_context(uid, "python", limit=5)
        assert "dense_hits" in result
        assert isinstance(result["dense_hits"], list)

    def test_retrieve_context_dense_hits_populated(self, isolated_db):
        """When vector_engine has data, dense_hits are populated."""
        uid = "dense_pop_user"
        ensure_user(uid)

        fake_results = {
            "ok": True,
            "results": [
                {"text": "Python info", "similarity": 0.8, "distance": 0.2, "index": 0}
            ],
            "total_vectors": 1,
        }
        with patch("aihub.vector_engine.search", return_value=fake_results):
            from aihub.memory_engine import retrieve_context

            result = retrieve_context(uid, "python", limit=5)
            assert len(result["dense_hits"]) >= 1
            assert result["dense_hits"][0]["similarity"] > 0.3

    def test_retrieve_context_dense_low_similarity_filtered(self, isolated_db):
        """Low similarity results are filtered out."""
        uid = "dense_filt_user"
        ensure_user(uid)

        fake_results = {
            "ok": True,
            "results": [
                {"text": "Irrelevant", "similarity": 0.1, "distance": 5.0, "index": 0}
            ],
            "total_vectors": 1,
        }
        with patch("aihub.vector_engine.search", return_value=fake_results):
            from aihub.memory_engine import retrieve_context

            result = retrieve_context(uid, "python", limit=5)
            assert len(result["dense_hits"]) == 0


# ---------------------------------------------------------------------------
# FAZA 6: Attention filtering in agent_tick
# ---------------------------------------------------------------------------


class TestAttentionFiltering:
    """agent_tick uses attention_controller for large message batches."""

    def test_small_batch_no_filtering(self, isolated_db):
        """< ATTENTION_THRESHOLD messages → no filtering applied."""
        uid = "att_small_user"
        ensure_user(uid)

        # Create a few STM msgs
        from aihub.memory_engine import add_stm

        for i in range(5):
            add_stm(uid, "user", f"wiadomość {i}", {})

        from aihub.agent_engine import agent_tick

        # With only 5 msgs (< 20 threshold), agent_tick processes them all
        result = asyncio.get_event_loop().run_until_complete(agent_tick(uid))
        assert result["ok"]
        assert result["processed"] == 5

    def test_large_batch_triggers_filtering(self, isolated_db):
        """> ATTENTION_THRESHOLD messages → attention_controller.rank_messages is called."""
        uid = "att_large_user"
        ensure_user(uid)

        # Create 25 STM msgs
        from aihub.memory_engine import add_stm

        for i in range(25):
            add_stm(uid, "user", f"wiadomość numer {i}", {})

        from aihub.attention_controller import AttentionRanking

        calls = []

        # Mock rank_messages to return all messages with scores
        def mock_rank(user_id, messages):
            calls.append(len(messages))
            return [
                AttentionRanking(
                    message=m,
                    score=0.5,
                    category="relevant",
                    urgency=0.3,
                    relevance=0.6,
                )
                for m in messages
            ]

        from aihub.agent_engine import agent_tick

        with patch("aihub.attention_controller.rank_messages", side_effect=mock_rank):
            result = asyncio.get_event_loop().run_until_complete(agent_tick(uid))
            assert result["ok"]
            # rank_messages was called
            assert len(calls) == 1
            # processed should be capped at ATTENTION_THRESHOLD (20)
            assert result["processed"] <= 20


# ---------------------------------------------------------------------------
# FAZA 3+4: Research + GC verified (integration)
# ---------------------------------------------------------------------------


class TestResearchGCIntegration:
    """Verify research and GC wiring is still intact."""

    def test_research_query_task_type_exists(self, isolated_db):
        """plan_from_text creates research.query tasks."""
        from aihub.agent_engine import plan_from_text

        tasks = plan_from_text("u1", "wyszukaj informacje o Pythonie")
        types = [t["type"] for t in tasks]
        assert "research.query" in types

    def test_maybe_gc_callable(self, isolated_db):
        """_maybe_gc runs without error even with empty DB."""
        uid = "gc_test_user"
        ensure_user(uid)
        from aihub.agent_engine import _maybe_gc

        result = _maybe_gc(uid)
        assert result is False  # no pressure with empty DB

    def test_evolve_all_callable(self, isolated_db):
        """knowledge_evolution.evolve_all callable (wired from GC)."""
        uid = "evo_test_user"
        ensure_user(uid)
        from aihub.knowledge_evolution import evolve_all

        result = evolve_all(uid)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Regression: existing contracts unchanged
# ---------------------------------------------------------------------------


class TestContractRegression:
    """Endpoint contracts remain unchanged."""

    def test_retrieve_context_has_required_keys(self, isolated_db):
        """retrieve_context returns all required keys."""
        uid = "contract_user"
        ensure_user(uid)
        from aihub.memory_engine import retrieve_context

        r = retrieve_context(uid, "test", limit=5)
        for key in ("user_id", "query", "stm", "episodic", "semantic", "total"):
            assert key in r, f"Missing key: {key}"

    def test_process_turn_returns_expected_keys(self, isolated_db):
        """process_turn response shape unchanged."""
        uid = "contract_pt_user"
        ensure_user(uid)
        from aihub.memory_engine import process_turn

        r = process_turn(uid, "cześć", "hej!", "chat", {})
        for key in ("stm_ids", "episode_id", "fact_ids", "ts"):
            assert key in r, f"Missing key: {key}"
        assert isinstance(r["stm_ids"], list)
        assert isinstance(r["fact_ids"], list)

    def test_add_fact_returns_node_id(self, isolated_db):
        """add_fact returns a hex node_id string."""
        uid = "contract_af_user"
        ensure_user(uid)
        from aihub.memory_engine import add_fact

        nid = add_fact(uid, "fakt testowy", tags=["test"], meta={})
        assert isinstance(nid, str)
        assert len(nid) == 64  # sha256 hex

    def test_add_episode_returns_node_id(self, isolated_db):
        """add_episode returns a hex node_id string."""
        uid = "contract_ae_user"
        ensure_user(uid)
        from aihub.memory_engine import add_episode

        nid = add_episode(uid, "epizod testowy", meta={"intent": "test"})
        assert isinstance(nid, str)
        assert len(nid) == 64
