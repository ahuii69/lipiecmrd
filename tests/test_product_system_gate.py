#!/usr/bin/env python3
"""
Canonical product system gate (E2E truth on one stack).

These tests prove the **default product path** works as one system:

1. Backend boots (`aihub.main:app`).
2. Observability: ``GET /cognitive/health``, ``GET /system/ping``.
3. Cockpit BFF allowlist (JSON + Python matcher) matches transport truth.
4. **Chat:** ``POST /chat/turn`` is the canonical conversational surface; ``POST /turn``
   is explicitly legacy STM (headers / 410), not interchangeable with chat JSON.
5. After a real chat turn (mocked LLM only), memory ingest + psyche evolution
   evidence appears (STM growth + trace flags).
6. Agent: ``POST /agent/run`` carries canonical surface headers; tick is secondary.
7. Debug surfaces are marked (cognitive decide when enabled).
8. ``POST /web/fetch`` works with stubbed fetch (tool/runtime web path).

Run: ``bash scripts/product_system_gate.sh`` or
``pytest -m system_gate -q`` (marker is on each test below).

This is not a replacement for the full suite; it is the **narrow production
contract** that must stay green when shipping.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aihub.agent_http_surface import (
    COGNITIVE_DEBUG_DECIDE,
    COGNITIVE_OBSERVABILITY_HEALTH,
    FLOW_RUN,
    FLOW_TICK,
    HEADER_CANONICAL_AGENT_FLOW,
    HEADER_COGNITIVE_SURFACE,
    HEADER_ENDPOINT_ROLE,
    ROLE_AGENT_CANONICAL_RUN,
    ROLE_AGENT_SECONDARY_TICK,
)
from aihub.cockpit_proxy_allowlist import (
    concrete_path_allowed,
    load_cockpit_proxy_allowlist_routes,
)


@pytest.mark.system_gate
def test_gate_import_main_app():
    import aihub.main  # noqa: F401

    assert aihub.main.app is not None


@pytest.mark.system_gate
def test_gate_system_ping_and_cognitive_health(monkeypatch):
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    monkeypatch.setattr(
        "aihub.main.knowledge_graph.stats",
        lambda: {"nodes": 0},
    )

    with TestClient(main.app) as client:
        ping = client.get("/system/ping")
        assert ping.status_code == 200

        health = client.get("/cognitive/health")
        assert health.status_code == 200
        assert (
            health.headers.get(HEADER_COGNITIVE_SURFACE)
            == COGNITIVE_OBSERVABILITY_HEALTH
        )


@pytest.mark.system_gate
def test_gate_cockpit_allowlist_matches_canonical_transport():
    routes = load_cockpit_proxy_allowlist_routes()
    assert concrete_path_allowed(routes, "POST", "/chat/turn")
    assert concrete_path_allowed(routes, "GET", "/system/ping")
    assert concrete_path_allowed(routes, "GET", "/chat/capabilities")
    assert not concrete_path_allowed(routes, "GET", "/admin/ping")
    assert not concrete_path_allowed(routes, "POST", "/openapi.json")


@pytest.mark.system_gate
def test_gate_legacy_post_turn_is_not_chat_contract(monkeypatch):
    """POST /turn returns TurnOut + deprecation headers, not ChatTurnResult."""
    from aihub import main

    monkeypatch.delenv("AIHUB_DISABLE_LEGACY_STM_TURN", raising=False)
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        r = client.post(
            "/turn",
            json={
                "user_id": "gate_legacy_turn",
                "role": "user",
                "content": "stm only",
                "meta": {},
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert "id" in body and "ts" in body
    assert "response_text" not in body
    assert "ok" not in body
    assert r.headers.get("X-AIHub-Endpoint-Role") == "legacy-stm-write"
    assert r.headers.get("X-AIHub-Canonical-Chat-Path") == "/chat/turn"


@pytest.mark.system_gate
def test_gate_legacy_post_turn_410_when_disabled(monkeypatch):
    from aihub import main

    monkeypatch.setenv("AIHUB_DISABLE_LEGACY_STM_TURN", "1")
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        r = client.post(
            "/turn",
            json={
                "user_id": "gate_legacy_off",
                "role": "user",
                "content": "x",
                "meta": {},
            },
        )
    assert r.status_code == 410
    detail = r.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("canonical_chat_path") == "/chat/turn"


@pytest.mark.system_gate
def test_gate_chat_turn_writes_stm_and_marks_psyche_trace(monkeypatch, isolated_db):
    """Full chat runtime path: mocked provider only; real ingest_turn + psyche evolve."""
    from aihub import db as db_mod
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    async def _fake_gen(req, **_kwargs):
        from aihub.chat_contracts import ModelResponse, ProviderUsage

        return ModelResponse(
            provider="fake",
            model="fake-model",
            content="system-gate assistant",
            usage=ProviderUsage(total_tokens=3, reporting_mode="provider"),
        )

    fake = SimpleNamespace(generate=_fake_gen, provider_name="fake")
    monkeypatch.setattr(
        "aihub.llm.provider_registry.get_default_provider",
        lambda: fake,
    )
    # Ensure runtime singleton picks up the patched provider
    import aihub.chat_runtime as cr

    cr._RUNTIME = None

    uid = "product_gate_e2e_user"
    before = db_mod.fetch_one(
        "SELECT COUNT(*) AS c FROM stm_messages WHERE user_id=?",
        (uid,),
    )
    n_before = int(before["c"] if before else 0)

    with TestClient(main.app) as client:
        r = client.post(
            "/chat/turn",
            json={
                "user_id": uid,
                "session_id": "sg1",
                "message": "product gate hello",
                "mode": "chat",
                "include_debug": False,
            },
        )

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body.get("response_text")
    assert "model" in body and "provider" in body
    trace = body.get("trace") or {}
    assert trace.get("experience_write_back_attempted") is True
    assert trace.get("experience_write_back_succeeded") is True
    assert trace.get("psyche_snapshot_happened") is True
    assert trace.get("memory_v2_writeback_attempted") is True
    after = db_mod.fetch_one(
        "SELECT COUNT(*) AS c FROM stm_messages WHERE user_id=?",
        (uid,),
    )
    n_after = int(after["c"] if after else 0)
    assert n_after > n_before, "STM must grow after canonical /chat/turn"


@pytest.mark.system_gate
def test_gate_agent_run_canonical_headers_tick_secondary(monkeypatch, isolated_db):
    from aihub import agent_api, main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    async def _fake_cycle(_self, _input_event, mode, user_id=None):
        return {
            "ok": True,
            "mode": mode,
            "strategy": "instant",
            "strategy_reason": "gate",
            "planning_used": False,
            "reasoning_used": False,
            "selected_goal": None,
            "goal_progress_changed": False,
            "execution_result": {"ok": True, "action_summary": "x", "errors": []},
            "reflection": {},
            "legacy_response": {"ok": True},
        }

    monkeypatch.setattr(
        agent_api.get_executive_controller().__class__,
        "run_cycle",
        _fake_cycle,
    )

    with TestClient(main.app) as client:
        run_r = client.post(
            "/agent/run",
            json={"user_id": "gate_agent", "text": "hi"},
        )
        assert run_r.status_code == 200
        assert run_r.headers.get(HEADER_ENDPOINT_ROLE) == ROLE_AGENT_CANONICAL_RUN
        assert run_r.headers.get(HEADER_CANONICAL_AGENT_FLOW) == FLOW_RUN

        tick_r = client.post("/agent/tick/gate_agent_tick?max_stm=1&max_tasks=1")
        assert tick_r.status_code == 200
        assert tick_r.headers.get(HEADER_ENDPOINT_ROLE) == ROLE_AGENT_SECONDARY_TICK
        assert tick_r.headers.get(HEADER_CANONICAL_AGENT_FLOW) == FLOW_TICK


@pytest.mark.system_gate
def test_gate_cognitive_decide_debug_marked_when_enabled(monkeypatch):
    from aihub import main
    from aihub.cognitive_controller import CognitiveController, DecisionResult

    monkeypatch.setattr(main, "COGNITIVE_DEBUG_ENDPOINT_ENABLED", True)
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    async def _fake_decide(_self, _req):
        return DecisionResult(
            action_type="skip",
            parameters={},
            confidence=0.5,
            reasoning="gate",
            skip_reason="gate",
        )

    monkeypatch.setattr(CognitiveController, "decide", _fake_decide)

    with TestClient(main.app) as client:
        r = client.post("/cognitive/decide", json={"message": "x", "context": {}})
    assert r.status_code == 200
    assert r.headers.get(HEADER_COGNITIVE_SURFACE) == COGNITIVE_DEBUG_DECIDE
    assert r.json().get("debug_only") is True
    assert r.json().get("canonical_runtime") is False


@pytest.mark.system_gate
def test_gate_web_fetch_stubbed(monkeypatch):
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    async def _fake_fetch(_uid, _url):
        return {"ok": True, "url": _url, "title": "gate", "content": "stub"}

    monkeypatch.setattr(main, "fetch_url", _fake_fetch)

    with TestClient(main.app) as client:
        r = client.post(
            "/web/fetch",
            json={"url": "https://example.com/gate"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True
    assert "example.com" in str(data.get("url", ""))
