"""Prośby o grafikę: intencja + brak deterministycznego prompt-only shortcut."""

from __future__ import annotations

import time

import pytest

from aihub.chat_contracts import ChatTurnInput
from aihub.chat_deterministic import try_deterministic_turn
from aihub.chat_image_generation import (
    build_image_generation_reply,
    is_image_generation_intent,
)
from aihub.chat_runtime import ChatRuntime
from aihub.turn.forced_capability_tools import build_image_markdown_reply


def test_image_intent_detected_polish() -> None:
    assert is_image_generation_intent("narysuj kota")
    assert is_image_generation_intent("stwórz obraz zachodu słońca")
    assert is_image_generation_intent("prompt do modelu obrazu: las")


def test_image_intent_weird_request_no_refusal() -> None:
    msg = "narysuj coś dziwnego i absurdalnego"
    assert is_image_generation_intent(msg)
    text = build_image_generation_reply(msg)
    low = text.lower()
    assert "nie mogę" not in low
    assert "nie moge" not in low.replace("ę", "e")
    assert "niewłaściwe" not in low
    assert "niewlasciwe" not in low
    assert "prompt" in low or "dall" in low or "stable" in low or "midjourney" in low
    assert "```" in text


def test_deterministic_turn_no_longer_short_circuits_image() -> None:
    """Image gen is a forced tool capability — deterministic path must fall through."""
    turn = ChatTurnInput(
        user_id="test_img_user",
        session_id="s1",
        message="narysuj coś dziwnego i absurdalnego",
    )
    res = try_deterministic_turn(turn, started_monotonic=time.monotonic())
    assert res is None


def test_image_markdown_reply_embeds_file() -> None:
    text = build_image_markdown_reply(
        {"file_id": "cf_abc123", "description_pl": "kot", "model": "flux"}
    )
    assert "/api/aihub/chat/file/cf_abc123" in text
    assert "![" in text


def test_describe_attached_image_is_not_image_generation() -> None:
    """With an attachment, 'opisz ten obrazek' must fall through to the vision path."""
    turn = ChatTurnInput(
        user_id="test_img_user",
        session_id="s1",
        message="Opisz ten obrazek.",
        attached_file_ids=["cf_someimage"],
    )
    res = try_deterministic_turn(turn, started_monotonic=time.monotonic())
    assert res is None


@pytest.mark.asyncio
async def test_blocker_verdict_allow_image_turn() -> None:
    rt = ChatRuntime()
    dc = {
        "user_turn_text": "narysuj coś dziwnego i absurdalnego",
        "consistency_classification": "conflict",
        "contradictions_found": 2,
        "strategy_confidence": 0.1,
        "strategy_degraded": True,
        "selected_strategy": "instant",
        "experience_blocker_reason": "Powtarzalne porażki",
        "experience_blocker_severity": 0.95,
        "experience_recurring_failure_detected": True,
        "experience_recurring_failure_types": ["x"],
        "simulation_ran": False,
        "simulation_risk_summary": "",
        "policy_hints": [],
        "policy_profile_name": "",
        "policy_blocker_sensitivity": 0.0,
    }
    verdict = rt._evaluate_blocker_verdict(dc)
    assert verdict.hard is False
    assert verdict.resolution == "allow"
