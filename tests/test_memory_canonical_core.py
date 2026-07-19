"""MemoryCanonicalCore: single entry for ingest, unified retrieval, and V2 delegation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_process_turn_routes_graph_writes_through_core(isolated_db, monkeypatch):
    """memory_engine.process_turn must use ingest_stm / ingest_episode / ingest_fact on core."""
    from aihub.memory_core import MemoryCanonicalCore
    from aihub.memory_engine import process_turn
    from aihub.psyche_core import get_psyche_core

    uid = "pt_writes_core"
    get_psyche_core().ensure_user(uid)
    counts = {"stm": 0, "ep": 0, "fact": 0}

    _orig_stm = MemoryCanonicalCore.ingest_stm_message
    _orig_ep = MemoryCanonicalCore.ingest_episode
    _orig_f = MemoryCanonicalCore.ingest_fact

    def track_stm(self, *a, **k):
        counts["stm"] += 1
        return _orig_stm(self, *a, **k)

    def track_ep(self, *a, **k):
        counts["ep"] += 1
        return _orig_ep(self, *a, **k)

    def track_f(self, *a, **k):
        counts["fact"] += 1
        return _orig_f(self, *a, **k)

    monkeypatch.setattr(MemoryCanonicalCore, "ingest_stm_message", track_stm)
    monkeypatch.setattr(MemoryCanonicalCore, "ingest_episode", track_ep)
    monkeypatch.setattr(MemoryCanonicalCore, "ingest_fact", track_f)

    process_turn(uid, "lubię testy", "super", "chat", {})
    assert counts["stm"] == 2
    assert counts["ep"] == 1
    assert counts["fact"] >= 1


def test_process_turn_persists_explicit_project_fact(isolated_db):
    from aihub.db import fetch_all
    from aihub.memory_engine import process_turn
    from aihub.psyche_core import get_psyche_core

    uid = "explicit_project_fact_user"
    get_psyche_core().ensure_user(uid)

    process_turn(
        uid,
        "Zapamiętaj ważny fakt: testowe hasło projektu to orzel-77.",
        "Zapisane.",
        "learn",
        {},
    )

    rows = fetch_all(
        """
        SELECT content FROM memory_nodes
        WHERE user_id=? AND layer='L2' AND deleted=0
        ORDER BY ts DESC
        """,
        (uid,),
    )
    texts = [str(r["content"]) for r in rows]
    assert any("orzel-77" in t for t in texts)


def test_add_fact_indexes_active_vector_store(isolated_db):
    import json
    from pathlib import Path

    from aihub.memory_core import get_memory_core
    from aihub.psyche_core import get_psyche_core

    uid = "fact_vector_index_user"
    get_psyche_core().ensure_user(uid)
    fact = "testowe hasło projektu to orzel-88"
    get_memory_core().ingest_fact(uid, fact, tags=["fact"], meta={})

    from aihub.vector_engine import VECTOR_META_PATH

    meta = json.loads(Path(VECTOR_META_PATH).read_text())
    assert any(
        isinstance(item, dict)
        and item.get("user_id") == uid
        and item.get("text") == fact
        for item in meta
    )


def test_ingest_fact_mirrors_into_memory_v2(isolated_db):
    from aihub.db import fetch_one
    from aihub.memory_core import get_memory_core
    from aihub.psyche_core import get_psyche_core

    uid = "fact_v2_mirror_user"
    fact = "mój ulubiony framework to FastAPI"
    get_psyche_core().ensure_user(uid)
    node_id = get_memory_core().ingest_fact(
        uid, fact, tags=["user", "preference"], meta={}
    )

    row = fetch_one(
        "SELECT content, memory_type, source_ref FROM memory_v2_items WHERE user_id=? AND content=?",
        (uid, fact),
    )
    assert row is not None
    assert row["memory_type"] == "preference"
    assert row["source_ref"] == node_id


def test_duplicate_fact_still_mirrors_memory_v2(isolated_db):
    """Duplicate L2 skip must still ensure a V2 row for the matched node."""
    from aihub.db import fetch_all
    from aihub.memory_core import get_memory_core
    from aihub.psyche_core import get_psyche_core

    uid = "fact_v2_dup_mirror"
    fact = "użytkownik pije tylko zieloną herbatę o poranku"
    get_psyche_core().ensure_user(uid)
    core = get_memory_core()
    first = core.ingest_fact(uid, fact, tags=["preference"], meta={})
    second = core.ingest_fact(uid, fact, tags=["preference"], meta={})
    assert first == second

    rows = fetch_all(
        "SELECT id, source_ref, content FROM memory_v2_items WHERE user_id=? AND source_ref=?",
        (uid, first),
    )
    assert len(rows) >= 1
    assert rows[0]["content"] == fact


def test_retrieve_unified_extends_v1_with_v2_fields(isolated_db, monkeypatch):
    from aihub.memory_core import MemoryCanonicalCore, get_memory_core
    from aihub.psyche_core import get_psyche_core

    uid = "core_unified_user"
    get_psyche_core().ensure_user(uid)
    core = MemoryCanonicalCore()
    out = core.retrieve_unified(uid, "anything", limit=5)
    assert "user_id" in out and out["user_id"] == uid
    assert "stm" in out and "episodic" in out and "semantic" in out
    assert "memory_v2_items" in out
    assert "memory_v2_total" in out
    assert isinstance(out["memory_v2_items"], list)
    assert isinstance(out["memory_v2_total"], int)
    assert get_memory_core() is get_memory_core()


def test_ingest_turn_matches_direct_process_turn(isolated_db, monkeypatch):
    from aihub.memory_core import MemoryCanonicalCore
    from aihub.memory_engine import process_turn
    from aihub.psyche_core import get_psyche_core

    core = MemoryCanonicalCore()
    u1 = "core_ingest_a"
    u2 = "core_ingest_b"
    get_psyche_core().ensure_user(u1)
    get_psyche_core().ensure_user(u2)
    a = core.ingest_turn(u1, "hello", "hi there", "chat", {})
    get_psyche_core().evolve(u2, "hello", "user")
    get_psyche_core().evolve(u2, "hi there", "assistant")
    b = process_turn(u2, "hello", "hi there", "chat", {})
    assert sorted(a.keys()) == sorted(b.keys())


@pytest.mark.asyncio
async def test_tool_memory_search_uses_unified_payload(isolated_db, monkeypatch):
    from aihub.memory_core import get_memory_core
    from aihub.psyche_core import get_psyche_core
    from aihub.tools.registry import ToolRegistry
    from aihub.tools.types import ToolExecutionContext

    monkeypatch.setenv("API_KEY", "")
    uid = "tool_mem_user"
    get_psyche_core().ensure_user(uid)
    reg = ToolRegistry()
    tool = reg.get("memory.search")
    ctx = ToolExecutionContext(
        user_id=uid,
        session_id="s",
        mode="chat",
        include_debug=False,
        policy_overrides={},
    )
    inp = tool.input_model.model_validate({"query": "x", "limit": 5})
    out = await tool.handler(ctx, inp)
    assert out["ok"] is True
    data = out["result"]
    assert "memory_v2_items" in data
    assert get_memory_core().retrieve_unified is not None


def test_runtime_memory_bridge_uses_same_v2_service_as_core(isolated_db, monkeypatch):
    import aihub.runtime_memory_bridge as rmb
    from aihub.memory_core import get_memory_core

    core = get_memory_core()
    rmb.build_memory_v2_runtime_snapshot("bridge_singleton_user", "q")
    assert rmb._canonical_v2() is core.v2_service


def test_legacy_http_memory_search_includes_unified_v2_fields(isolated_db, monkeypatch):
    from aihub import main
    from aihub.psyche_core import get_psyche_core

    monkeypatch.setenv("AIHUB_DISABLE_LEGACY_MEMORY_V1_HTTP", "0")
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    uid = "legacy_search_v2_fields"
    get_psyche_core().ensure_user(uid)
    with TestClient(main.app) as client:
        r = client.post(
            "/memory/search",
            json={"user_id": uid, "query": "anything", "limit": 3},
        )
    assert r.status_code == 200
    body = r.json()
    assert "memory_v2_items" in body
    assert "memory_v2_total" in body
    assert isinstance(body["memory_v2_items"], list)
    assert isinstance(body["memory_v2_total"], int)


def test_cockpit_memory_v2_panel_via_core(isolated_db, monkeypatch):
    from aihub import main
    from aihub.memory_core import get_memory_core

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    uid = "cockpit_core_panel"
    from aihub.psyche_core import get_psyche_core

    get_psyche_core().ensure_user(uid)
    direct = get_memory_core().build_cockpit_memory_v2_panel(uid)
    with TestClient(main.app) as client:
        http = client.get(f"/cockpit/memory-v2/{uid}")
    assert http.status_code == 200
    body = http.json()
    assert body["user_id"] == direct["user_id"]
    assert body["total_items"] == direct["total_items"]


def test_retrieve_unified_matches_read_memory_outcome_dict(isolated_db, monkeypatch):
    from aihub.memory_core import get_memory_core
    from aihub.memory_read_contracts import MemoryReadSpec, unified_dict_from_outcome
    from aihub.psyche_core import get_psyche_core

    uid = "read_outcome_equiv"
    get_psyche_core().ensure_user(uid)
    core = get_memory_core()
    a = core.retrieve_unified(uid, "query zzz", 4)
    b = unified_dict_from_outcome(
        core.read_memory(MemoryReadSpec.unified(uid, "query zzz", 4))
    )
    assert a == b


def test_v2_search_invokes_read_memory(isolated_db, monkeypatch):
    from aihub.memory_core import MemoryCanonicalCore, get_memory_core
    from aihub.memory_v2_models import MemoryV2SearchRequest
    from aihub.psyche_core import get_psyche_core

    get_psyche_core().ensure_user("v2_read_core")
    calls = {"n": 0}
    orig = MemoryCanonicalCore.read_memory

    def wrapped(self, spec):
        calls["n"] += 1
        return orig(self, spec)

    monkeypatch.setattr(MemoryCanonicalCore, "read_memory", wrapped)
    req = MemoryV2SearchRequest(user_id="v2_read_core", query="a", limit=2)
    get_memory_core().v2_search(req)
    assert calls["n"] == 1


def test_runtime_memory_bridge_v2_ranked_read_uses_core_v2_search(
    isolated_db, monkeypatch
):
    from aihub.memory_core import MemoryCanonicalCore
    from aihub.psyche_core import get_psyche_core

    get_psyche_core().ensure_user("rmb_v2search")
    calls = {"n": 0}
    orig = MemoryCanonicalCore.v2_search

    def wrapped(self, req):
        calls["n"] += 1
        return orig(self, req)

    monkeypatch.setattr(MemoryCanonicalCore, "v2_search", wrapped)
    import aihub.runtime_memory_bridge as rmb

    rmb.build_memory_v2_runtime_snapshot("rmb_v2search", "hello")
    assert calls["n"] >= 1


def test_retrieve_unified_rejects_empty_user_id(isolated_db):
    from aihub.memory_errors import MemoryUserIdRequiredError
    from aihub.memory_core import get_memory_core

    with pytest.raises(MemoryUserIdRequiredError):
        get_memory_core().retrieve_unified("  ", "q", 3)


def test_add_fact_raises_when_vector_write_fails(isolated_db, monkeypatch):
    from aihub.memory_errors import MemoryVectorWriteError
    from aihub.memory_engine import add_fact
    from aihub.psyche_core import get_psyche_core

    uid = "vector_fail_user"
    get_psyche_core().ensure_user(uid)

    def boom(_text, user_id=""):
        return {"ok": False, "error": "vector_operations_unavailable"}

    monkeypatch.setattr("aihub.vector_engine.add_memory", boom)

    with pytest.raises(MemoryVectorWriteError):
        add_fact(uid, "fakt testowy xyz", ["fact"], {})


def test_memory_health_endpoint_reports_active_stack(isolated_db, monkeypatch):
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    uid = "memory_health_user"
    from aihub.psyche_core import get_psyche_core

    get_psyche_core().ensure_user(uid)
    with TestClient(main.app) as client:
        resp = client.get("/memory/health", params={"user_id": uid})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == uid
    assert "layers" in body
    assert "vector" in body
    assert "embedding" in body


def test_retrieval_explanation_uses_search_path(isolated_db, monkeypatch):
    from aihub.memory_core import get_memory_core
    from aihub.memory_v2_service import MemoryV2Service
    from aihub.psyche_core import get_psyche_core

    get_psyche_core().ensure_user("expl_search")
    svc = get_memory_core().v2_service
    calls = {"n": 0}
    orig = MemoryV2Service.search

    def wrapped(self, req):
        calls["n"] += 1
        return orig(self, req)

    monkeypatch.setattr(MemoryV2Service, "search", wrapped)
    svc.get_retrieval_explanation("expl_search", "q", top_n=5)
    assert calls["n"] == 1
