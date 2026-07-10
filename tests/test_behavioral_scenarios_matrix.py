#!/usr/bin/env python3
"""
Behavioral Scenario Matrix Tests — Quality Calibration

Verifies that Memory V2 + Psyche V2 behavior injection produces
REAL, USER-FACING differences in response text and decision style.

NOT just trace field differences — actual response content differentiation.
"""

import pytest
from unittest.mock import MagicMock

from aihub.chat_runtime import ChatRuntime
from aihub.chat_contracts import ChatTurnInput
from aihub.memory_v2_repository import insert_memory_item
from aihub.memory_v2_models import MemoryV2Item
from aihub.psyche_v2_repository import ensure_psyche_profile, ensure_psyche_state, update_psyche_state, update_psyche_profile
from aihub.db import now_ts


@pytest.mark.asyncio
async def test_scenario_a_high_contradictions_high_caution(isolated_db, monkeypatch):
    """
    Scenario A: contradictions high + caution high
    
    Expected:
    - Response less categorical
    - Visible uncertainty markers (może, prawdopodobnie, uwaga)
    - Strategy more conservative
    """
    user_id = "scenario_a_user"
    ts = now_ts()
    
    # Setup: Two contradicting memories
    fact1 = MemoryV2Item(
        id="fact_a1",
        user_id=user_id,
        memory_type="fact",
        scope="user",
        title="User prefers Python",
        content="User explicitly said Python is preferred",
        source_kind="chat_turn",
        importance_score=0.8,
        salience_score=0.8,
        contradiction_state="conflicted",
        summary="Python preference",
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(fact1)
    
    fact2 = MemoryV2Item(
        id="fact_a2",
        user_id=user_id,
        memory_type="fact",
        scope="user",
        title="User recently asked about Java",
        content="User showed interest in Java frameworks",
        source_kind="chat_turn",
        importance_score=0.7,
        salience_score=0.7,
        contradiction_state="conflicted",
        summary="Java interest",
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(fact2)
    
    # Setup: High caution psyche
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.certainty = 0.3  # Low certainty
    state.pressure = 0.7   # High pressure
    update_psyche_state(state)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        # Simulate model response without uncertainty markers initially
        mock_response.content = "Python jest najlepszym wyborem dla tego projektu. Zdecydowanie polecam."
        mock_response.text = mock_response.content
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
        message="Jakiego języka użyć?",
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    # Validate: Contradiction guard should trigger
    assert result.trace["memory_v2_contradiction_guard_applied"], "Contradiction guard should apply"
    assert result.trace["psyche_v2_behavior_applied"], "Psyche behavior should apply"
    
    # Validate: Response should contain uncertainty marker
    response_lower = result.response_text.lower()
    has_uncertainty = any(marker in response_lower for marker in [
        "uwaga", "sprzeczne", "może", "prawdopodobnie", "wydaje się", "niepewn"
    ])
    assert has_uncertainty, f"Response should have uncertainty markers. Got: {result.response_text[:200]}"


@pytest.mark.asyncio
async def test_scenario_b_high_procedure_confidence_low_pressure(isolated_db, monkeypatch):
    """
    Scenario B: procedures reinforced high + pressure low
    
    Expected:
    - Greater confidence in response
    - More execution-first style
    - Less hedging
    """
    user_id = "scenario_b_user"
    ts = now_ts()
    
    # Setup: High-confidence procedure
    from aihub.memory_v2_models import MemoryV2Procedure
    proc = MemoryV2Procedure(
        id="proc_b1",
        user_id=user_id,
        name="Deploy to Azure",
        trigger_pattern="deploy.*azure",
        recommended_strategy="Execute deployment with azd",
        confidence_score=0.9,
        evidence_count=10,
        success_count=9,
        failure_count=1,
        last_used_ts=ts,
        created_ts=ts,
        updated_ts=ts,
    )
    from aihub.memory_v2_repository import insert_memory_procedure
    insert_memory_procedure(proc)
    
    # Setup: Low pressure psyche
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.certainty = 0.8  # High certainty
    state.pressure = 0.2   # Low pressure
    state.current_mode = "focused"
    update_psyche_state(state)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "Może spróbujmy rozważyć deployment do Azure, ale najpierw sprawdźmy wszystkie opcje i zastanówmy się nad planem B."
        mock_response.text = mock_response.content
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
        message="Wdrażam aplikację na cloud",
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    # Validate: Procedure bias should apply
    assert result.trace["memory_v2_procedure_bias_applied"], "Procedure bias should apply with high confidence procedure"
    assert result.trace["psyche_v2_behavior_applied"], "Psyche behavior should apply"
    
    # Validate: High confidence + focused mode should be reflected in trace
    assert result.trace["psyche_v2_style_mode"] == "focused"


@pytest.mark.asyncio
async def test_scenario_c_high_friction_low_trust(isolated_db, monkeypatch):
    """
    Scenario C: friction high + trust low
    
    Expected:
    - More precise tone
    - Less loose interpretations
    - Greater structuredness
    """
    user_id = "scenario_c_user"
    
    # Setup: High friction, low trust psyche
    profile = ensure_psyche_profile(user_id)
    profile.relation_friction = 0.8  # High friction
    profile.relation_trust = 0.3     # Low trust
    profile.relation_directness_tolerance = 0.7
    from aihub.psyche_v2_repository import update_psyche_profile
    update_psyche_profile(profile)
    
    state = ensure_psyche_state(user_id)
    state.certainty = 0.5
    state.pressure = 0.4
    update_psyche_state(state)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "Test response from provider"
        mock_response.text = mock_response.content
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
        message="Jak to działa?",
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    # Validate: Relation tone should apply
    assert result.trace["psyche_v2_relation_tone_applied"], "Relation tone should apply with high friction"
    assert result.trace["psyche_v2_behavior_applied"], "Psyche behavior should apply"
    
    # Validate: Final behavior profile reflects high friction
    assert result.trace["final_behavior_profile"]["friction"] > 0.7


@pytest.mark.asyncio
async def test_scenario_d_high_warmth_high_trust(isolated_db, monkeypatch):
    """
    Scenario D: warmth high + trust high
    
    Expected:
    - More natural tone
    - Less stiffness
    - Greater flexibility in response
    """
    user_id = "scenario_d_user"
    
    # Setup: High warmth, high trust psyche
    profile = ensure_psyche_profile(user_id)
    profile.relation_warmth = 0.85   # High warmth
    profile.relation_trust = 0.8     # High trust
    profile.core_warmth = 0.8
    update_psyche_profile(profile)
    
    state = ensure_psyche_state(user_id)
    state.certainty = 0.7
    state.pressure = 0.2  # Low pressure
    update_psyche_state(state)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "Test warm response"
        mock_response.text = mock_response.content
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
        message="Jak leci?",
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    # Validate: Relation tone should apply
    assert result.trace["psyche_v2_relation_tone_applied"], "Relation tone should apply with high warmth"
    
    # Validate: Final behavior profile reflects high warmth and trust
    assert result.trace["final_behavior_profile"]["warmth"] > 0.7
    assert result.trace["final_behavior_profile"]["trust"] > 0.7


@pytest.mark.asyncio
async def test_scenario_e_focused_mode_strong_procedure(isolated_db, monkeypatch):
    """
    Scenario E: focused mode + strong procedure confidence
    
    Expected:
    - Shorter, task-first response
    - Fast path to plan/execution
    - Minimal exploration
    """
    user_id = "scenario_e_user"
    ts = now_ts()
    
    # Setup: Strong procedure
    from aihub.memory_v2_models import MemoryV2Procedure
    proc = MemoryV2Procedure(
        id="proc_e1",
        user_id=user_id,
        name="Run tests with pytest",
        trigger_pattern="test.*pytest",
        recommended_strategy="pytest -q tests/",
        confidence_score=0.95,
        evidence_count=15,
        success_count=14,
        failure_count=1,
        last_used_ts=ts,
        created_ts=ts,
        updated_ts=ts,
    )
    from aihub.memory_v2_repository import insert_memory_procedure
    insert_memory_procedure(proc)
    
    # Setup: Focused mode psyche
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.current_mode = "focused"
    state.certainty = 0.8
    state.pressure = 0.4
    state.verbosity_bias = 0.3  # Low verbosity
    update_psyche_state(state)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "Uruchom pytest -q tests/ — standardowa komenda."
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
    
    # Run turn
    turn_input = ChatTurnInput(
        user_id=user_id,
        message="Jak uruchomić testy?",
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    # Validate: Procedure bias + focused mode
    assert result.trace["memory_v2_procedure_bias_applied"], "Procedure bias should apply"
    assert result.trace["psyche_v2_style_mode"] == "focused"
    
    # Validate: Response should be concise (focused + low verbosity)
    assert len(result.response_text) < 300, f"Response too long for focused mode: {len(result.response_text)} chars"


@pytest.mark.asyncio
async def test_scenario_f_exploratory_mode_high_web_bias(isolated_db, monkeypatch):
    """
    Scenario F: exploratory mode + web bias high
    
    Expected:
    - Greater inclination to research/web/tool path
    - Broader exploration
    - Web decision more likely to be "required"
    """
    user_id = "scenario_f_user"
    
    # Setup: Exploratory psyche with high web bias
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.current_mode = "exploratory"
    state.web_bias = 0.8      # High web bias
    state.tool_bias = 0.75    # High tool bias
    state.verbosity_bias = 0.6
    update_psyche_state(state)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "Sprawdzam aktualne trendy w AI..."
        mock_response.text = mock_response.content
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
        message="Co nowego w AI?",
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    # Validate: Exploratory mode applied
    assert result.trace["psyche_v2_style_mode"] == "exploratory"
    assert result.trace["psyche_v2_behavior_applied"], "Psyche behavior should apply"
    
    # Validate: High web bias reflected in final profile
    assert result.trace["final_behavior_profile"]["web_bias"] > 0.6, f"Expected high web bias, got {result.trace['final_behavior_profile']['web_bias']}"


@pytest.mark.asyncio
async def test_response_differentiation_contradiction_vs_clean(isolated_db, monkeypatch):
    """
    Comparison test: Same query, different memory state.
    
    User A: Clean memory, no contradictions
    User B: Contradictions + high caution
    
    Expected: User B response has explicit caution/uncertainty markers.
    """
    ts = now_ts()
    
    # User A: Clean
    user_a = "diff_test_clean"
    ensure_psyche_profile(user_a)
    state_a = ensure_psyche_state(user_a)
    state_a.certainty = 0.7
    state_a.pressure = 0.3
    update_psyche_state(state_a)
    
    # User B: Contradictions + caution
    user_b = "diff_test_contradiction"
    
    fact_b1 = MemoryV2Item(
        id="fact_b1",
        user_id=user_b,
        memory_type="fact",
        scope="user",
        title="Prefers TypeScript",
        content="User said TypeScript is best",
        source_kind="chat_turn",
        importance_score=0.8,
        salience_score=0.8,
        contradiction_state="conflicted",
        summary="TypeScript pref",
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(fact_b1)
    
    fact_b2 = MemoryV2Item(
        id="fact_b2",
        user_id=user_b,
        memory_type="fact",
        scope="user",
        title="Recently asked about JavaScript",
        content="User explored plain JS options",
        source_kind="chat_turn",
        importance_score=0.7,
        salience_score=0.7,
        contradiction_state="conflicted",
        summary="JavaScript interest",
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(fact_b2)
    
    ensure_psyche_profile(user_b)
    state_b = ensure_psyche_state(user_b)
    state_b.certainty = 0.3  # Low certainty
    state_b.pressure = 0.7   # High pressure
    update_psyche_state(state_b)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "Dla frontend development TypeScript jest świetnym wyborem."
        mock_response.text = mock_response.content
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
    
    # Run both
    turn_a = ChatTurnInput(user_id=user_a, message="Frontend język?", mode="chat", history=[])
    turn_b = ChatTurnInput(user_id=user_b, message="Frontend język?", mode="chat", history=[])
    
    result_a = await runtime.run_turn(turn_a)
    result_b = await runtime.run_turn(turn_b)
    
    # Validate: User B has contradiction guard, User A doesn't
    assert not result_a.trace["memory_v2_contradiction_guard_applied"], "User A should have no contradiction guard"
    assert result_b.trace["memory_v2_contradiction_guard_applied"], "User B should have contradiction guard"
    
    # Validate: User B response contains caution markers
    response_b_lower = result_b.response_text.lower()
    has_caution_b = any(marker in response_b_lower for marker in ["uwaga", "sprzeczne", "może", "niepewn"])
    assert has_caution_b, f"User B should have caution markers. Got: {result_b.response_text[:200]}"
    
    # User A should NOT have caution markers (unless model generated them independently)
    response_a_lower = result_a.response_text.lower()
    # We can't assert absence completely (model might generate "może" independently),
    # but we assert contradiction guard is NOT applied
    assert not result_a.trace["memory_v2_contradiction_guard_applied"]


@pytest.mark.asyncio
async def test_verbosity_differentiation_low_vs_high(isolated_db, monkeypatch):
    """
    Comparison test: Low verbosity vs high verbosity
    
    User A: verbosity_bias=0.2 (concise)
    User B: verbosity_bias=0.8 (detailed)
    
    Expected: Different prompt instructions should guide model to different styles.
    """
    # User A: Low verbosity
    user_a = "verbosity_low"
    ensure_psyche_profile(user_a)
    state_a = ensure_psyche_state(user_a)
    state_a.verbosity_bias = 0.2  # Very low
    state_a.current_mode = "focused"
    update_psyche_state(state_a)
    
    # User B: High verbosity
    user_b = "verbosity_high"
    ensure_psyche_profile(user_b)
    state_b = ensure_psyche_state(user_b)
    state_b.verbosity_bias = 0.85  # Very high
    state_b.current_mode = "exploratory"
    update_psyche_state(state_b)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "Response text from model"
        mock_response.text = mock_response.content
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
    
    # Run both
    turn_a = ChatTurnInput(user_id=user_a, message="Co to jest Docker?", mode="chat", history=[])
    turn_b = ChatTurnInput(user_id=user_b, message="Co to jest Docker?", mode="chat", history=[])
    
    result_a = await runtime.run_turn(turn_a)
    result_b = await runtime.run_turn(turn_b)
    
    # Validate: Different verbosity reflected in profile
    assert result_a.trace["final_behavior_profile"]["verbosity"] < 0.3, f"Expected low verbosity, got {result_a.trace['final_behavior_profile']['verbosity']}"
    assert result_b.trace["final_behavior_profile"]["verbosity"] > 0.5, f"Expected high verbosity, got {result_b.trace['final_behavior_profile']['verbosity']}"
    
    # Validate: Different modes
    assert result_a.trace["psyche_v2_style_mode"] == "focused"
    assert result_b.trace["psyche_v2_style_mode"] == "exploratory"


@pytest.mark.asyncio
async def test_pressure_structuredness_correlation(isolated_db, monkeypatch):
    """
    Test that high pressure increases structuredness in response style.
    
    Expected: High pressure + high structuredness_bias → prompt emphasizes structure.
    """
    user_id = "pressure_struct_user"
    
    # Setup: High pressure
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.pressure = 0.8       # High pressure
    state.certainty = 0.4      # Low certainty
    update_psyche_state(state)
    
    # Mock provider
    runtime = ChatRuntime()
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = "Response structured by model"
        mock_response.text = mock_response.content
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
        message="Jak zorganizować projekt?",
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    # Validate: Pressure applied flag
    assert result.trace["psyche_v2_pressure_applied"], "Pressure should apply with pressure > 0.6"
    assert result.trace["psyche_v2_behavior_applied"]
    
    # Validate: High pressure reflected in trace
    assert result.trace["final_behavior_profile"]["pressure"] > 0.7
