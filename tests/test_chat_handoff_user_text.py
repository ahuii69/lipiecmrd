from __future__ import annotations

from aihub.chat_handoff_user_text import (
    extract_substantive_from_reasoning_payload,
    is_casual_greeting,
    looks_like_execution_report_text,
    synthesize_chat_handoff_user_text,
    user_requested_execution_report,
)


def test_user_requested_execution_report():
    assert user_requested_execution_report("Daj raport wykonania") is True
    assert user_requested_execution_report("Elo") is False


def test_looks_like_execution_report():
    assert looks_like_execution_report_text("Zrealizowałem 3 kroki planu.") is True
    assert looks_like_execution_report_text("Cykl zakończony z błędem.") is False
    assert looks_like_execution_report_text("Tu jest normalna odpowiedź.") is False


def test_casual_greeting():
    assert is_casual_greeting("Elo") is True
    assert is_casual_greeting("  hej!!  ") is True
    assert is_casual_greeting("Zaplanuj mi dzień") is False


def test_synthesize_replaces_plan_leak_for_chat():
    cycle = {
        "execution_result": {
            "payload": {
                "context": {
                    "history": [],
                },
            },
        },
    }
    out = synthesize_chat_handoff_user_text(
        user_message="Elo",
        internal_reply="Zrealizowałem 3 kroki planu.",
        action_summary="reasoning steps=3",
        cycle=cycle,
        agent_ok=True,
    )
    assert "Zrealizowałem" not in out
    assert "krok" not in out.lower()
    assert "Siema" in out


def test_synthesize_keeps_substantive_when_not_internal():
    out = synthesize_chat_handoff_user_text(
        user_message="Jaka jest stolica Polski?",
        internal_reply="Warszawa jest stolicą Polski.",
        action_summary="",
        cycle={"execution_result": {"payload": {}}},
        agent_ok=True,
    )
    assert "Warszawa" in out


def test_synthesize_report_keyword_passthrough():
    t = "Zrealizowałem 2 kroki planu."
    out = synthesize_chat_handoff_user_text(
        user_message="Pokaż raport wykonania",
        internal_reply=t,
        action_summary="",
        cycle={"execution_result": {"payload": {}}},
        agent_ok=True,
    )
    assert out == t


def test_extract_from_history():
    payload = {
        "context": {
            "history": [
                {
                    "task_id": "1",
                    "task_type": "x",
                    "ok": True,
                    "result": {"text": "Oto odpowiedź merytoryczna dla użytkownika."},
                },
            ],
        },
    }
    got = extract_substantive_from_reasoning_payload(payload)
    assert got is not None
    assert "merytoryczna" in got
