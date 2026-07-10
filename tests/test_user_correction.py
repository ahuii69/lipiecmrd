# -*- coding: utf-8 -*-
from aihub.user_correction import (
    build_correction_hints_for_prompt,
    detect_user_correction,
)


def test_detect_factual_hallucination():
    d = detect_user_correction("To zmyśliłeś, nie podałem roku produkcji.")
    assert d is not None
    assert d["kind"] == "factual"


def test_detect_negative():
    d = detect_user_correction("Źle, nie o to chodziło.")
    assert d is not None
    assert d["kind"] == "negative"


def test_detect_style():
    d = detect_user_correction("Ma być krócej, bez lania wody.")
    assert d is not None
    assert d["kind"] == "style"


def test_detect_durable_marker():
    d = detect_user_correction("Zawsze odpowiadaj krócej w tym wątku.")
    assert d is not None
    assert d["durable"] is True


def test_no_false_positive_random_chat():
    assert detect_user_correction("Jaka jest stolica Francji?") is None


def test_build_hints_filters_session_and_durable(monkeypatch):
    calls = []

    def fake_fetch(uid, typ, limit):
        calls.append((uid, typ, limit))
        return [
            {
                "id": 2,
                "data": {
                    "session_id": "s_other",
                    "kind": "style",
                    "summary": "inne sesja",
                    "durable": False,
                },
            },
            {
                "id": 1,
                "data": {
                    "session_id": "s1",
                    "kind": "style",
                    "summary": "krócej proszę",
                    "durable": False,
                },
            },
            {
                "id": 3,
                "data": {
                    "session_id": "s_other",
                    "kind": "style",
                    "summary": "zawsze formalnie",
                    "durable": True,
                },
            },
        ]

    monkeypatch.setattr(
        "aihub.user_correction.fetch_recent_events_by_type", fake_fetch
    )
    text = build_correction_hints_for_prompt("u1", "s1")
    assert "krócej" in text
    assert "zawsze formalnie" in text
    assert "inne sesja" not in text
