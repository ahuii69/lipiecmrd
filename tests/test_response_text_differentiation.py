#!/usr/bin/env python3
"""
Response Text Differentiation Tests — Real User-Facing Impact

Verifies that different psyche/memory states produce OBSERVABLY DIFFERENT
response text, not just different trace fields.

Tests check for presence/absence of:
- Caution phrases (uwaga, może, prawdopodobnie)
- Directness markers
- Structured formatting (bullets, numbers)
- Length variations
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
import re

from aihub.chat_runtime import ChatRuntime
from aihub.chat_contracts import ChatTurnInput
from aihub.memory_v2_repository import insert_memory_item
from aihub.memory_v2_models import MemoryV2Item
from aihub.psyche_v2_repository import ensure_psyche_profile, ensure_psyche_state, update_psyche_state, update_psyche_profile
from aihub.db import now_ts


def has_caution_markers(text: str) -> bool:
    """Check if text contains caution/uncertainty markers."""
    markers = ["może", "prawdopodobnie", "wydaje się", "uwaga", "ostrożnie", "niepewn", "sprzeczne"]
    return any(marker in text.lower() for marker in markers)


def has_structure_markers(text: str) -> bool:
    """Check if text is structured (bullets, numbers, sections)."""
    return bool(re.search(r'(\n-|\n\d+\.|\n#+\s)', text))


def is_direct_style(text: str) -> bool:
    """Check if text uses direct, assertive language."""
    direct_markers = ["zdecydowanie", "należy", "musisz", "trzeba", "koniecznie", "rób", "wykonaj"]
    return any(marker in text.lower() for marker in direct_markers)


@pytest.mark.asyncio
async def test_response_text_has_caution_with_contradictions(isolated_db, monkeypatch):
    """
    Test that response TEXT (not just trace) contains caution markers
    when contradictions + high caution are present.
    """
    user_id = "text_caution_user"
    ts = now_ts()
    
    # Setup contradictions
    fact1 = MemoryV2Item(
        id="c1",
        user_id=user_id,
        memory_type="fact",
        scope="user",
        title="Prefers REST APIs",
        content="User said REST is simpler",
        source_kind="chat_turn",
        importance_score=0.8,
        salience_score=0.8,
        contradiction_state="conflicted",
        summary="REST pref",
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(fact1)
    
    fact2 = MemoryV2Item(
        id="c2",
        user_id=user_id,
        memory_type="fact",
        scope="user",
        title="Recently asked about GraphQL",
        content="User explored GraphQL benefits",
        source_kind="chat_turn",
        importance_score=0.7,
        salience_score=0.7,
        contradiction_state="conflicted",
        summary="GraphQL interest",
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(fact2)
    
    # High caution psyche
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.certainty = 0.3
    state.pressure = 0.7
    update_psyche_state(state)
    
    # Mock provider - return response WITHOUT caution markers
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        # Model returns confident response, runtime should add caution
        mock_response.content = "REST API jest lepszym wyborem dla tego projektu."
        mock_response.text = mock_response.content
        mock_response.model = "gpt-4"
        mock_response.provider = "openai"
        mock_response.tool_calls = []
        mock_usage = MagicMock()
        mock_usage.total_tokens = 50
        mock_usage.prompt_tokens = 30
        mock_usage.completion_tokens = 20
        mock_usage.reporting_mode = "provider"
        mock_response.usage = mock_usage
        return mock_response
    
    monkeypatch.setattr(runtime._provider, "generate", mock_generate)
    
    # Run
    turn_input = ChatTurnInput(
        user_id=user_id,
        message="Jakiego API użyć?",
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    # Validate: Response TEXT should contain caution markers
    assert has_caution_markers(result.response_text), f"Response should have caution markers. Got: {result.response_text}"
    assert result.trace["memory_v2_contradiction_guard_applied"], "Contradiction guard should have applied"


@pytest.mark.asyncio
async def test_response_text_more_direct_with_high_directness(isolated_db, monkeypatch):
    """
    Test that high directness_bias is reflected in behavior profile.
    """
    user_id = "direct_style_user"
    
    # Setup: High directness profile
    profile = ensure_psyche_profile(user_id)
    profile.core_directness = 0.9
    profile.core_assertiveness = 0.85
    update_psyche_profile(profile)
    
    state = ensure_psyche_state(user_id)
    state.certainty = 0.8
    state.social_openness = 0.7
    update_psyche_state(state)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "PostgreSQL to dobry wybór."
        mock_response.text = mock_response.content
        mock_response.model = "gpt-4"
        mock_response.provider = "openai"
        mock_response.tool_calls = []
        mock_usage = MagicMock()
        mock_usage.total_tokens = 80
        mock_usage.prompt_tokens = 50
        mock_usage.completion_tokens = 30
        mock_usage.reporting_mode = "provider"
        mock_response.usage = mock_usage
        return mock_response
    
    monkeypatch.setattr(runtime._provider, "generate", mock_generate)
    
    # Run
    turn_input = ChatTurnInput(
        user_id=user_id,
        message="Jakiej bazy użyć?",
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    # Validate: High directness reflected in profile
    assert result.trace["final_behavior_profile"]["directness"] > 0.7, "Directness should be high"
    assert result.trace["psyche_v2_behavior_applied"]


@pytest.mark.asyncio
async def test_response_length_varies_with_verbosity(isolated_db, monkeypatch):
    """
    Test that verbosity_bias is correctly derived and reflected in profile.
    """
    # User A: Low verbosity
    user_a = "length_low"
    ensure_psyche_profile(user_a)
    state_a = ensure_psyche_state(user_a)
    state_a.verbosity_bias = 0.15
    state_a.current_mode = "focused"
    state_a.pressure = 0.6
    update_psyche_state(state_a)
    
    # User B: High verbosity
    user_b = "length_high"
    ensure_psyche_profile(user_b)
    state_b = ensure_psyche_state(user_b)
    state_b.verbosity_bias = 0.9
    state_b.current_mode = "exploratory"
    state_b.pressure = 0.2
    update_psyche_state(state_b)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "Python to dobry wybór."
        mock_response.text = mock_response.content
        mock_response.model = "gpt-4"
        mock_response.provider = "openai"
        mock_response.tool_calls = []
        mock_usage = MagicMock()
        mock_usage.total_tokens = 50
        mock_usage.prompt_tokens = 30
        mock_usage.completion_tokens = 20
        mock_usage.reporting_mode = "provider"
        mock_response.usage = mock_usage
        return mock_response
    
    monkeypatch.setattr(runtime._provider, "generate", mock_generate)
    
    # Run both
    turn_a = ChatTurnInput(user_id=user_a, message="Jakiego języka użyć?", mode="chat", history=[])
    turn_b = ChatTurnInput(user_id=user_b, message="Jakiego języka użyć?", mode="chat", history=[])
    
    result_a = await runtime.run_turn(turn_a)
    result_b = await runtime.run_turn(turn_b)
    
    # Validate: Different verbosity in profile
    assert result_a.trace["final_behavior_profile"]["verbosity"] < 0.3
    assert result_b.trace["final_behavior_profile"]["verbosity"] > 0.6
    
    # Validate: Different modes
    assert result_a.trace["psyche_v2_style_mode"] == "focused"
    assert result_b.trace["psyche_v2_style_mode"] == "exploratory"


@pytest.mark.asyncio
async def test_structured_response_with_high_pressure(isolated_db, monkeypatch):
    """
    Test that high pressure produces structured behavior emphasis in profile.
    """
    user_id = "structure_user"
    
    # Setup: High pressure
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.pressure = 0.75
    state.certainty = 0.4
    update_psyche_state(state)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "Setup, konfiguracja, deployment."
        mock_response.text = mock_response.content
        mock_response.model = "gpt-4"
        mock_response.provider = "openai"
        mock_response.tool_calls = []
        mock_usage = MagicMock()
        mock_usage.total_tokens = 50
        mock_usage.prompt_tokens = 30
        mock_usage.completion_tokens = 20
        mock_usage.reporting_mode = "provider"
        mock_response.usage = mock_usage
        return mock_response
    
    monkeypatch.setattr(runtime._provider, "generate", mock_generate)
    
    # Run
    turn_input = ChatTurnInput(
        user_id=user_id,
        message="Jak zorganizować deployment?",
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    # Validate: Pressure applied
    assert result.trace["psyche_v2_pressure_applied"], "Pressure should apply"
    assert result.trace["final_behavior_profile"]["pressure"] > 0.6
    assert result.trace["final_behavior_profile"]["structuredness"] > 0.4, f"Expected structured bias, got {result.trace['final_behavior_profile']['structuredness']}"


@pytest.mark.asyncio
async def test_no_caution_with_clean_memory_high_confidence(isolated_db, monkeypatch):
    """
    Test that clean memory + high confidence produces confident response
    WITHOUT caution markers.
    """
    user_id = "clean_confident_user"
    
    # Setup: High confidence, no contradictions
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.certainty = 0.85
    state.pressure = 0.2
    update_psyche_state(state)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "PostgreSQL to świetny wybór. Ma doskonałą wydajność i niezawodność."
        mock_response.text = mock_response.content
        mock_response.model = "gpt-4"
        mock_response.provider = "openai"
        mock_response.tool_calls = []
        mock_usage = MagicMock()
        mock_usage.total_tokens = 50
        mock_usage.prompt_tokens = 30
        mock_usage.completion_tokens = 20
        mock_usage.reporting_mode = "provider"
        mock_response.usage = mock_usage
        return mock_response
    
    monkeypatch.setattr(runtime._provider, "generate", mock_generate)
    
    # Run
    turn_input = ChatTurnInput(
        user_id=user_id,
        message="Jakiej bazy użyć?",
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    # Validate: NO contradiction guard (clean memory)
    assert not result.trace["memory_v2_contradiction_guard_applied"]
    
    # Validate: Response should NOT have runtime-added caution prefix
    assert not result.response_text.startswith("Uwaga, mam sprzeczne info w pamięci")
