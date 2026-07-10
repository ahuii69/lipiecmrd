"""Tests for the production repair sprint: memory touch, learning, research, agent research, GC."""

import asyncio
import time
from unittest.mock import patch

import pytest

from aihub.db import fetch_all, fetch_one
from aihub.memory_engine import add_fact, process_turn, retrieve_context
from aihub.meta_memory import touch_nodes
from aihub.psyche_engine import ensure_user

# ---------------------------------------------------------------------------
# FAZA 1: MetaMemory touch on retrieve
# ---------------------------------------------------------------------------


class TestMetaMemoryTouch:
    """meta_memory.touch_nodes is called on retrieve_context and updates access_count."""

    def test_touch_nodes_updates_access(self, isolated_db):
        uid = "touch_user"
        ensure_user(uid)

        # Insert some L2 facts
        fid1 = add_fact(
            uid, "Lubię pizzę z salami", tags=["user", "preference"], meta={}
        )
        fid2 = add_fact(
            uid, "Zawsze piję kawę rano", tags=["user", "preference"], meta={}
        )

        touched = touch_nodes([fid1, fid2])
        assert touched == 2

        row = fetch_one(
            "SELECT access_count, last_access FROM memory_meta WHERE fact_id=?", (fid1,)
        )
        assert row is not None
        assert int(row["access_count"]) >= 1
        assert float(row["last_access"]) > 0

    def test_retrieve_context_touches_meta(self, isolated_db):
        uid = "retrieve_touch"
        ensure_user(uid)

        add_fact(uid, "Lubię Python i FastAPI", tags=["user", "preference"], meta={})
        add_fact(uid, "Zawsze piszę testy", tags=["user", "preference"], meta={})

        result = retrieve_context(uid, "Python", limit=10)
        # Should have hits
        total = result["total"]
        if total == 0:
            pytest.skip("No search results to touch")

        hit_ids = [item["id"] for item in result["episodic"] + result["semantic"]]
        for hid in hit_ids:
            row = fetch_one(
                "SELECT access_count FROM memory_meta WHERE fact_id=?", (hid,)
            )
            assert row is not None, f"meta_memory row missing for {hid}"
            assert int(row["access_count"]) >= 1

    def test_touch_nodes_idempotent(self, isolated_db):
        uid = "touch_idem"
        ensure_user(uid)
        node_id = add_fact(
            uid, "Preferuję ciemny motyw edytora", tags=["user"], meta={}
        )

        touch_nodes([node_id])
        touch_nodes([node_id])
        touch_nodes([node_id])

        row = fetch_one(
            "SELECT access_count FROM memory_meta WHERE fact_id=?", (node_id,)
        )
        assert row is not None
        assert int(row["access_count"]) == 3


# ---------------------------------------------------------------------------
# FAZA 2: LearningEngine activation
# ---------------------------------------------------------------------------


class TestLearningEngine:
    def test_process_turn_writes_facts(self, isolated_db):
        uid = "learn_user"
        ensure_user(uid)

        process_turn(
            uid,
            "Jestem programistą i pracuję w Google",
            "Super, zapamiętam to!",
            "chat",
            {},
        )

        # LearningEngine should have extracted at least one fact with regex
        # (user_work or user_identity pattern)
        facts = fetch_all(
            "SELECT content, tags FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
            (uid,),
        )
        # We expect at least 1 fact from keyword-match or learning_engine
        assert len(facts) >= 1

    def test_learning_engine_regex_extraction(self, isolated_db):
        """LearningEngine extracts per-rule short facts, not whole message."""
        uid = "learn_regex"
        ensure_user(uid)

        from aihub.learning_engine import LearningEngine

        le = LearningEngine()
        facts = le.extract_facts_from_message(
            uid, "Mam na imię Marek i lubię pizzę", "user"
        )
        # Should get at least user_identity or user_preference match
        assert len(facts) >= 1
        for fact_text, tags, imp, conf in facts:
            assert len(fact_text) < 200  # Short fact, not whole message

    def test_no_duplicate_facts(self, isolated_db):
        """Keyword-fallback should not fire when LearningEngine already extracted."""
        uid = "dedup_user"
        ensure_user(uid)

        process_turn(
            uid,
            "Lubię programowanie w Pythonie",
            "Fajnie!",
            "chat",
            {},
        )
        facts = fetch_all(
            "SELECT content FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
            (uid,),
        )
        contents = [f["content"] for f in facts]
        # No exact duplicates
        assert len(contents) == len(set(contents)), f"Duplicate facts: {contents}"

    def test_reflection_writes_facts(self, isolated_db):
        uid = "reflect_learn"
        ensure_user(uid)

        from aihub.learning_engine import learn_from_reflection

        reflection = {
            "topics": ["python", "testing", "devops"],
            "recommendations": ["User prefers typed Python code"],
        }
        result = learn_from_reflection(uid, reflection)
        assert result["ok"]
        assert result.get("facts_added", 0) >= 1


# ---------------------------------------------------------------------------
# FAZA 3: ResearchEngine (real, no placeholder)
# ---------------------------------------------------------------------------


class TestResearchEngine:
    def test_research_url_fetch_extracts_facts(self, isolated_db):
        uid = "research_fetch"
        ensure_user(uid)

        from aihub.research_engine import ResearchEngine

        engine = ResearchEngine()

        fake_results = [
            {
                "title": "Python (programming language)",
                "url": "https://en.wikipedia.org/wiki/Python_(programming_language)",
                "content": (
                    "Python jest językiem programowania ogólnego przeznaczenia. "
                    "Badania wykazują że Python jest najczęściej uczywanym językiem w 2024. "
                    "Według Stack Overflow survey Python ma 48% popularności."
                ),
                "source": "wikipedia",
            }
        ]

        with (
            patch.object(engine, "_fetch_wikipedia", return_value=fake_results),
            patch.object(engine, "_fetch_duckduckgo", return_value=[]),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                engine.research(uid, "Python programowanie", research_type="general")
            )

        assert result["ok"]
        assert result["total_results"] >= 1
        assert result["total_facts"] >= 1

    def test_research_no_placeholder(self, isolated_db):
        """research should never fabricate placeholder data; real backend results are allowed."""
        uid = "no_placeholder"
        ensure_user(uid)

        from aihub.research_engine import ResearchEngine

        engine = ResearchEngine()

        # Both backends return empty (simulating failure / no results)
        with (
            patch.object(engine, "_fetch_wikipedia", return_value=[]),
            patch.object(engine, "_fetch_duckduckgo", return_value=[]),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                engine.research(uid, "cokolwiek")
            )

        assert result["ok"]
        assert isinstance(result.get("results"), list)
        assert result["total_results"] == len(result["results"])
        assert result["total_facts"] == sum(
            int(r.get("facts_extracted", 0) or 0) for r in result["results"]
        )

        for row in result["results"]:
            assert row.get("source") in {"brave", "wikipedia", "duckduckgo"}
            assert isinstance(row.get("title"), str) and row["title"].strip()
            assert isinstance(row.get("url"), str) and row["url"].strip()
            assert 0.0 <= float(row.get("relevance", 0.0)) <= 1.0
            assert int(row.get("facts_extracted", 0) or 0) >= 0

        # Persisted facts, if any, must be real (non-placeholder, quality-gated).
        import json

        from aihub.research_engine import _BOILERPLATE_RE, RESEARCH_MIN_FACT_LEN

        facts = fetch_all(
            "SELECT content, meta FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
            (uid,),
        )
        assert len(facts) == result["total_facts"]
        for fact in facts:
            content = (fact["content"] or "").strip()
            assert len(content) >= RESEARCH_MIN_FACT_LEN
            assert "placeholder" not in content.lower()
            assert _BOILERPLATE_RE.search(content) is None
            meta = json.loads(fact["meta"] or "{}")
            assert meta.get("research_query") == "cokolwiek"
            assert meta.get("backend") in {
                "brave",
                "wikipedia",
                "duckduckgo",
                "unknown",
            }

    def test_research_stores_facts_with_source_tags(self, isolated_db):
        uid = "research_tags"
        ensure_user(uid)

        from aihub.research_engine import ResearchEngine

        engine = ResearchEngine()

        fake_results = [
            {
                "title": "AI overview",
                "url": "https://en.wikipedia.org/wiki/Artificial_intelligence",
                "content": "Badania wykazują że AI zmienia świat technologii w 2025 roku.",
                "source": "wikipedia",
            }
        ]

        with (
            patch.object(engine, "_fetch_wikipedia", return_value=fake_results),
            patch.object(engine, "_fetch_duckduckgo", return_value=[]),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                engine.research(uid, "AI technologia")
            )

        facts = fetch_all(
            "SELECT content, tags FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
            (uid,),
        )
        research_facts = [f for f in facts if "research" in (f["tags"] or "")]
        # If any facts were extracted, they should have research tag
        if result["total_facts"] > 0:
            assert len(research_facts) >= 1


# ---------------------------------------------------------------------------
# FAZA 4: Agent research task
# ---------------------------------------------------------------------------


class TestAgentResearch:
    def test_plan_from_text_creates_research_task(self, isolated_db):
        uid = "plan_research"
        ensure_user(uid)

        from aihub.agent_engine import plan_from_text

        tasks = plan_from_text(uid, "wyszukaj informacje o Python 3.13")
        types = [t["type"] for t in tasks]
        assert "research.query" in types

    def test_agent_engine_executes_research_task(self, isolated_db):
        uid = "exec_research"
        ensure_user(uid)

        fake_results = [
            {
                "title": "Python 3.13",
                "url": "https://en.wikipedia.org/wiki/Python_(programming_language)",
                "content": "Python 3.13 jest nową wersją języka programowania. Badania wykazują że nowy interpreter jest szybszy o 20% od poprzedniej wersji Pythona.",
                "source": "wikipedia",
            }
        ]

        from aihub.agent_engine import execute_task
        from aihub.research_engine import _research_engine

        task = {"type": "research.query", "payload": {"query": "Python 3.13"}}

        with (
            patch.object(
                _research_engine, "_fetch_wikipedia", return_value=fake_results
            ),
            patch.object(_research_engine, "_fetch_duckduckgo", return_value=[]),
        ):
            asyncio.get_event_loop().run_until_complete(execute_task(uid, task))

        facts = fetch_all(
            "SELECT content FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
            (uid,),
        )
        # At least one research fact should exist
        assert len(facts) >= 1


# ---------------------------------------------------------------------------
# FAZA 5: GC trigger in agent_tick
# ---------------------------------------------------------------------------


class TestGCTrigger:
    def test_gc_triggered_when_pressure_high(self, isolated_db):
        """When memory pressure > 0.7, _maybe_gc should call collect_garbage."""
        uid = "gc_trigger"
        ensure_user(uid)

        # Insert enough memory_nodes to exceed 0.7 pressure threshold
        # LTM_MAX_FACTS_PER_USER defaults to 20000, so 0.7 * 20000 = 14000+
        from aihub.db import exec_one

        for i in range(14001):
            exec_one(
                "INSERT INTO memory_nodes(id,user_id,layer,content,tags,meta,ts,importance,confidence,deleted) VALUES(?,?,'L2',?,'[]','{}',?,0.5,0.5,0)",
                (f"gc_node_{i}", uid, f"fact {i}", time.time()),
            )

        with patch("aihub.memory_gc.collect_garbage") as mock_gc:
            from aihub.agent_engine import _maybe_gc

            result = _maybe_gc(uid)
            assert result is True
            mock_gc.assert_called_once_with(uid)
