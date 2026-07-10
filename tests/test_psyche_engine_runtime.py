"""Runtime confidence tests for psyche_engine and /psyche/reflect endpoint."""

from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from aihub.db import fetch_all
from aihub.memory_engine import add_stm
from aihub.psyche_engine import analyze_sentiment, ensure_user, evolve, reflect


def test_analyze_sentiment_handles_non_string_input():
    s, conf, meta = analyze_sentiment(cast(Any, None))

    assert -1.0 <= s <= 1.0
    assert 0.0 <= conf <= 0.95
    assert isinstance(meta, dict)
    assert meta["words"] == 0


def test_evolve_harsh_user_input_updates_traits():
    user_id = "psyche_harsh_user"
    before = ensure_user(user_id)

    out = evolve(
        user_id,
        "To jest kurwa problem, wszystko chujowo i słabo działa",
        "user",
    )

    assert out["traits"]["directness"] >= before["traits"]["directness"]
    assert out["traits"]["patience"] <= before["traits"]["patience"]


def test_evolve_friendly_user_input_updates_traits():
    user_id = "psyche_friendly_user"
    before = ensure_user(user_id)

    out = evolve(
        user_id,
        "Bardzo dobrze, super i dzięki za świetnie wykonaną robotę",
        "user",
    )

    assert out["traits"]["agreeableness"] >= before["traits"]["agreeableness"]
    assert out["traits"]["patience"] >= before["traits"]["patience"]


def test_reflect_handles_noisy_context_and_calls_learning(monkeypatch):
    user_id = "psyche_reflect_noisy"
    ensure_user(user_id)

    calls = {"count": 0, "payload": None}

    def _fake_learn_from_reflection(uid, payload):
        calls["count"] += 1
        calls["payload"] = (uid, payload)
        return {"ok": True}

    monkeypatch.setattr(
        "aihub.learning_engine.learn_from_reflection",
        _fake_learn_from_reflection,
    )

    out = reflect(
        user_id,
        cast(
            Any,
            [
                {"content": "Python python test test"},
                None,
                "DevOps devops docker docker",
                {"content": 12345},
            ],
        ),
    )

    assert out["user_id"] == user_id
    assert isinstance(out["topics"], list)
    assert calls["count"] == 1
    payload = calls["payload"]
    assert isinstance(payload, tuple)


def test_psyche_reflect_endpoint_integration(monkeypatch):
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    user_id = "psyche_reflect_endpoint"
    ensure_user(user_id)
    add_stm(user_id, "user", "Python python test test", {})
    add_stm(user_id, "assistant", "Jasne, pomogę z Python", {})

    with TestClient(main.app) as client:
        resp = client.post(
            "/psyche/reflect",
            json={"user_id": user_id, "query": "python", "limit": 10},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == user_id
    assert "reflection" in body
    assert isinstance(body.get("topics", []), list)

    events = fetch_all(
        "SELECT type FROM event_log WHERE user_id=? AND type='psyche.reflect'",
        (user_id,),
    )
    assert len(events) >= 1
