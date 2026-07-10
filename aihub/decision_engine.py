#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Escalation: map strategy selector output → runtime execution path."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

StrategyName = Literal["instant", "contextual", "research", "agentic"]
FinalMode = Literal["direct", "memory_augmented", "research", "planner"]


class EscalationPath(TypedDict):
    final_mode: FinalMode
    use_reasoning: bool
    use_tools: bool


def _coerce_strategy(value: str) -> StrategyName:
    v = (value or "instant").strip().lower()
    if v in ("instant", "contextual", "research", "agentic"):
        return v  # type: ignore[return-value]
    return "instant"


def decide_execution_path(strategy_output: dict[str, Any]) -> EscalationPath:
    """Map classifier output to execution flags.

    Mapping (fixed product contract):
    - instant → direct
    - contextual → memory_augmented
    - research → research
    - agentic → planner + reasoning
    """
    s = _coerce_strategy(str(strategy_output.get("strategy", "instant")))
    if s == "instant":
        return {
            "final_mode": "direct",
            "use_reasoning": False,
            "use_tools": False,
        }
    if s == "contextual":
        return {
            "final_mode": "memory_augmented",
            "use_reasoning": False,
            "use_tools": True,
        }
    if s == "research":
        return {
            "final_mode": "research",
            "use_reasoning": False,
            "use_tools": True,
        }
    return {
        "final_mode": "planner",
        "use_reasoning": True,
        "use_tools": True,
    }


def build_strategy_output_for_escalation(
    *,
    strategy: str,
    confidence: float | None,
    requires_memory: bool,
    requires_research: bool,
    requires_planning: bool,
    reason: str,
) -> dict[str, Any]:
    """Stable dict shape for :func:`decide_execution_path` after pipeline overrides."""
    return {
        "strategy": _coerce_strategy(strategy),
        "confidence": float(confidence) if confidence is not None else 0.7,
        "requires_memory": bool(requires_memory),
        "requires_research": bool(requires_research),
        "requires_planning": bool(requires_planning),
        "reason": str(reason or ""),
    }
