"""Executable lesson machine actions — the real consumer of learned lessons."""

from __future__ import annotations

from typing import Any

from aihub.adaptive_learning.models import CausalAttribution, LearnedLesson, TurnOutcomeEvaluation

# Canonical action vocabulary (bounded — anti-drift)
MACHINE_ACTIONS = frozenset(
    {
        "increase_strategy_bias",
        "decrease_strategy_bias",
        "force_contextual",
        "prefer_research",
        "prefer_tool",
        "avoid_tool",
        "prefer_query_pattern",
        "avoid_query_pattern",
        "reduce_verbosity",
        "increase_verbosity",
        "increase_reasoning",
        "prefer_provider",
        "avoid_provider",
        "remember_rejected_option",
        "require_memory_lookup",
        "prefer_planner",
        "avoid_instant",
    }
)


def map_attribution_to_machine_action(
    *,
    attr: CausalAttribution,
    outcome: TurnOutcomeEvaluation,
) -> tuple[str, dict[str, Any]]:
    """Map causal factor → executable action + payload."""
    factor = (attr.factor or "").lower()
    neg = attr.contribution_score < 0
    payload: dict[str, Any] = {
        "strategy": outcome.selected_strategy,
        "intent": outcome.primary_intent,
        "factor": attr.factor,
    }
    if "web" in factor or "query" in factor or "research" in factor or "source" in factor:
        if neg:
            return "prefer_research", {**payload, "reason": "web_path_weak"}
        return "prefer_query_pattern", {**payload, "pattern": "semantic_rewrite"}
    if "strategy" in factor:
        if neg and outcome.selected_strategy == "instant":
            return "force_contextual", payload
        if neg:
            return "decrease_strategy_bias", {**payload, "target": outcome.selected_strategy}
        return "increase_strategy_bias", {**payload, "target": outcome.selected_strategy}
    if "tool" in factor:
        return ("avoid_tool" if neg else "prefer_tool"), payload
    if "provider" in factor:
        return ("avoid_provider" if neg else "prefer_provider"), payload
    if "planner" in factor:
        return ("prefer_planner" if not neg else "force_contextual"), payload
    if "verbosity" in factor or "style" in factor or "persona" in factor:
        return ("reduce_verbosity" if neg else "increase_verbosity"), payload
    if "pragmatics" in factor or "intent" in factor:
        return "increase_reasoning", payload
    if "memory" in factor or "missing context" in factor:
        return "require_memory_lookup", payload
    if neg:
        return "avoid_instant", payload
    return "increase_strategy_bias", payload


def apply_machine_actions(
    *,
    decision_core: dict[str, Any],
    lessons: list[LearnedLesson],
    codes: list[str],
) -> bool:
    """Mutate decision_core from lesson machine_actions. Returns whether influenced."""
    influenced = False
    applied: list[dict[str, Any]] = []
    strategy = str(decision_core.get("selected_strategy") or "contextual")
    web_decision = str(decision_core.get("web_decision") or "off")

    for lesson in lessons:
        action = str(getattr(lesson, "machine_action", "") or "").strip()
        if not action or action not in MACHINE_ACTIONS:
            # Legacy fallback: parse statement
            st = (lesson.statement or "").lower()
            if "prefer research" in st or "prefer: factor=web" in st:
                action = "prefer_research"
            elif "force_contextual" in st or "avoid instant" in st:
                action = "avoid_instant"
            elif "reduce_verbosity" in st or "verbosity" in st:
                action = "reduce_verbosity"
            else:
                continue
        if lesson.confidence < 0.4:
            continue

        payload = {
            "lesson_id": lesson.lesson_id,
            "action": action,
            "confidence": lesson.confidence,
        }
        if action in ("force_contextual", "avoid_instant") and strategy == "instant":
            if web_decision != "required":
                decision_core["selected_strategy"] = "contextual"
                codes.append("LEARN_ACTION_FORCE_CONTEXTUAL")
                influenced = True
        elif action == "prefer_research":
            if strategy == "instant" or (
                web_decision == "off" and lesson.confidence >= 0.55
            ):
                decision_core["selected_strategy"] = "research"
                if web_decision == "off":
                    decision_core["web_decision"] = "optional"
                codes.append("LEARN_ACTION_PREFER_RESEARCH")
                influenced = True
        elif action == "decrease_strategy_bias":
            bias = dict(decision_core.get("learning_strategy_bias") or {})
            target = str(lesson.applicable_strategies[0] if lesson.applicable_strategies else strategy)
            bias[target] = float(bias.get(target, 0.0)) - min(0.08, 0.04 + 0.02 * lesson.confidence)
            decision_core["learning_strategy_bias"] = bias
            codes.append("LEARN_ACTION_DECREASE_BIAS")
            influenced = True
        elif action == "increase_strategy_bias":
            bias = dict(decision_core.get("learning_strategy_bias") or {})
            target = str(lesson.applicable_strategies[0] if lesson.applicable_strategies else strategy)
            bias[target] = float(bias.get(target, 0.0)) + min(0.08, 0.03 + 0.02 * lesson.confidence)
            decision_core["learning_strategy_bias"] = bias
            codes.append("LEARN_ACTION_INCREASE_BIAS")
            influenced = True
        elif action == "reduce_verbosity":
            decision_core["learning_length_directive"] = "short"
            codes.append("LEARN_ACTION_REDUCE_VERBOSITY")
            influenced = True
        elif action == "increase_verbosity":
            decision_core["learning_length_directive"] = "long"
            codes.append("LEARN_ACTION_INCREASE_VERBOSITY")
            influenced = True
        elif action == "increase_reasoning":
            decision_core["escalation_use_reasoning"] = True
            decision_core["planner_recommended"] = True
            codes.append("LEARN_ACTION_INCREASE_REASONING")
            influenced = True
        elif action == "prefer_planner":
            decision_core["planner_recommended"] = True
            if strategy == "instant":
                decision_core["selected_strategy"] = "contextual"
            codes.append("LEARN_ACTION_PREFER_PLANNER")
            influenced = True
        elif action == "require_memory_lookup":
            decision_core["learning_require_memory"] = True
            codes.append("LEARN_ACTION_REQUIRE_MEMORY")
            influenced = True
        elif action == "prefer_tool":
            order = list(decision_core.get("tool_order_hint") or [])
            for t in lesson.applicable_tools:
                fam = str(t).split(".", 1)[0]
                if fam and fam not in order:
                    order.insert(0, fam)
            if order:
                decision_core["tool_order_hint"] = order
                codes.append("LEARN_ACTION_PREFER_TOOL")
                influenced = True
        elif action == "avoid_tool":
            avoid = list(decision_core.get("learning_avoid_tools") or [])
            avoid.extend(lesson.applicable_tools)
            decision_core["learning_avoid_tools"] = list(dict.fromkeys(avoid))[:8]
            codes.append("LEARN_ACTION_AVOID_TOOL")
            influenced = True
        elif action in ("prefer_provider", "avoid_provider"):
            decision_core["provider_learning_lesson"] = action
            codes.append(f"LEARN_ACTION_{action.upper()}")
            influenced = True
        elif action in ("prefer_query_pattern", "avoid_query_pattern"):
            decision_core["research_learning_action"] = action
            codes.append(f"LEARN_ACTION_{action.upper()}")
            influenced = True
        elif action == "remember_rejected_option":
            codes.append("LEARN_ACTION_REMEMBER_REJECTED")
            influenced = True

        applied.append(payload)
        strategy = str(decision_core.get("selected_strategy") or strategy)

    if applied:
        decision_core["learning_machine_actions_applied"] = applied[:8]
        codes.append("LEARN_MACHINE_ACTIONS_EXECUTED")
    return influenced
