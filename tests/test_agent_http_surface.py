"""Agent/cognitive HTTP surface: canonical vs secondary vs debug roles (headers + guards)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aihub.agent_http_surface import (
    FLOW_LOOP,
    FLOW_RUN,
    FLOW_TICK,
    HEADER_CANONICAL_AGENT_FLOW,
    HEADER_COGNITIVE_SURFACE,
    HEADER_ENDPOINT_ROLE,
    ROLE_AGENT_CANONICAL_LOOP,
    ROLE_AGENT_CANONICAL_RUN,
    ROLE_AGENT_DEBUG_GOAL_LINKS,
    ROLE_AGENT_SECONDARY_TICK,
    COGNITIVE_DEBUG_DECIDE,
    COGNITIVE_OBSERVABILITY_HEALTH,
)


@pytest.fixture
def client(monkeypatch):
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    with TestClient(main.app) as c:
        yield c


def test_agent_run_is_canonical_with_headers(client: TestClient, isolated_db, monkeypatch):
    from aihub import agent_api

    async def _fake_run_cycle(_self, _ev, mode, user_id=None):
        return {
            "ok": True,
            "mode": mode,
            "strategy": "instant",
            "strategy_reason": "test",
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
        _fake_run_cycle,
    )
    r = client.post("/agent/run", json={"user_id": "surf_u1", "text": "hi"})
    assert r.status_code == 200
    assert r.headers[HEADER_ENDPOINT_ROLE] == ROLE_AGENT_CANONICAL_RUN
    assert r.headers[HEADER_CANONICAL_AGENT_FLOW] == FLOW_RUN


def test_agent_loop_headers_distinct_from_run(client: TestClient, isolated_db, monkeypatch):
    from aihub import agent_api

    async def _fake_run_cycle(_self, _ev, mode, user_id=None):
        return {
            "ok": True,
            "mode": mode,
            "strategy": "cognitive_direct",
            "strategy_reason": "test",
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
        _fake_run_cycle,
    )
    r = client.post(
        "/agent/loop",
        json={"user_id": "surf_u2", "text": "hi", "max_iters": 1},
    )
    assert r.status_code == 200
    assert r.headers[HEADER_ENDPOINT_ROLE] == ROLE_AGENT_CANONICAL_LOOP
    assert r.headers[HEADER_CANONICAL_AGENT_FLOW] == FLOW_LOOP


def test_agent_tick_secondary_header(client: TestClient, isolated_db, monkeypatch):
    from aihub import agent_api

    async def _fake_run_cycle(_self, _ev, mode, user_id=None):
        return {
            "ok": True,
            "mode": mode,
            "strategy": "instant",
            "strategy_reason": "t",
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
        _fake_run_cycle,
    )
    r = client.post("/agent/tick/surf_u3?max_stm=1&max_tasks=1")
    assert r.status_code == 200
    assert r.headers[HEADER_ENDPOINT_ROLE] == ROLE_AGENT_SECONDARY_TICK
    assert r.headers[HEADER_CANONICAL_AGENT_FLOW] == FLOW_TICK


def test_agent_tick_disabled_by_env(client: TestClient, isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_ENABLE_AGENT_TICK_HTTP", "0")
    r = client.post("/agent/tick/surf_u4")
    assert r.status_code == 404


def test_cognitive_health_observability_header(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "aihub.main.knowledge_graph.stats",
        lambda: {"nodes": 0},
    )
    r = client.get("/cognitive/health")
    assert r.status_code == 200
    assert r.headers[HEADER_COGNITIVE_SURFACE] == COGNITIVE_OBSERVABILITY_HEALTH


def test_cognitive_decide_debug_header_when_enabled(client: TestClient, monkeypatch):
    from aihub import main

    monkeypatch.setattr(main, "COGNITIVE_DEBUG_ENDPOINT_ENABLED", True)

    from aihub.cognitive_controller import CognitiveController, DecisionResult

    async def _fake_decide(_self, _req):
        return DecisionResult(
            action_type="skip",
            parameters={},
            confidence=0.5,
            reasoning="t",
            skip_reason="test",
        )

    monkeypatch.setattr(CognitiveController, "decide", _fake_decide)
    r = client.post("/cognitive/decide", json={"message": "x", "context": {}})
    assert r.status_code == 200
    assert r.headers[HEADER_COGNITIVE_SURFACE] == COGNITIVE_DEBUG_DECIDE
    assert r.headers[HEADER_ENDPOINT_ROLE] == "cognitive-debug-decide"
    body = r.json()
    assert body.get("debug_only") is True
    assert body.get("canonical_runtime") is False


def test_goal_links_404_when_artifact_http_disabled(
    client: TestClient, isolated_db, monkeypatch
):
    monkeypatch.setenv("AIHUB_ENABLE_AGENT_GOAL_ARTIFACT_HTTP", "0")
    r = client.get("/agent/goals/u/g/links")
    assert r.status_code == 404


def test_goal_links_debug_header_when_enabled(client: TestClient, isolated_db, monkeypatch):
    from aihub.goal_engine import GoalCandidate, GoalType, get_goal_engine

    monkeypatch.delenv("AIHUB_ENABLE_AGENT_GOAL_ARTIFACT_HTTP", raising=False)
    ge = get_goal_engine()
    g = ge.create_goal(
        GoalCandidate(
            user_id="gl_u1",
            title="t",
            description="d",
            goal_type=GoalType.TASK.value,
            source="test",
            confidence=0.8,
        )
    )
    ge.activate_goal("gl_u1", g.goal_id)
    r = client.get(f"/agent/goals/gl_u1/{g.goal_id}/links")
    assert r.status_code == 200
    assert r.headers[HEADER_ENDPOINT_ROLE] == ROLE_AGENT_DEBUG_GOAL_LINKS
