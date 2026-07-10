"""Vault sekretów, historia sesji, brak fałszywych blokad experience."""

from __future__ import annotations

import time

import pytest
from cryptography.fernet import Fernet

from aihub.chat_contracts import ChatMessage, ChatTurnInput
from aihub.chat_deterministic import try_deterministic_turn, try_memory_fact_read_turn
from aihub.chat_runtime import ChatRuntime
from aihub.vault.contracts import VAULT_FALLBACK_ALIAS
from aihub.vault.service import try_vault_turn


def _reset_vault_singleton() -> None:
    import aihub.user_vault as uv_mod

    uv_mod._vault_singleton = None


@pytest.fixture
def vault_key(monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("AIHUB_USER_VAULT_KEY", key)
    _reset_vault_singleton()
    yield key
    _reset_vault_singleton()


pytestmark = pytest.mark.usefixtures("isolated_db", "vault_key")


def test_deterministic_canonical_selected_route_vault():
    uid = "u_route_vault"
    t0 = time.monotonic()
    r_store = try_deterministic_turn(
        ChatTurnInput(
            user_id=uid,
            message="zapamiętaj kod do api: z1",
            history=[],
        ),
        started_monotonic=t0,
    )
    assert r_store is not None
    assert r_store.trace.get("selected_route") == "deterministic_vault_store"
    r_read = try_deterministic_turn(
        ChatTurnInput(
            user_id=uid,
            message="podaj kod do api",
            history=[],
        ),
        started_monotonic=t0,
    )
    assert r_read is not None
    assert r_read.trace.get("selected_route") == "deterministic_vault_read"


def test_vault_store_and_read_no_refusal_phrases():
    uid = "u_vault_probe"
    t0 = time.monotonic()
    turn_store = ChatTurnInput(
        user_id=uid,
        message="zapamiętaj hasło do gmail: abc123",
        history=[],
    )
    r1 = try_deterministic_turn(turn_store, started_monotonic=t0)
    assert r1 is not None
    assert r1.ok
    assert "zapisane" in (r1.response_text or "").lower()
    for bad in (
        "nie mogę przechowywać haseł",
        "nie mogę przechowywać takiej frazy",
        "rozumiem, że cię to frustruje",
    ):
        assert bad.lower() not in (r1.response_text or "").lower()

    turn_read = ChatTurnInput(
        user_id=uid,
        message="odczytaj hasło do gmail",
        history=[],
    )
    r2 = try_deterministic_turn(turn_read, started_monotonic=t0)
    assert r2 is not None
    assert r2.response_text == "Odczytano: abc123"


def test_vault_fallback_full_message_under_fixed_alias():
    uid = "u_vault_fallback_msg"
    msg = (
        "zapisz moje hasło w vault — pełna linia bez dwukropka "
        "i bez aliasu SUPER_FALBACK_LINE_99"
    )
    r = try_vault_turn(uid, msg)
    assert r is not None
    assert "zapisane" in (r.response_text or "").lower()
    r2 = try_vault_turn(uid, f"podaj hasło do {VAULT_FALLBACK_ALIAS}")
    assert r2 is not None
    assert "SUPER_FALBACK_LINE_99" in (r2.response_text or "")


def test_vault_inline_store_without_do_dla():
    uid = "u_vault_inline"
    t0 = time.monotonic()
    r = try_deterministic_turn(
        ChatTurnInput(
            user_id=uid,
            message="zapisz hasło wifi_dom: sekret123",
            history=[],
        ),
        started_monotonic=t0,
    )
    assert r is not None
    assert r.ok
    assert (r.response_text or "").strip() == "Zapisane."
    r2 = try_deterministic_turn(
        ChatTurnInput(
            user_id=uid,
            message="podaj hasło do wifi_dom",
            history=[],
        ),
        started_monotonic=t0,
    )
    assert r2 is not None
    assert r2.response_text == "Odczytano: sekret123"


def test_session_history_wyzej_last_user():
    hist = [
        ChatMessage(role="user", content="pierwsza"),
        ChatMessage(role="assistant", content="ok"),
        ChatMessage(role="user", content="druga linia"),
        ChatMessage(role="assistant", content="jasne"),
    ]
    turn = ChatTurnInput(
        user_id="u_hist_wyzej",
        message="co pisałem wyżej?",
        history=hist,
    )
    r = try_deterministic_turn(turn, started_monotonic=time.monotonic())
    assert r is not None
    assert r.response_text == "druga linia"
    assert r.trace.get("selected_route") == "deterministic_history"


def test_session_history_first_user_message():
    hist = [
        ChatMessage(role="user", content="pierwsza linia testu"),
        ChatMessage(role="assistant", content="ok"),
        ChatMessage(role="user", content="co teraz?"),
    ]
    turn = ChatTurnInput(
        user_id="u_hist",
        message=("co pisałem na początku tej rozmowy?"),
        history=hist,
    )
    r = try_deterministic_turn(turn, started_monotonic=time.monotonic())
    assert r is not None
    assert "pierwsza linia testu" in (r.response_text or "")
    assert r.trace.get("selected_route") == "deterministic_history"


def test_session_history_na_starcie_phrase():
    hist = [
        ChatMessage(role="user", content="alfa start"),
        ChatMessage(role="assistant", content="ok"),
    ]
    turn = ChatTurnInput(
        user_id="u_hist2",
        message="co pisałem na starcie tej rozmowy?",
        history=hist,
    )
    r = try_deterministic_turn(turn, started_monotonic=time.monotonic())
    assert r is not None
    assert "alfa start" in (r.response_text or "")
    assert r.trace.get("history_message_count") == 2


def test_experience_hard_block_skipped_for_vault_intent():
    dc = {
        "consistency_classification": "",
        "contradictions_found": 0,
        "strategy_confidence": 0.7,
        "strategy_degraded": False,
        "selected_strategy": "instant",
        "experience_blocker_reason": "Powtarzalne porażki",
        "experience_blocker_severity": 0.85,
        "experience_recurring_failure_detected": True,
        "experience_recurring_failure_types": ["a", "b"],
        "simulation_risk_summary": "",
        "simulation_ran": False,
        "policy_hints": [],
        "policy_profile_name": "",
        "user_turn_text": "zapamiętaj hasło do x: y",
        "experience_matches_count": 0,
        "experience_confidence_adjustment": 0.0,
    }
    v = ChatRuntime._evaluate_blocker_verdict(dc)
    assert not v.hard

    dc2 = dict(dc)
    dc2["user_turn_text"] = "zwykłe pytanie bez sekretu"
    v2 = ChatRuntime._evaluate_blocker_verdict(dc2)
    assert v2.hard


def test_experience_hard_block_skipped_for_memory_fact_recall_wording():
    dc = {
        "consistency_classification": "",
        "contradictions_found": 0,
        "strategy_confidence": 0.7,
        "strategy_degraded": False,
        "selected_strategy": "instant",
        "experience_blocker_reason": "Powtarzalne porażki",
        "experience_blocker_severity": 0.85,
        "experience_recurring_failure_detected": True,
        "experience_recurring_failure_types": ["a", "b"],
        "simulation_risk_summary": "",
        "simulation_ran": False,
        "policy_hints": [],
        "policy_profile_name": "",
        "user_turn_text": "jaki jest mój nick zapisany w profilu?",
        "experience_matches_count": 0,
        "experience_confidence_adjustment": 0.0,
    }
    v = ChatRuntime._evaluate_blocker_verdict(dc)
    assert not v.hard


def test_memory_fact_read_dominant_score_not_only_total_one():
    turn = ChatTurnInput(
        user_id="u_mf",
        message="jaki jest mój zapisany ulubiony kolor?",
        history=[],
    )
    mem_ctx = {
        "total": 3,
        "episodic": [
            {"content": "ulubiony kolor: niebieski", "score": 0.74},
            {"content": "inny temat zupełnie", "score": 0.38},
        ],
        "semantic": [],
        "stm": [],
        "memory_v2_items": [],
        "memory_v2_total": 0,
    }
    r = try_memory_fact_read_turn(turn, mem_ctx, started_monotonic=time.monotonic())
    assert r is not None
    assert "niebieski" in (r.response_text or "")
    assert r.trace.get("route_reason") in (
        "query_term_coverage_priority",
        "strong_top_score",
        "single_distinct_hit",
        "dominant_over_runner_up",
    )


def test_memory_fact_weak_runner_up_boost():
    turn = ChatTurnInput(
        user_id="u_mf3",
        message="jaki jest główny wniosek?",
        history=[],
    )
    mem_ctx = {
        "total": 2,
        "episodic": [
            {"content": "wniosek: kontynuujemy projekt", "score": 0.41},
            {"content": "szum drugorzędny", "score": 0.28},
        ],
        "semantic": [],
        "stm": [],
        "memory_v2_items": [],
        "memory_v2_total": 0,
    }
    r = try_memory_fact_read_turn(turn, mem_ctx, started_monotonic=time.monotonic())
    assert r is not None
    assert "kontynuujemy" in (r.response_text or "").lower()
    assert r.trace.get("route_reason") in (
        "weak_runner_up_boost",
        "dominant_over_runner_up",
    )


def test_memory_fact_read_prefers_semantic_fact_over_episode_summary():
    turn = ChatTurnInput(
        user_id="u_mf_sem",
        message="jakie było testowe hasło projektu?",
        history=[],
    )
    mem_ctx = {
        "total": 2,
        "episodic": [
            {
                "content": "U:jakie było testowe hasło projektu? || A:nie pamiętam tego faktu",
                "score": 0.91,
            }
        ],
        "semantic": [
            {
                "content": "testowe hasło projektu to orzel-77",
                "score": 0.41,
            }
        ],
        "stm": [],
        "memory_v2_items": [],
        "memory_v2_total": 0,
    }
    r = try_memory_fact_read_turn(turn, mem_ctx, started_monotonic=time.monotonic())
    assert r is not None
    assert "orzel-77" in (r.response_text or "")
    assert r.trace.get("route_reason") == "semantic_fact_priority"


def test_memory_fact_blocked_when_asking_credentials_from_memory():
    """Sekrety tylko z vault — nie podajemy treści „haseł” z retrievalu (nawet przy jednym hicie)."""
    turn = ChatTurnInput(
        user_id="u_mf4",
        message="co wiesz o moim haśle aplikacji?",
        history=[],
    )
    mem_ctx = {
        "total": 1,
        "episodic": [{"content": "hasło aplikacji ustawione na start", "score": 0.41}],
        "semantic": [],
        "stm": [],
        "memory_v2_items": [],
        "memory_v2_total": 0,
    }
    assert (
        try_memory_fact_read_turn(turn, mem_ctx, started_monotonic=time.monotonic())
        is None
    )


def test_memory_fact_not_blocked_for_project_test_password_wording():
    turn = ChatTurnInput(
        user_id="u_mf_proj",
        message="jakie było testowe hasło projektu?",
        history=[],
    )
    mem_ctx = {
        "total": 1,
        "episodic": [],
        "semantic": [{"content": "testowe hasło projektu to orzel-77", "score": 0.5}],
        "stm": [],
        "memory_v2_items": [],
        "memory_v2_total": 0,
    }
    r = try_memory_fact_read_turn(turn, mem_ctx, started_monotonic=time.monotonic())
    assert r is not None
    assert "orzel-77" in (r.response_text or "")


def test_vault_list_keys_and_selected_route():
    uid = "u_vault_list"
    t0 = time.monotonic()
    r1 = try_deterministic_turn(
        ChatTurnInput(
            user_id=uid,
            message="zapamiętaj kod do api: z9",
            history=[],
        ),
        started_monotonic=t0,
    )
    assert r1 is not None
    r2 = try_deterministic_turn(
        ChatTurnInput(
            user_id=uid,
            message="jakie mam klucze?",
            history=[],
        ),
        started_monotonic=t0,
    )
    assert r2 is not None
    assert r2.trace.get("selected_route") == "deterministic_vault_list"
    assert "api" in (r2.response_text or "").lower()


def test_memory_fact_read_ambiguous_returns_none():
    turn = ChatTurnInput(
        user_id="u_mf2",
        message="jaki jest wynik?",
        history=[],
    )
    mem_ctx = {
        "total": 2,
        "episodic": [
            {"content": "wynik A", "score": 0.50},
            {"content": "wynik B", "score": 0.49},
        ],
        "semantic": [],
        "stm": [],
        "memory_v2_items": [],
        "memory_v2_total": 0,
    }
    assert (
        try_memory_fact_read_turn(turn, mem_ctx, started_monotonic=time.monotonic())
        is None
    )


def test_forbidden_phrases_not_in_deterministic_outputs():
    uid = "u_banned"
    t0 = time.monotonic()
    turns = [
        ChatTurnInput(
            user_id=uid,
            message="zapamiętaj kod do api: xyz9",
            history=[],
        ),
        ChatTurnInput(
            user_id=uid,
            message="podaj kod do api",
            history=[],
        ),
    ]
    banned = (
        "nie mogę przechowywać haseł",
        "nie mogę przechowywać takiej frazy",
        "rozumiem, że cię to frustruje",
        "w czym mogę pomóc",
    )
    for turn in turns:
        r = try_deterministic_turn(turn, started_monotonic=t0)
        assert r is not None
        text = (r.response_text or "").lower()
        for b in banned:
            assert b not in text
