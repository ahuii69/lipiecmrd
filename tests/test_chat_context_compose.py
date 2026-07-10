"""Kompozycja kontekstu LLM — kolejność, caps, prawda retrievalu."""

from __future__ import annotations

from aihub.chat_context_compose import (
    augment_trace_context_truth,
    clip_chat_history,
    memory_results_count_for_trace,
    memory_truth_for_prompt,
    sanitize_user_message_for_llm,
    smart_clip_chat_history,
    web_grounding_in_prompt,
)
from aihub.chat_contracts import ChatMessage


def test_clip_chat_history_tail():
    h = [ChatMessage(role="user", content=str(i)) for i in range(60)]
    clipped = clip_chat_history(h, max_messages=48)
    assert len(clipped) == 48
    assert "12" in (clipped[0].content or "")
    assert "59" in (clipped[-1].content or "")


def test_smart_clip_returns_rollup_and_raw_tail():
    h = [ChatMessage(role="user", content=f"m{i}") for i in range(55)]
    rollup, tail = smart_clip_chat_history(
        h, trigger_over=40, raw_tail=25, max_messages=200
    )
    assert rollup is not None
    assert "M0" in rollup.upper() or "m0" in rollup
    assert len(tail) == 25
    assert (tail[-1].content or "") == "m54"


def test_smart_clip_no_rollup_when_short():
    h = [
        ChatMessage(role="user", content="a"),
        ChatMessage(role="assistant", content="b"),
    ]
    rollup, tail = smart_clip_chat_history(
        h, trigger_over=48, raw_tail=30, max_messages=200
    )
    assert rollup is None
    assert len(tail) == 2


def test_memory_truth_substantive_only_ltm_or_v2():
    assert not memory_truth_for_prompt(
        {"stm": [{"role": "u", "content": "x", "id": "1"}], "total": 0}
    )["memory_substantive_in_prompt"]
    assert memory_truth_for_prompt(
        {"episodic": [{"content": "a", "id": "1"}], "total": 1}
    )["memory_substantive_in_prompt"]
    assert memory_truth_for_prompt(
        {"memory_v2_items": [{"title": "t", "content": "c", "id": "v2"}], "total": 0}
    )["memory_substantive_in_prompt"]


def test_memory_results_count_merges_v2():
    n = memory_results_count_for_trace(
        {"total": 2, "memory_v2_items": [{"id": "a"}, {"id": "b"}]}
    )
    assert n == 4


def test_sanitize_vault_intent_for_llm():
    t, red = sanitize_user_message_for_llm("zapamiętaj hasło do x: y")
    assert red is True
    assert "Vault" in t


def test_web_grounding_in_prompt_requires_ok_results():
    assert not web_grounding_in_prompt(
        {"triggered": True, "ok": False, "has_results": True}
    )
    assert web_grounding_in_prompt({"triggered": True, "ok": True, "has_results": True})


def test_memory_retrieval_has_rows_true_for_stm_even_if_not_substantive():
    t = memory_truth_for_prompt(
        {"stm": [{"role": "user", "content": "hi", "id": "1"}], "total": 0}
    )
    assert t["memory_retrieval_has_rows"] is True
    assert t["memory_substantive_in_prompt"] is False


def test_web_required_without_triggered_is_not_grounding_in_prompt():
    assert not web_grounding_in_prompt(
        {"triggered": False, "ok": True, "has_results": True}
    )


def test_augment_trace_separates_web_selector_required_from_verified_injection():
    tr: dict = {}
    augment_trace_context_truth(
        tr,
        mem_truth=memory_truth_for_prompt(
            {"episodic": [{"id": "1", "content": "fact"}], "total": 1}
        ),
        controlled_web={"triggered": False, "ok": None, "has_results": None},
        decision_core={"web_decision": "required"},
    )
    assert tr["memory_retrieval_executed"] is True
    assert tr["web_required_by_selector"] is True
    assert tr["web_verified_grounding_in_llm_messages"] is False
    assert tr["web_grounding_in_prompt"] is False
    assert tr["memory_substantive_injected_in_prompt"] is True


def test_augment_trace_force_no_web_verified_after_explicit_fail():
    tr: dict = {}
    augment_trace_context_truth(
        tr,
        mem_truth=memory_truth_for_prompt({}),
        controlled_web={"triggered": True, "ok": True, "has_results": True},
        decision_core={"web_decision": "required"},
        force_no_web_verified=True,
    )
    assert tr["web_verified_grounding_in_llm_messages"] is False
    assert tr["web_grounding_in_prompt"] is False
