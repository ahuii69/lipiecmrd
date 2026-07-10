#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Response-quality / de-personification contract (06.07 fix).

These tests lock the behavior the product requires: a factual assistant that may have a lightly
casual tone but never invents a human biography (alive, coffee, boredom, fighting code, poetry),
never gets defensive/theatrical on criticism, and whose psyche/memory can modulate tone but can
never mirror the user's aggression.
"""

import pytest

from aihub.chat_contracts import ChatTurnContext
from aihub.chat_runtime import ChatRuntime
from aihub.response_persona_guard import (
    PERSONA_CONTRACT_PROMPT,
    contains_persona_leakage,
    dry_fallback_response,
    sanitize_persona_leakage,
)

FORBIDDEN_TOKENS = ["żyję", "kawą", "nudę", "walczę z kodem", "poezją"]


def _system_prompt(message_first_turn: bool = True) -> str:
    rt = ChatRuntime()
    ctx = ChatTurnContext(user_id="u_quality", session_id="s_quality", mode="chat")
    return rt._build_system_prompt(
        ctx,
        memory_brief="(brak)",
        psyche_brief="style=rzeczowy, mood=0.55, energy=0.7, focus=0.65, directness=0.7.",
        first_turn_in_thread=message_first_turn,
    )


# --- 1. "co słychać?" status contract --------------------------------------------------------

def test_status_smalltalk_prompt_forbids_fake_biography():
    prompt = _system_prompt()
    low = prompt.lower()
    # The system prompt must instruct a short, useful status answer and forbid the fake-life tropes.
    assert "co słychać" in low
    assert "gotowy do rozmowy" in low
    assert "zakaz fałszywej biografii" in low
    for token in ("żyjesz", "kawę", "nudzisz", "walczysz z kodem", "poezją"):
        assert token in low, f"contract must explicitly ban: {token}"


def test_status_answer_sanitizer_strips_personified_smalltalk():
    # The exact style of bad answer reported for "co słychać?".
    bad = "Dobra, jak zwykle – wciąż żyję, nie mam czasu na nudę, więc walczę z kodem i kawą."
    cleaned, changed = sanitize_persona_leakage(bad, user_message="co słychać?")
    assert changed is True
    low = cleaned.lower()
    for token in FORBIDDEN_TOKENS:
        assert token.lower() not in low, f"personification token leaked: {token}"
    # Whole reply was leakage -> becomes the dry, useful fallback.
    assert cleaned == dry_fallback_response(user_message="co słychać?")


# --- 2. aggression contract ------------------------------------------------------------------

def test_aggression_prompt_requires_calm_concrete_reply():
    prompt = _system_prompt(message_first_turn=False)
    low = prompt.lower()
    assert "agresywna" in low or "wulgarna" in low
    assert "przyznaj" in low and "doprecyzowanie" in low
    assert "nie odbijaj" in low  # do not mirror aggression
    assert "nie rób poezji" in low or "metafor" in low  # no poetry/metaphors


def test_aggression_answer_sanitizer_removes_defensive_poetry():
    bad = "Spokojnie, nie mam w planach nagradzania się poezją, ale wciąż żyję i piję kawę."
    assert contains_persona_leakage(bad, user_message="co ty za bzdury pierdolisz?")
    cleaned, _ = sanitize_persona_leakage(
        bad, user_message="co ty za bzdury pierdolisz?"
    )
    low = cleaned.lower()
    assert "poezj" not in low
    assert "żyję" not in low


# --- 3. memory / psyche must not force aggression or personification -------------------------

def test_psyche_evolve_does_not_escalate_aggression_on_hostile_input(isolated_db):
    """Hostile user input must NOT raise swearing/sarcasm and must NOT force a 'ziomek' style."""
    from aihub.psyche_engine import ensure_user, evolve

    uid = "u_hostile_psyche"
    base = ensure_user(uid)
    base_sw = float(base["traits"].get("swearing", 0.0))
    base_sar = float(base["traits"].get("sarcasm", 0.0))

    state = evolve(uid, "co ty za bzdury pierdolisz debilu, kurwa chujowo", role="user")
    new_sw = float(state["traits"].get("swearing", 0.0))
    new_sar = float(state["traits"].get("sarcasm", 0.0))

    assert new_sw <= base_sw, "swearing must not increase on hostile input"
    assert new_sar <= base_sar, "sarcasm must not increase on hostile input"
    # Directness may rise (stay precise), but the tone must not be mirrored back as aggression.
    assert float(state["traits"].get("directness", 0.0)) >= float(
        base["traits"].get("directness", 0.0)
    )


def test_memory_layer_keeps_personation_contract(isolated_db):
    """Even with memory context injected, the anti-personification / no-tone-copy contract stays."""
    rt = ChatRuntime()
    ctx = ChatTurnContext(user_id="u_mem", session_id="s_mem", mode="chat")
    prompt = rt._build_system_prompt(
        ctx,
        memory_brief="Wcześniej użytkownik był wulgarny i agresywny wobec asystenta.",
        psyche_brief="style=rzeczowy, mood=0.4, energy=0.6, focus=0.6, directness=0.75.",
        first_turn_in_thread=False,
    )
    low = prompt.lower()
    assert "kopiowania tonu" in low  # memory/psyche cannot copy the quarrel tone
    assert "nie personifikuj" in low or "personifikacji" in low


def test_psyche_brief_does_not_inject_raw_sarcasm_swearing():
    rt = ChatRuntime()
    brief = rt._build_psyche_brief(
        {
            "mood": 0.5,
            "energy": 0.6,
            "focus": 0.6,
            "style": "rzeczowy",
            "traits": {"directness": 0.7, "sarcasm": 0.9, "swearing": 0.9},
        }
    )
    assert "sarcasm=" not in brief
    assert "swearing=" not in brief
    assert "nie kopiuj agresywnego tonu" in brief.lower()


# --- 4. fallback must be dry -----------------------------------------------------------------

def test_dry_fallback_is_neutral_and_useful():
    fb = dry_fallback_response(user_message="co słychać?")
    low = fb.lower()
    for token in ("żyję", "kawa", "kawą", "nudę", "poezj", "walczę z kodem"):
        assert token not in low
    assert "działa" in low
    assert not contains_persona_leakage(fb)


def test_sanitizer_keeps_substantive_content_and_allows_user_topic():
    # Substantive content is kept; only the personification sentence is trimmed.
    text = "Oto rozwiązanie: użyj funkcji sorted(). Swoją drogą wciąż żyję i walczę z kodem."
    cleaned, changed = sanitize_persona_leakage(text, user_message="jak posortować listę?")
    assert changed is True
    assert "sorted()" in cleaned
    assert "żyję" not in cleaned.lower()

    # Factual mention of coffee/poetry the user asked about must NOT be stripped.
    factual = "Kawa pochodzi z Etiopii i była znana już w XV wieku."
    cleaned2, changed2 = sanitize_persona_leakage(factual, user_message="opowiedz o kawie")
    assert changed2 is False
    assert cleaned2 == factual


def test_contract_prompt_constant_is_hard_and_present_in_system_prompt():
    assert "KONTRAKT PERSONY" in PERSONA_CONTRACT_PROMPT
    assert PERSONA_CONTRACT_PROMPT.strip() in _system_prompt().strip() or (
        "kontrakt persony" in _system_prompt().lower()
    )
