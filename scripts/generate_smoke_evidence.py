#!/usr/bin/env python3
"""
Behavior Calibration Smoke Evidence Generator

Creates before/after examples showing real behavioral differentiation
across 6 calibration scenarios.
"""

import asyncio
from unittest.mock import MagicMock

from aihub.chat_runtime import ChatRuntime
from aihub.chat_contracts import ChatTurnInput
from aihub.memory_v2_repository import insert_memory_item, insert_memory_procedure
from aihub.memory_v2_models import MemoryV2Item, MemoryV2Procedure
from aihub.psyche_v2_repository import ensure_psyche_profile, ensure_psyche_state, update_psyche_state, update_psyche_profile
from aihub.db import init_db, now_ts


async def run_scenario(
    user_id: str,
    message: str,
    setup_fn,
    mock_model_response: str,
) -> dict:
    """Run a single scenario and return trace + response."""
    # Setup state
    setup_fn(user_id)
    
    # Mock provider
    runtime = ChatRuntime()
    
    original_generate = runtime._provider.generate
    
    async def mock_generate(req):
        mock_response = MagicMock()
        mock_response.content = mock_model_response
        mock_response.text = mock_model_response
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
    
    runtime._provider.generate = mock_generate
    
    # Run turn
    turn_input = ChatTurnInput(
        user_id=user_id,
        message=message,
        mode="chat",
        history=[],
    )
    
    result = await runtime.run_turn(turn_input)
    
    return {
        "user_id": user_id,
        "message": message,
        "response_text": result.response_text,
        "trace": {
            "memory_v2_context_injected": result.trace["memory_v2_context_injected"],
            "memory_v2_contradiction_guard_applied": result.trace["memory_v2_contradiction_guard_applied"],
            "memory_v2_procedure_bias_applied": result.trace["memory_v2_procedure_bias_applied"],
            "psyche_v2_behavior_applied": result.trace["psyche_v2_behavior_applied"],
            "psyche_v2_style_mode": result.trace["psyche_v2_style_mode"],
            "psyche_v2_pressure_applied": result.trace["psyche_v2_pressure_applied"],
            "psyche_v2_relation_tone_applied": result.trace["psyche_v2_relation_tone_applied"],
            "final_behavior_profile": result.trace["final_behavior_profile"],
        },
    }


def setup_scenario_a_contradictions_caution(user_id: str):
    """Scenario A: High contradictions + high caution."""
    ts = now_ts()
    
    fact1 = MemoryV2Item(
        id=f"{user_id}_f1",
        user_id=user_id,
        memory_type="fact",
        scope="user",
        title="Prefers Python",
        content="User said Python is best",
        source_kind="chat_turn",
        importance_score=0.8,
        salience_score=0.8,
        contradiction_state="conflicted",
        summary="Python pref",
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(fact1)
    
    fact2 = MemoryV2Item(
        id=f"{user_id}_f2",
        user_id=user_id,
        memory_type="fact",
        scope="user",
        title="Recently asked about Java",
        content="User explored Java frameworks",
        source_kind="chat_turn",
        importance_score=0.7,
        salience_score=0.7,
        contradiction_state="conflicted",
        summary="Java interest",
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_item(fact2)
    
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.certainty = 0.3
    state.pressure = 0.7
    update_psyche_state(state)


def setup_scenario_b_high_procedure_confidence(user_id: str):
    """Scenario B: High procedure confidence + low pressure."""
    ts = now_ts()
    
    proc = MemoryV2Procedure(
        id=f"{user_id}_proc",
        user_id=user_id,
        name="Deploy to Azure",
        trigger_pattern="deploy.*azure",
        recommended_strategy="azd up",
        confidence_score=0.92,
        evidence_count=12,
        success_count=11,
        failure_count=1,
        last_used_ts=ts,
        created_ts=ts,
        updated_ts=ts,
    )
    insert_memory_procedure(proc)
    
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.certainty = 0.85
    state.pressure = 0.2
    state.current_mode = "focused"
    update_psyche_state(state)


def setup_scenario_c_high_friction(user_id: str):
    """Scenario C: High friction + low trust."""
    profile = ensure_psyche_profile(user_id)
    profile.relation_friction = 0.85
    profile.relation_trust = 0.3
    update_psyche_profile(profile)
    
    state = ensure_psyche_state(user_id)
    state.certainty = 0.5
    update_psyche_state(state)


def setup_scenario_d_high_warmth_trust(user_id: str):
    """Scenario D: High warmth + high trust."""
    profile = ensure_psyche_profile(user_id)
    profile.relation_warmth = 0.85
    profile.relation_trust = 0.8
    profile.core_warmth = 0.8
    update_psyche_profile(profile)
    
    state = ensure_psyche_state(user_id)
    state.certainty = 0.75
    state.pressure = 0.2
    update_psyche_state(state)


def setup_scenario_e_focused_high_conf(user_id: str):
    """Scenario E: Focused mode + high procedure confidence."""
    ts = now_ts()
    
    proc = MemoryV2Procedure(
        id=f"{user_id}_proc",
        user_id=user_id,
        name="Run pytest",
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
    insert_memory_procedure(proc)
    
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.current_mode = "focused"
    state.certainty = 0.85
    state.verbosity_bias = 0.2
    update_psyche_state(state)


def setup_scenario_f_exploratory_web(user_id: str):
    """Scenario F: Exploratory mode + high web bias."""
    ensure_psyche_profile(user_id)
    state = ensure_psyche_state(user_id)
    state.current_mode = "exploratory"
    state.web_bias = 0.85
    state.tool_bias = 0.8
    state.verbosity_bias = 0.65
    update_psyche_state(state)


async def main():
    """Generate smoke evidence for all scenarios."""
    init_db()
    
    scenarios = [
        {
            "id": "scenario_a",
            "name": "High Contradictions + High Caution",
            "setup": setup_scenario_a_contradictions_caution,
            "message": "Jakiego języka użyć do projektu?",
            "model_response": "Python jest najlepszym wyborem dla tego projektu.",
        },
        {
            "id": "scenario_b",
            "name": "High Procedure Confidence + Low Pressure",
            "setup": setup_scenario_b_high_procedure_confidence,
            "message": "Jak wdrożyć aplikację na Azure?",
            "model_response": "Warto rozważyć różne opcje wdrożenia.",
        },
        {
            "id": "scenario_c",
            "name": "High Friction + Low Trust",
            "setup": setup_scenario_c_high_friction,
            "message": "Jak to działa?",
            "model_response": "System wykorzystuje kilka mechanizmów.",
        },
        {
            "id": "scenario_d",
            "name": "High Warmth + High Trust",
            "setup": setup_scenario_d_high_warmth_trust,
            "message": "Co słychać?",
            "model_response": "Wszystko działa poprawnie.",
        },
        {
            "id": "scenario_e",
            "name": "Focused Mode + High Confidence",
            "setup": setup_scenario_e_focused_high_conf,
            "message": "Jak uruchomić testy?",
            "model_response": "Można uruchomić testy za pomocą różnych frameworków.",
        },
        {
            "id": "scenario_f",
            "name": "Exploratory Mode + High Web Bias",
            "setup": setup_scenario_f_exploratory_web,
            "message": "Co nowego w AI?",
            "model_response": "AI rozwija się w wielu kierunkach.",
        },
    ]
    
    print("=" * 80)
    print("BEHAVIOR CALIBRATION SMOKE EVIDENCE")
    print("=" * 80)
    print()
    
    for scenario in scenarios:
        print(f"## {scenario['name']}")
        print(f"User: {scenario['message']}")
        print(f"Model (before runtime shaping): {scenario['model_response']}")
        print()
        
        result = await run_scenario(
            user_id=scenario["id"],
            message=scenario["message"],
            setup_fn=scenario["setup"],
            mock_model_response=scenario["model_response"],
        )
        
        print(f"Runtime (after behavior injection): {result['response_text'][:200]}...")
        print()
        print("Trace Flags:")
        trace = result["trace"]
        print(f"  memory_v2_context_injected: {trace['memory_v2_context_injected']}")
        print(f"  memory_v2_contradiction_guard_applied: {trace['memory_v2_contradiction_guard_applied']}")
        print(f"  memory_v2_procedure_bias_applied: {trace['memory_v2_procedure_bias_applied']}")
        print(f"  psyche_v2_behavior_applied: {trace['psyche_v2_behavior_applied']}")
        print(f"  psyche_v2_style_mode: {trace['psyche_v2_style_mode']}")
        print(f"  psyche_v2_pressure_applied: {trace['psyche_v2_pressure_applied']}")
        print(f"  psyche_v2_relation_tone_applied: {trace['psyche_v2_relation_tone_applied']}")
        print()
        print("Final Behavior Profile:")
        profile = trace["final_behavior_profile"]
        if profile:
            print(f"  mode: {profile.get('mode', 'N/A')}")
            print(f"  caution: {profile.get('caution', 0):.2f}")
            print(f"  directness: {profile.get('directness', 0):.2f}")
            print(f"  verbosity: {profile.get('verbosity', 0):.2f}")
            print(f"  pressure: {profile.get('pressure', 0):.2f}")
            print(f"  friction: {profile.get('friction', 0):.2f}")
            print(f"  trust: {profile.get('trust', 0):.2f}")
            print(f"  warmth: {profile.get('warmth', 0):.2f}")
            print(f"  web_bias: {profile.get('web_bias', 0):.2f}")
            print(f"  tool_bias: {profile.get('tool_bias', 0):.2f}")
        else:
            print("  (empty)")
        print()
        print("-" * 80)
        print()
    
    print("=" * 80)
    print("SMOKE EVIDENCE COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
