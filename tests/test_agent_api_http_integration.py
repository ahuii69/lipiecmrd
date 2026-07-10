"""HTTP-boundary integration tests for canonical /agent/* runtime contracts."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _build_client(monkeypatch, *, debug_endpoint_enabled: bool = False) -> TestClient:
    from aihub import main

    # Keep integration tests deterministic and isolated from background worker side effects.
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    monkeypatch.setattr(
        main, "COGNITIVE_DEBUG_ENDPOINT_ENABLED", debug_endpoint_enabled
    )

    return TestClient(main.app)


def _seed_user_with_memory(user_id: str) -> None:
    from aihub.memory_core import get_memory_core
    from aihub.psyche_engine import ensure_user

    ensure_user(user_id)
    get_memory_core().ingest_fact(
        user_id, "Użytkownik pracuje nad AI-Hub", tags=["seed"], meta={}
    )


def _assert_canonical_schema(payload: dict) -> None:
    required = {
        "ok",
        "mode",
        "strategy_authority_external",
        "strategy_source",
        "strategy",
        "strategy_reason",
        "planning_used",
        "reasoning_used",
        "context_signals",
        "active_goals_summary",
        "selected_goal",
        "selected_goal_reason",
        "goal_affected_planning",
        "goal_progress_changed",
        "execution_summary",
        "trace",
        "errors",
        "reflection",
    }
    assert required.issubset(set(payload.keys()))
    assert isinstance(payload["context_signals"], dict)
    assert isinstance(payload["active_goals_summary"], list)
    assert isinstance(payload["selected_goal_reason"], str)
    assert isinstance(payload["goal_affected_planning"], bool)


def _assert_execution_flags(payload: dict) -> None:
    summary = payload["execution_summary"]
    assert isinstance(summary, dict)
    flags = summary.get("execution_flags")
    assert isinstance(flags, dict)
    for k in (
        "planning_attempted",
        "planning_executed",
        "reasoning_attempted",
        "reasoning_executed",
    ):
        assert isinstance(flags.get(k), bool)


def test_http_agent_run_real_execution_truthful_flags(monkeypatch):
    user_id = "http_run_real_user"
    _seed_user_with_memory(user_id)

    with _build_client(monkeypatch) as client:
        resp = client.post(
            "/agent/run",
            json={
                "user_id": user_id,
                "text": "zapisz: notes.txt :: canonical run execution",
            },
        )

    assert resp.status_code == 200
    body = resp.json()

    _assert_canonical_schema(body)
    _assert_execution_flags(body)
    assert "legacy" not in body
    assert "cycle" not in body

    assert body["mode"] == "run"
    assert body["strategy"] == "planned_reasoning"
    assert body["planning_used"] is True
    assert body["reasoning_used"] is True

    flags = body["execution_summary"]["execution_flags"]
    assert flags["planning_attempted"] is True
    assert flags["planning_executed"] is True
    assert flags["reasoning_attempted"] is True
    assert flags["reasoning_executed"] is True

    assert isinstance(body["trace"].get("cycle_id", ""), str)
    assert isinstance(body["trace"].get("plan_task_ids", []), list)
    assert isinstance(body["trace"].get("executed_task_ids", []), list)


def test_http_agent_run_dry_run_does_not_claim_reasoning_execution(monkeypatch):
    user_id = "http_run_dry_user"
    _seed_user_with_memory(user_id)

    with _build_client(monkeypatch) as client:
        resp = client.post(
            "/agent/run",
            json={
                "user_id": user_id,
                "text": "zapisz: notes.txt :: dry-run execution",
                "dry_run": True,
            },
        )

    assert resp.status_code == 200
    body = resp.json()

    _assert_canonical_schema(body)
    _assert_execution_flags(body)

    assert body["mode"] == "run"
    assert body["strategy"] == "planned_reasoning"
    assert body["planning_used"] is True
    assert body["reasoning_used"] is False
    assert body["execution_summary"]["dry_run"] is True

    flags = body["execution_summary"]["execution_flags"]
    assert flags["planning_attempted"] is True
    assert flags["planning_executed"] is True
    assert flags["reasoning_attempted"] is False
    assert flags["reasoning_executed"] is False


def test_http_agent_tick_and_loop_share_schema_and_truthful_mode_semantics(monkeypatch):
    user_id = "http_tick_loop_user"

    with _build_client(monkeypatch) as client:
        tick_resp = client.post(f"/agent/tick/{user_id}?max_stm=50&max_tasks=2")
        loop_resp = client.post(
            "/agent/loop",
            json={"user_id": user_id, "max_iters": 2, "dry_run": False},
        )

    assert tick_resp.status_code == 200
    assert loop_resp.status_code == 200

    tick_body = tick_resp.json()
    loop_body = loop_resp.json()

    _assert_canonical_schema(tick_body)
    _assert_canonical_schema(loop_body)
    _assert_execution_flags(tick_body)
    _assert_execution_flags(loop_body)

    assert tick_body["mode"] == "tick"
    assert tick_body["strategy"] == "reactive_tick"
    assert tick_body["planning_used"] is False
    assert tick_body["reasoning_used"] is False
    assert tick_body["selected_goal"] is None

    tick_flags = tick_body["execution_summary"]["execution_flags"]
    assert tick_flags["planning_attempted"] is False
    assert tick_flags["planning_executed"] is False
    assert tick_flags["reasoning_attempted"] is False
    assert tick_flags["reasoning_executed"] is False

    assert loop_body["mode"] == "loop"
    assert loop_body["planning_used"] is False
    assert loop_body["reasoning_used"] is False
    assert loop_body["selected_goal"] is None
    assert isinstance(loop_body["trace"].get("strategy_counts", {}), dict)


def test_http_agent_endpoints_expose_same_top_level_contract(monkeypatch):
    user_id = "http_contract_user"
    _seed_user_with_memory(user_id)

    with _build_client(monkeypatch) as client:
        run_body = client.post(
            "/agent/run",
            json={
                "user_id": user_id,
                "text": "zapisz: contract.txt :: run",
                "dry_run": True,
            },
        ).json()
        tick_body = client.post(f"/agent/tick/{user_id}").json()
        loop_body = client.post(
            "/agent/loop",
            json={"user_id": user_id, "max_iters": 1, "dry_run": True},
        ).json()

    run_keys = set(run_body.keys())
    tick_keys = set(tick_body.keys())
    loop_keys = set(loop_body.keys())

    assert run_keys == tick_keys == loop_keys
    assert "legacy" not in run_keys
    assert "cycle" not in run_keys


def test_http_agent_run_debug_payload_is_opt_in(monkeypatch):
    user_id = "http_run_debug_user"
    _seed_user_with_memory(user_id)

    with _build_client(monkeypatch) as client:
        default_resp = client.post(
            "/agent/run",
            json={
                "user_id": user_id,
                "text": "zapisz: debug.txt :: no debug payload",
                "dry_run": True,
            },
        )
        debug_resp = client.post(
            "/agent/run",
            json={
                "user_id": user_id,
                "text": "zapisz: debug.txt :: include debug payload",
                "dry_run": True,
                "include_debug": True,
            },
        )

    assert default_resp.status_code == 200
    assert debug_resp.status_code == 200

    default_body = default_resp.json()
    debug_body = debug_resp.json()

    assert default_body.get("debug") is None
    assert isinstance(debug_body.get("debug"), dict)
    assert "legacy_response" in debug_body["debug"]


def test_http_agent_goals_listing_contract(monkeypatch):
    user_id = "http_agent_goals_user"

    from aihub.goal_engine import GoalCandidate, GoalType, get_goal_engine
    from aihub.psyche_engine import ensure_user

    ensure_user(user_id)
    goal = get_goal_engine().create_goal(
        GoalCandidate(
            user_id=user_id,
            title="Sprawdzić kontrakt goals",
            description="Test kontraktu GET /agent/goals/{user_id}",
            goal_type=GoalType.USER_INTENT_GOAL.value,
            source="test",
            confidence=0.8,
        )
    )
    get_goal_engine().activate_goal(user_id, goal.goal_id)

    with _build_client(monkeypatch) as client:
        resp = client.get(f"/agent/goals/{user_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["user_id"] == user_id
    assert body["count"] >= 1
    assert any(g["goal_id"] == goal.goal_id for g in body["goals"])


def test_http_cognitive_decide_is_gated_by_default(monkeypatch):
    with _build_client(monkeypatch, debug_endpoint_enabled=False) as client:
        resp = client.post(
            "/cognitive/decide?user_id=http_cognitive_disabled",
            json={"message": "should fail", "context": {}},
        )

    assert resp.status_code == 404
    detail = resp.json().get("detail", "")
    assert "debug endpoint disabled" in detail


def test_http_cognitive_decide_when_enabled_is_explicitly_non_canonical(monkeypatch):
    with _build_client(monkeypatch, debug_endpoint_enabled=True) as client:
        resp = client.post(
            "/cognitive/decide?user_id=http_cognitive_enabled",
            json={"message": "co pamiętasz o AI-Hub?", "context": {}},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["debug_endpoint"] is True
    assert body["debug_only"] is True
    assert body["bypass"] is True
    assert body["canonical_runtime"] is False
    assert isinstance(body.get("action_type"), str)
