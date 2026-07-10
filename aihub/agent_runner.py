#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""High-level synchronous runner for one-shot autonomous execution."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, List

from .executive_controller import build_agent_cycle_response, get_executive_controller
from .vector_engine import search


async def run_agent_async(
    text: str,
    user_id: str = "default",
    max_steps: int = 8,
    timeout_seconds: float = 20.0,
) -> Dict[str, Any]:
    """Native async API for ExecutiveController runtime."""
    message = (text or "").strip()
    if not message:
        return {"ok": False, "error": "empty text"}

    controller = get_executive_controller()
    cycle = await controller.run_cycle(
        {
            "text": message,
            "max_steps": int(max_steps),
            "timeout_seconds": float(timeout_seconds),
        },
        mode="run",
        user_id=user_id,
    )
    normalized = build_agent_cycle_response(cycle)

    return {
        "ok": bool(normalized.get("ok", False)),
        "result": normalized,
        "reasoning": cycle.get("execution_result", {}).get("payload", {}),
        "vector_memory": search(message, user_id=user_id),
    }


def run_agent(
    text: str,
    user_id: str = "default",
    max_steps: int = 8,
    timeout_seconds: float = 20.0,
) -> Dict[str, Any]:
    """Legacy synchronous adapter over canonical ExecutiveController runtime.

    Handles both sync and async contexts:
    - If called from sync context: uses asyncio.run()
    - If called from active event loop: spawns thread with isolated event loop
    """
    message = (text or "").strip()
    if not message:
        return {"ok": False, "error": "empty text"}

    # Check if we're already in an active event loop
    try:
        asyncio.get_running_loop()
        # We're in an active loop — must run in separate thread
        result_container: Dict[str, Any] = {}
        error_container: List[Exception] = []

        def run_in_thread() -> None:
            try:
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    result_container["result"] = new_loop.run_until_complete(
                        run_agent_async(
                            text=text,
                            user_id=user_id,
                            max_steps=max_steps,
                            timeout_seconds=timeout_seconds,
                        )
                    )
                finally:
                    new_loop.close()
            except Exception as e:  # noqa: BLE001
                error_container.append(e)

        thread = threading.Thread(target=run_in_thread, daemon=False)
        thread.start()
        thread.join()

        if error_container:
            raise error_container[0]
        return result_container.get(
            "result", {"ok": False, "error": "thread execution failed"}
        )

    except RuntimeError:
        # No running loop — safe to use asyncio.run()
        return asyncio.run(
            run_agent_async(
                text=text,
                user_id=user_id,
                max_steps=max_steps,
                timeout_seconds=timeout_seconds,
            )
        )
