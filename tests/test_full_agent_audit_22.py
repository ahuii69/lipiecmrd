#!/usr/bin/env python3
"""Regression tests for 22.07 full-agent audit fixes."""

from __future__ import annotations

from aihub.chat_contracts import ChatTurnInput
from aihub.chat_deterministic import try_memory_fact_read_turn
from aihub.goal_engine import GoalEngine
from aihub.llm.failover_policy import max_retries_before_failover, parse_retry_after
from aihub.llm.provider_types import ProviderError


def test_memory_fact_read_skips_freshness_query():
    turn = ChatTurnInput(
        user_id="u1",
        session_id="s1",
        message="Jaka jest teraz najnowsza stabilna wersja Pythona?",
    )
    mem_ctx = {
        "total": 1,
        "semantic": [{"content": "Made progress using planned_reasoning", "score": 0.99}],
    }
    assert try_memory_fact_read_turn(turn, mem_ctx, started_monotonic=0.0) is None


def test_memory_fact_read_skips_junk_snippet():
    turn = ChatTurnInput(user_id="u1", session_id="s1", message="Kim jesteś?")
    mem_ctx = {
        "total": 1,
        "dense_hits": [{"text": "Elo", "similarity": 0.95}],
    }
    assert try_memory_fact_read_turn(turn, mem_ctx, started_monotonic=0.0) is None


def test_memory_fact_read_skips_irrelevant_dominant_hit():
    turn = ChatTurnInput(user_id="u1", session_id="s1", message="Jak nazywa się mój pies?")
    mem_ctx = {
        "total": 1,
        "semantic": [
            {
                "content": "Made progress using planned_reasoning: reasoning steps=4",
                "score": 0.99,
            }
        ],
    }
    assert try_memory_fact_read_turn(turn, mem_ctx, started_monotonic=0.0) is None


def test_goal_skip_memory_store_and_recall():
    ge = GoalEngine()
    skip, reason = ge._should_skip_goal_extraction("Zapamiętaj, że mój pies nazywa się Borys.")
    assert skip and reason == "GOAL_SKIPPED_MEMORY_STORE"
    skip2, reason2 = ge._should_skip_goal_extraction("Jak nazywa się mój pies?")
    assert skip2 and reason2 == "GOAL_SKIPPED_MEMORY_RECALL"


def test_groq_rate_limit_retry_policy():
    exc = ProviderError(
        provider="groq",
        code="rate_limit",
        message="Limit 8000, Used 2548. Please try again in 9.3375s.",
        status_code=429,
        retryable=True,
    )
    assert max_retries_before_failover(exc) == 2
    assert parse_retry_after(exc) is not None
    assert parse_retry_after(exc) >= 9.0


def test_simple_greeting_guard():
    from aihub.strategy_selector import is_simple_greeting

    assert is_simple_greeting("Elo")
    assert is_simple_greeting("No i co tam u ciebie?")
    assert not is_simple_greeting("Zaplanuj migrację bazy danych")
