"""Cohesion surface: cost ledger, capabilities, adaptive modules no longer skeletons."""

from __future__ import annotations

import uuid

from aihub.cost_ledger import estimate_cost_usd, record_turn_cost, user_day_summary
from aihub.ops_platform import capability_matrix
from aihub.turn.adaptive_runtime import plan_adaptive_runtime
from aihub.turn.prompt_budget import refine_prompt_budget_dynamic, select_prompt_budget
from aihub.turn.turn_signals import compute_turn_signals


def test_cost_ledger_records_and_sums(isolated_db):
    uid = f"cost-{uuid.uuid4().hex[:8]}"
    a = record_turn_cost(
        user_id=uid,
        session_id="s",
        turn_id="t1",
        provider="deepinfra",
        model="m",
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
    )
    assert a["cost_usd"] > 0
    b = record_turn_cost(
        user_id=uid,
        session_id="s",
        turn_id="t2",
        provider="groq",
        model="m",
        prompt_tokens=2000,
        completion_tokens=1000,
        total_tokens=3000,
    )
    day = user_day_summary(uid)
    assert day["turns"] == 2
    assert day["cost_usd"] >= a["cost_usd"] + b["cost_usd"] - 1e-9
    assert len(day["by_provider"]) >= 1


def test_estimate_cost_known_providers():
    assert estimate_cost_usd(provider="deepinfra", prompt_tokens=1_000_000, completion_tokens=0) == 0.1
    assert estimate_cost_usd(provider="unknown", prompt_tokens=0, completion_tokens=0) == 0.0


def test_capability_matrix_exposes_integrated_modules():
    m = capability_matrix()
    caps = m["capabilities"]
    for key in (
        "chat",
        "memory",
        "cost_ledger",
        "adaptive_runtime",
        "continuous_self_eval",
        "response_variants",
        "simulation",
        "planner",
    ):
        assert key in caps
    assert "agent_workers" in m
    assert m.get("environment") is not None


def test_agentic_keeps_variants_and_simulation_alive():
    budget = select_prompt_budget(
        user_text="Zaplanuj trzyetapową migrację PostgreSQL z rollbackiem",
        selected_strategy="agentic",
    )
    signals = compute_turn_signals(
        user_text="Zaplanuj trzyetapową migrację PostgreSQL z rollbackiem",
        selected_strategy="agentic",
        budget_profile="agentic",
        strategy_confidence=0.6,
    )
    signals.complexity = 0.55
    signals.uncertainty = 0.4
    refined = refine_prompt_budget_dynamic(budget, signals)
    assert refined.allow_response_variants is True
    plan = plan_adaptive_runtime(
        signals, refined, decision_core={"selected_strategy": "agentic", "planner_recommended": True}
    )
    assert plan.skip_response_variants is False
    assert plan.skip_simulation is False
    assert plan.skip_planner is False


def test_ops_cost_routes(client):
    r = client.get("/ops/cost/today", params={"user_id": "cohesion_user"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert "cost_usd" in body
    r2 = client.get("/ops/cost/global-today")
    assert r2.status_code == 200
    assert r2.json().get("ok") is True


def test_vision_key_falls_back_to_llm_key(monkeypatch):
    monkeypatch.setenv("CHAT_VISION_API_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "test-llm-key-for-vision")
    import importlib

    import aihub.config as cfg

    importlib.reload(cfg)
    assert (cfg.CHAT_VISION_API_KEY or "") == "test-llm-key-for-vision"
    # restore test env
    monkeypatch.setenv("ENV", "test")
    importlib.reload(cfg)
