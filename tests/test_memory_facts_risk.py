"""Tests for memory_facts RISK fix — verifies GC operates on memory_nodes without errors."""

# pylint: disable=W0613,W0212

import logging
import uuid

from aihub.db import exec_one, fetch_all, fetch_one, now_ts
from aihub.knowledge_evolution import KnowledgeEvolution
from aihub.memory_gc import MemoryGC


def _insert_test_fact(
    user_id: str,
    layer: str = "L1",
    importance: float = 0.3,
    confidence: float = 0.3,
    age_days: int = 0,
) -> str:
    """Insert a test fact into memory_nodes and return its id."""
    fact_id = uuid.uuid4().hex
    ts = now_ts() - (age_days * 86400)
    exec_one(
        """INSERT INTO memory_nodes (id, user_id, layer, content, tags, meta, ts, importance, confidence, deleted)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            fact_id,
            user_id,
            layer,
            f"test fact {fact_id[:8]}",
            "[]",
            "{}",
            ts,
            importance,
            confidence,
        ),
    )
    return fact_id


def test_memory_facts_view_exists(isolated_db):
    """After init_db, the memory_facts VIEW must exist as a compatibility layer."""
    row = fetch_one(
        "SELECT count(*) as cnt FROM sqlite_master WHERE type='view' AND name='memory_facts'"
    )
    assert row is not None, "Query should return a result"
    assert row["cnt"] == 1, "memory_facts VIEW should exist after init_db"


def test_memory_meta_table_exists(isolated_db):
    """After init_db, memory_meta table must exist (cold-start safe)."""
    row = fetch_one(
        "SELECT count(*) as cnt FROM sqlite_master WHERE type='table' AND name='memory_meta'"
    )
    assert row is not None, "Query should return a result"
    assert row["cnt"] == 1, "memory_meta TABLE should exist after init_db"


def test_memory_facts_view_readable(isolated_db):
    """The memory_facts VIEW should be queryable and return data from memory_nodes."""
    uid = "test_view_user"
    fid = _insert_test_fact(uid, layer="L1", importance=0.5, confidence=0.5)

    rows = fetch_all("SELECT * FROM memory_facts WHERE user_id=?", (uid,))
    assert len(rows) == 1
    assert rows[0]["id"] == fid
    # VIEW aliases ts → created_ts
    assert rows[0]["created_ts"] is not None


def test_gc_get_fact_count_no_error(isolated_db):
    """_get_fact_count must work without 'no such table' error."""
    gc = MemoryGC()
    uid = "count_test_user"

    count = gc._get_fact_count(uid)
    assert count == 0

    _insert_test_fact(uid)
    _insert_test_fact(uid)
    count = gc._get_fact_count(uid)
    assert count == 2


def test_gc_archive_old_facts_no_error(isolated_db):
    """_archive_old_facts must archive facts without 'no such table' error."""
    gc = MemoryGC()
    uid = "archive_test_user"

    # Insert old facts (40 days old, past 30-day threshold)
    _insert_test_fact(uid, layer="L1", age_days=40)
    _insert_test_fact(uid, layer="L2", age_days=40)
    # Insert fresh fact
    _insert_test_fact(uid, layer="L1", age_days=1)

    archived = gc._archive_old_facts(uid)
    assert archived == 2, f"Expected 2 archived, got {archived}"

    # Verify layers changed
    rows = fetch_all(
        "SELECT layer FROM memory_nodes WHERE user_id=? AND deleted=0 ORDER BY layer",
        (uid,),
    )
    layers = [r["layer"] for r in rows]
    assert layers.count("L3_archive") == 2
    assert layers.count("L1") == 1


def test_gc_delete_fact_soft_deletes(isolated_db):
    """_delete_fact must soft-delete (set deleted=1), not hard-delete."""
    gc = MemoryGC()
    uid = "delete_test_user"
    fid = _insert_test_fact(uid)

    gc._delete_fact(uid, fid)

    row = fetch_one("SELECT deleted FROM memory_nodes WHERE id=?", (fid,))
    assert row is not None, "Row should still exist (soft delete)"
    assert row["deleted"] == 1


def test_gc_remove_low_priority(isolated_db):
    """_remove_low_priority_facts must work on memory_nodes."""
    gc = MemoryGC()
    uid = "lowprio_user"

    _insert_test_fact(uid, importance=0.1, confidence=0.1)
    _insert_test_fact(uid, importance=0.2, confidence=0.2)
    _insert_test_fact(uid, importance=0.9, confidence=0.9)

    removed = gc._remove_low_priority_facts(uid, 2)
    assert removed == 2

    active = fetch_all(
        "SELECT importance FROM memory_nodes WHERE user_id=? AND deleted=0", (uid,)
    )
    assert len(active) == 1
    assert float(active[0]["importance"]) == 0.9


def test_archive_stale_no_error(isolated_db):
    """archive_stale in knowledge_evolution must run without errors."""
    ke = KnowledgeEvolution()
    uid = "stale_test_user"

    # Insert stale facts (100 days old, low importance)
    _insert_test_fact(uid, layer="L1", importance=0.2, confidence=0.2, age_days=100)
    _insert_test_fact(uid, layer="L2", importance=0.3, confidence=0.3, age_days=100)
    # Fresh fact should not be archived
    _insert_test_fact(uid, layer="L1", importance=0.2, confidence=0.2, age_days=1)

    result = ke.archive_stale(uid, days=90)
    assert result["ok"] is True
    assert result["archived"] == 2


def test_gc_collect_no_table_error(isolated_db, caplog):
    """Full GC cycle must complete without 'no such table' for any runtime table."""
    gc = MemoryGC()
    uid = "gc_full_test"

    _insert_test_fact(uid, layer="L1", importance=0.5, confidence=0.5, age_days=5)

    with caplog.at_level(logging.DEBUG):
        stats = gc.collect_garbage(uid)

    assert "error" not in stats, f"GC returned error: {stats.get('error')}"
    # Critical: no 'no such table' for ANY runtime table
    for record in caplog.records:
        msg = record.getMessage().lower()
        assert "no such table" not in msg, (
            f"Found 'no such table' in logs: {record.getMessage()}"
        )


def test_evolve_all_no_error(isolated_db):
    """evolve_all must complete cleanly with memory_nodes schema."""
    ke = KnowledgeEvolution()
    uid = "evolve_test"

    _insert_test_fact(uid, layer="L1", importance=0.8, confidence=0.8)
    _insert_test_fact(uid, layer="L2", importance=0.8, confidence=0.8)

    result = ke.evolve_all(uid)
    assert result["ok"] is True
    assert "error" not in result
