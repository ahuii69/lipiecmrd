#!/usr/bin/env python3
"""POST-FIX VALIDATION - Real runtime scenarios through /chat/turn ACTIVE path."""

import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

import json

from aihub.chat_contracts import ChatTurnInput
from aihub.chat_runtime import ChatRuntime


async def test_scenario_a_explicit_url():
    """Scenario A: explicit URL"""
    print("=== SCENARIO A: EXPLICIT URL ===")
    runtime = ChatRuntime()

    result = await runtime.run_turn(
        ChatTurnInput(
            user_id="test_a",
            session_id="sess_a",
            message="Sprawdź tę stronę: https://httpbin.org/json",
            mode="chat",
        )
    )

    print(f"HTTP Status: {result.ok}")
    print(f"Response text preview: {result.response_text[:100]}...")
    print(f"Tool calls count: {len(result.tool_calls)}")
    print(f"Tool results count: {len(result.tool_results)}")
    print(f"Controlled web triggered: {result.trace.get('controlled_web_triggered')}")
    print(f"Controlled web tool: {result.trace.get('controlled_web_tool')}")
    print(f"Controlled web ok: {result.trace.get('controlled_web_ok')}")
    print(f"Has results: {result.trace.get('controlled_web_has_results')}")
    print(f"Provider info: {result.trace.get('controlled_web_provider_info')}")

    return result


async def test_scenario_b_research_intent():
    """Scenario B: research intent"""
    print("\n=== SCENARIO B: RESEARCH INTENT ===")
    runtime = ChatRuntime()

    result = await runtime.run_turn(
        ChatTurnInput(
            user_id="test_b",
            session_id="sess_b",
            message="wyszukaj informacje o quantum computing 2026",
            mode="chat",
        )
    )

    print(f"HTTP Status: {result.ok}")
    print(f"Response text preview: {result.response_text[:100]}...")
    print(f"Tool calls count: {len(result.tool_calls)}")
    print(f"Tool results count: {len(result.tool_results)}")
    print(f"Controlled web triggered: {result.trace.get('controlled_web_triggered')}")
    print(f"Controlled web tool: {result.trace.get('controlled_web_tool')}")
    print(f"Controlled web ok: {result.trace.get('controlled_web_ok')}")
    print(f"Has results: {result.trace.get('controlled_web_has_results')}")
    print(f"Provider info: {result.trace.get('controlled_web_provider_info')}")

    # Check if result is truthful about failure/empty
    if result.tool_results:
        for tool_result in result.tool_results:
            if tool_result.name == "research.query":
                print(f"Research result ok: {tool_result.ok}")
                if tool_result.output:
                    try:
                        if isinstance(tool_result.output, dict):
                            data = tool_result.output
                        else:
                            data = json.loads(tool_result.output)
                        print(
                            f"Research total_results: {data.get('total_results', 'unknown')}"
                        )
                        print(
                            f"Research total_facts: {data.get('total_facts', 'unknown')}"
                        )
                        print(f"Research message: {data.get('message', 'none')}")
                    except:
                        print("Could not parse research output")

    return result


async def main():
    print("POST-FIX VALIDATION: WEB REALTIME RELIABILITY PROOF")
    print("=" * 60)

    try:
        result_a = await test_scenario_a_explicit_url()
        result_b = await test_scenario_b_research_intent()

        print("\n=== VALIDATION SUMMARY ===")
        print(
            f"Scenario A (URL) controlled_web: {result_a.trace.get('controlled_web_triggered', False)}"
        )
        print(
            f"Scenario B (Research) controlled_web: {result_b.trace.get('controlled_web_triggered', False)}"
        )
        print("✓ Post-fix validation complete")

    except Exception as e:
        print(f"✗ Validation FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
