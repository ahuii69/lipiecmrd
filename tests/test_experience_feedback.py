#!/usr/bin/env python3
"""Runtime experience feedback: analyzer metrics, persisted bias, trace, HTTP contract."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("isolated_db")

from aihub.chat_runtime import get_chat_runtime
from aihub.db import get_strategy_decision_bias, write_experience
from aihub.experience_analyzer import ExperienceAnalyzer
from aihub.strategy_selector import (
    adjust_strategy_bias,
    compute_strategy_bias_from_metrics,
    persist_user_strategy_bias_from_metrics,
    reset_strategy_confidence_bias,
    select_strategy,
)


def _write_experience_row(**kwargs: object) -> None:
    defaults: dict[str, object] = {
        "experience_id": str(uuid.uuid4()),
        "user_id": "fb_user",
        "user_input_summary": "x",
        "selected_strategy": "instant",
        "reason_codes": [],
        "tools_needed": False,
        "tools_executed": False,
        "research_needed": False,
        "research_executed": False,
        "planner_recommended": False,
        "planner_executed": False,
        "agentic_recommended": False,
        "agentic_executed": False,
        "outcome_type": "ok",
        "success": True,
        "latency_ms": 50.0,
        "content_hash": str(uuid.uuid4()),
    }
    defaults.update(kwargs)
    assert write_experience(**defaults) is True


def test_low_instant_success_reduces_instant_bias() -> None:
    for _ in range(3):
        _write_experience_row(
            selected_strategy="instant",
            success=False,
            latency_ms=10.0,
        )
    metrics = ExperienceAnalyzer().analyze_recent_experiences("fb_user", limit=100)
    assert metrics["instant"]["sample_count"] == 3
    assert metrics["instant"]["success_rate"] == 0.0
    reset_strategy_confidence_bias()
    persist_user_strategy_bias_from_metrics("fb_user", metrics)
    assert get_strategy_decision_bias("fb_user")["instant"] == -0.06


def test_high_agentic_success_increases_agentic_bias() -> None:
    for _ in range(3):
        _write_experience_row(
            selected_strategy="agentic",
            success=True,
            latency_ms=100.0,
            planner_executed=True,
        )
    metrics = ExperienceAnalyzer().analyze_recent_experiences("fb_user", limit=100)
    reset_strategy_confidence_bias()
    adjust_strategy_bias(metrics, user_id="fb_user")
    assert get_strategy_decision_bias("fb_user")["agentic"] == 0.05


def test_instant_confidence_reflects_negative_bias() -> None:
    for _ in range(3):
        _write_experience_row(
            selected_strategy="instant",
            success=False,
        )
    m = ExperienceAnalyzer().analyze_recent_experiences("fb_user", limit=100)
    persist_user_strategy_bias_from_metrics("fb_user", m)
    sel = select_strategy(
        user_id="fb_user",
        user_text="ile to 2+2",
        mode="chat",
        active_goals_summary=None,
        history=[],
    )
    assert sel.selected_strategy == "instant"
    assert sel.confidence == 0.76


def test_compute_strategy_bias_pure_without_user() -> None:
    metrics = {
        "instant": {
            "sample_count": 3,
            "success_rate": 0.0,
            "avg_latency_ms": 1.0,
            "fallback_rate": 0.0,
            "tool_usage_rate": 0.0,
            "reasoning_usage_rate": 0.0,
        },
        "contextual": {},
        "research": {},
        "agentic": {},
    }
    b = compute_strategy_bias_from_metrics(metrics)
    assert b["instant"] == -0.06
    assert get_strategy_decision_bias("no_such_user_xyz")["instant"] == 0.0


def test_chat_turn_response_contract_unchanged(monkeypatch) -> None:
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    async def _fake_gen(_self, _req, **_kwargs):
        return {
            "message": {"role": "assistant", "content": "ok"},
            "tool_calls": [],
            "stop_reason": "done",
        }

    monkeypatch.setattr(
        "aihub.llm.provider_registry.get_default_provider",
        lambda: SimpleNamespace(generate=_fake_gen),
    )

    with TestClient(main.app) as client:
        r = client.post(
            "/chat/turn",
            json={
                "user_id": "contract_fb_user",
                "session_id": "s1",
                "message": "hello contract",
                "mode": "chat",
                "include_debug": False,
            },
        )
    assert r.status_code == 200
    body = r.json()
    required_top = {
        "ok",
        "response_text",
        "model",
        "provider",
        "tool_calls",
        "tool_results",
        "selected_mode",
        "usage",
        "trace",
        "errors",
    }
    assert required_top <= set(body.keys())


def test_trace_records_bias_change_and_persisted_flow() -> None:
    for _ in range(3):
        _write_experience_row(
            user_id="trace_user",
            selected_strategy="instant",
            success=False,
        )
    rt = get_chat_runtime()
    tr: dict[str, object] = {}
    rt._run_runtime_experience_feedback(
        "trace_user", tr
    )  # pylint: disable=protected-access
    assert isinstance(tr.get("strategy_bias_before"), dict)
    assert isinstance(tr.get("strategy_bias_after"), dict)
    assert tr.get("feedback_applied") is True
    assert tr["strategy_bias_before"].get("instant", 0.0) == 0.0
    assert tr["strategy_bias_after"]["instant"] == -0.06
    assert tr.get("strategy_bias_flow") == ["default", "memory", "persisted"]
    assert tr.get("strategy_bias_persisted_to") == "persisted"
    assert tr.get("strategy_bias_source") == "persisted"
    assert get_strategy_decision_bias("trace_user")["instant"] == -0.06
