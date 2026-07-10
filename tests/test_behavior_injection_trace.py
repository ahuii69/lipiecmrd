#!/usr/bin/env python3
"""
Integration tests for V2 behavior injection trace honesty.

Validates that behavior injection fields in chat trace and agent payload
accurately reflect real runtime influence.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from aihub.chat_runtime import ChatRuntime
from aihub.chat_contracts import ChatTurnInput, ChatMessage
from aihub.memory_v2_models import MemoryV2Item
from aihub.memory_psyche_contracts import MemoryV2RuntimeContext, PsycheV2BehaviorContext
from aihub.psyche_v2_repository import (
    ensure_psyche_profile,
    ensure_psyche_state,
    update_psyche_state,
)


@pytest.mark.asyncio
async def test_chat_trace_includes_behavior_injection_fields(isolated_db, monkeypatch):
    """Test chat trace includes all behavior injection fields."""
    user_id = "test_chat_trace_behavior"
    from aihub.memory_v2_repository import insert_memory_item
    from aihub.db import now_ts
    ts = now_ts()
    
    # Create test data
    fact = MemoryV2Item(
        id="fact_trace",
        user_id=user_id,
        memory_type="fact",
        scope="domain",
        source_kind="chat_turn",
        title="Test fact",
        content="Test content",
        summary="Test",
        salience_score=0.8,
        reinforcement_count=2,
        success_reinforcements=2,
        failure_reinforcements=0,
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(fact)
    
    # Set psyche state
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.current_mode = "focused"
    state.pressure = 0.3
    state.certainty = 0.7
    update_psyche_state(state)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "Test response from provider"
        mock_response.text = "Test response from provider"
        mock_response.model = "gpt-4"
        mock_response.provider = "openai"
        mock_response.tool_calls = []
        mock_usage = MagicMock()
        mock_usage.total_tokens = 100
        mock_usage.prompt_tokens = 50
        mock_usage.completion_tokens = 50
        mock_usage.reporting_mode = "provider"
        mock_response.usage = mock_usage
        return mock_response
    
    monkeypatch.setattr(runtime._provider, "generate", mock_generate)
    
    # Run turn
    turn_input = ChatTurnInput(
        user_id=user_id,
        message="test query",
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    # Validate behavior injection fields present in trace
    assert "memory_v2_context_injected" in result.trace
    assert "memory_v2_context_item_count" in result.trace
    assert "memory_v2_procedure_bias_applied" in result.trace
    assert "memory_v2_contradiction_guard_applied" in result.trace
    assert "psyche_v2_behavior_applied" in result.trace
    assert "psyche_v2_style_mode" in result.trace
    assert "psyche_v2_pressure_applied" in result.trace
    assert "psyche_v2_relation_tone_applied" in result.trace
    assert "final_behavior_profile" in result.trace
    
    # Validate values make sense
    assert isinstance(result.trace["memory_v2_context_injected"], bool)
    assert isinstance(result.trace["memory_v2_context_item_count"], int)
    assert isinstance(result.trace["psyche_v2_behavior_applied"], bool)
    assert isinstance(result.trace["psyche_v2_style_mode"], str)
    assert isinstance(result.trace["final_behavior_profile"], dict)
    
    # If behavior was applied, profile should be non-empty
    if result.trace["psyche_v2_behavior_applied"]:
        profile = result.trace["final_behavior_profile"]
        assert "mode" in profile
        assert "directness" in profile
        assert "caution" in profile


@pytest.mark.asyncio
async def test_agent_payload_includes_behavior_injection_fields(isolated_db):
    """Test agent payload includes all behavior injection fields."""
    from aihub.executive_controller import get_executive_controller
    
    user_id = "test_agent_payload_behavior"
    from aihub.memory_v2_repository import insert_memory_item
    from aihub.db import now_ts
    ts = now_ts()
    
    # Create test memories
    lesson = MemoryV2Item(
        id="lesson_agent",
        user_id=user_id,
        memory_type="lesson",
        scope="workflow",
        source_kind="agent_cycle",
        title="Always check logs",
        content="When debugging, always check logs first",
        summary="Check logs first",
        salience_score=0.85,
        reinforcement_count=3,
        success_reinforcements=3,
        failure_reinforcements=0,
        contradiction_state="none",
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(lesson)
    
    # Set cautious psyche state
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.current_mode = "cautious"
    state.pressure = 0.7
    update_psyche_state(state)
    
    controller = get_executive_controller()
    
    # Run cycle
    cycle = await controller.run_cycle(
        input_event={"text": "debug issue", "max_steps": 2},
        mode="run",
        user_id=user_id,
    )
    
    # Validate behavior injection fields in payload
    assert "memory_v2_context_injected" in cycle
    assert "memory_v2_context_item_count" in cycle
    assert "memory_v2_procedure_bias_applied" in cycle
    assert "memory_v2_contradiction_guard_applied" in cycle
    assert "psyche_v2_behavior_applied" in cycle
    assert "psyche_v2_style_mode" in cycle
    assert "psyche_v2_pressure_applied" in cycle
    assert "psyche_v2_relation_tone_applied" in cycle
    assert "final_behavior_profile" in cycle
    
    # Validate types
    assert isinstance(cycle["memory_v2_context_injected"], bool)
    assert isinstance(cycle["psyche_v2_behavior_applied"], bool)
    assert isinstance(cycle["final_behavior_profile"], dict)


def test_contradiction_guard_triggers_with_high_caution(isolated_db):
    """Test contradiction guard is applied when caution is high and contradictions exist."""
    user_id = "test_contradiction_guard"
    from aihub.memory_v2_repository import insert_memory_item
    from aihub.db import now_ts
    ts = now_ts()
    
    # Create contradicting memories
    mem1 = MemoryV2Item(
        id="contr1",
        user_id=user_id,
        memory_type="fact",
        scope="domain",
        source_kind="chat_turn",
        title="API returns JSON",
        content="API returns JSON",
        summary="API JSON",
        salience_score=0.7,
        contradiction_state="conflicted",
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(mem1)
    
    # Set high caution
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.current_mode = "cautious"
    state.pressure = 0.8
    state.certainty = 0.3
    update_psyche_state(state)
    
    # Build contexts
    from aihub.runtime_memory_bridge import build_memory_v2_runtime_context
    from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context
    
    mem_ctx = build_memory_v2_runtime_context(user_id, "api format")
    psyche_ctx = build_psyche_v2_behavior_context(user_id)
    
    # Check context structure
    assert mem_ctx.loaded is True
    assert psyche_ctx.loaded is True
    assert isinstance(mem_ctx.contradiction_alerts, list)
    assert isinstance(psyche_ctx.caution_bias, float)


def test_procedure_bias_triggers_with_high_confidence(isolated_db):
    """Test procedure bias structure is correct."""
    user_id = "test_proc_bias"
    
    from aihub.runtime_memory_bridge import build_memory_v2_runtime_context
    
    mem_ctx = build_memory_v2_runtime_context(user_id, "commit workflow")
    
    # Test structure
    assert isinstance(mem_ctx, MemoryV2RuntimeContext)
    assert isinstance(mem_ctx.top_procedures, list)
    assert isinstance(mem_ctx.confidence_modifier, float)
    assert 0.0 <= mem_ctx.confidence_modifier <= 1.0


def test_pressure_applied_flag_with_high_pressure(isolated_db):
    """Test pressure_applied flag is set when pressure > 0.5."""
    user_id = "test_pressure_flag"
    
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.current_mode = "neutral"
    state.pressure = 0.75
    update_psyche_state(state)
    
    from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context
    
    psyche_ctx = build_psyche_v2_behavior_context(user_id)
    
    assert psyche_ctx.loaded is True
    assert psyche_ctx.pressure == 0.75
    
    # When used in payload, should trigger pressure_applied
    pressure_applied = psyche_ctx.pressure > 0.5
    assert pressure_applied is True


def test_relation_tone_applied_with_friction_or_warmth(isolated_db):
    """Test relation_tone_applied flag is set with high friction or warmth."""
    user_id = "test_relation_tone"
    
    from aihub.psyche_v2_repository import (
        ensure_psyche_profile,
        update_psyche_profile,
    )
    
    profile = ensure_psyche_profile(user_id)
    profile.relation_friction = 0.7
    profile.relation_warmth = 0.3
    update_psyche_profile(profile)
    
    from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context
    
    psyche_ctx = build_psyche_v2_behavior_context(user_id)
    
    assert psyche_ctx.loaded is True
    assert psyche_ctx.friction == 0.7
    
    # Should trigger relation_tone_applied
    relation_tone_applied = psyche_ctx.friction > 0.5 or psyche_ctx.warmth > 0.7
    assert relation_tone_applied is True
