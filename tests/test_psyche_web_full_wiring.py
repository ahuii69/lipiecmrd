from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_psyche_runtime_http_surfaces(client):
    user_id = "psyche_full_wire"
    r1 = client.get(f"/psyche/runtime/{user_id}")
    assert r1.status_code == 200, r1.text
    data = r1.json()
    assert data["ok"] is True
    assert data["psyche_v1"]["user_id"] == user_id
    assert "behavior_context" in data
    assert "psyche_v2_snapshot" in data

    r2 = client.get(f"/psyche/v2/runtime/{user_id}")
    assert r2.status_code == 200, r2.text
    assert r2.json()["ok"] is True


@pytest.mark.anyio
async def test_web_service_adapter_delegates_to_canonical(monkeypatch, isolated_db):
    import aihub.web.service as ws

    async def fake_ingest(user_id, url, *, importance=0.6, confidence=0.72, session_id=None):
        return {
            "ok": True,
            "fetch": {"url": url, "title": "Example", "text": "Example text"},
            "memory_ids": {"v1_fact": "l2-1", "v2_item": "memv2-1"},
            "user_id": user_id,
            "importance": importance,
            "confidence": confidence,
            "session_id": session_id,
        }

    monkeypatch.setattr(ws, "ingest_url_async", fake_ingest)
    out = await ws.ingest_url_for_user("web_adapter_user", "https://example.com", importance=0.8)
    assert out["ok"] is True
    assert out["memory_ids"]["v2_item"] == "memv2-1"


def test_web_http_fetch_research_ingest_and_psyche_events(client, monkeypatch, isolated_db):
    async def fake_fetch(user_id, url):
        assert user_id == "web_full_user"
        return {
            "ok": True,
            "url": url,
            "status": 200,
            "headers": {"content-type": "text/html"},
            "content_type": "text/html",
            "bytes": 128,
            "title": "Example Domain",
            "text": "Example Domain text from mocked canonical web fetch.",
            "links": [],
            "extraction": "html_stdlib",
            "redirects_followed": 0,
        }

    async def fake_ingest(user_id, url, *, importance=0.6, confidence=0.72, session_id=None):
        return {
            "ok": True,
            "fetch": await fake_fetch(user_id, url),
            "memory_ids": {"v1_fact": "fact-1", "v2_item": "memv2-1"},
        }

    async def fake_research(user_id, query, research_type="general"):
        return {
            "ok": True,
            "user_id": user_id,
            "query": query,
            "type": research_type,
            "results": [{"title": "R", "url": "https://example.com", "relevance": 1.0}],
            "total_results": 1,
            "total_facts": 1,
        }

    monkeypatch.setattr("aihub.main.fetch_url", fake_fetch)
    monkeypatch.setattr("aihub.main.ingest_web_url", fake_ingest)
    monkeypatch.setattr("aihub.research_engine.research", fake_research)

    fetch_resp = client.post(
        "/web/fetch?user_id=web_full_user",
        json={"url": "https://example.com"},
    )
    assert fetch_resp.status_code == 200, fetch_resp.text
    assert fetch_resp.json()["title"] == "Example Domain"

    ingest_resp = client.post(
        "/web/ingest?user_id=web_full_user",
        json={"url": "https://example.com", "importance": 0.7},
    )
    assert ingest_resp.status_code == 200, ingest_resp.text
    assert ingest_resp.json()["memory_ids"]["v2_item"] == "memv2-1"

    research_resp = client.post(
        "/web/research?user_id=web_full_user",
        json={"query": "example", "research_type": "general"},
    )
    assert research_resp.status_code == 200, research_resp.text
    assert research_resp.json()["total_results"] == 1

    psyche_hist = client.get("/psyche/v2/history/web_full_user?limit=20")
    assert psyche_hist.status_code == 200, psyche_hist.text
    events = psyche_hist.json()
    assert any(e["event_type"] == "web_research_triggered" for e in events)
    assert any(e["event_type"] == "tool_success" for e in events)


def test_web_health_and_ops_capabilities_include_web_psyche(client):
    wh = client.get("/web/health")
    assert wh.status_code == 200, wh.text
    assert wh.json()["fetch"]["enabled"] is True

    ops = client.get("/ops/capabilities")
    assert ops.status_code == 200, ops.text
    caps = ops.json()["capabilities"]
    assert "web" in caps
    assert "psyche" in caps
