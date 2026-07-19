#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adaptive runtime stage plan — shorten/skip stages by turn complexity."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from aihub.turn.prompt_budget import PromptBudgetDecision
    from aihub.turn.turn_signals import TurnSignals


@dataclass
class AdaptiveRuntimePlan:
    skip_reflection: bool = False
    skip_critic: bool = False
    skip_response_variants: bool = False
    skip_planner: bool = False
    skip_simulation: bool = False
    memory_pack_max_items: int = 6
    memory_pack_max_chars: int = 1800
    planner_max_nodes: int = 12
    max_tool_iterations: int | None = None
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_adaptive_runtime(
    signals: "TurnSignals",
    budget: "PromptBudgetDecision | None" = None,
    *,
    decision_core: dict[str, Any] | None = None,
) -> AdaptiveRuntimePlan:
    """Derive per-stage runtime plan from signals + budget profile."""
    dc = decision_core or {}
    profile = getattr(budget, "profile", None) or str(dc.get("budget_profile") or "")
    codes: list[str] = ["ADAPTIVE_RUNTIME_PLANNED"]

    skip_reflection = False
    skip_critic = False
    skip_variants = True
    skip_planner = True
    skip_sim = True
    pack_items = 6
    pack_chars = 1800
    planner_nodes = 12
    tool_iters: int | None = None

    if profile in ("meta_light", "casual_light"):
        skip_reflection = True
        skip_critic = profile == "meta_light"
        skip_variants = True
        skip_planner = True
        skip_sim = True
        pack_items = 0
        pack_chars = 0
        codes.append("ADAPT_LIGHT_SHORT_CIRCUIT")
    else:
        # High confidence + low complexity → drop post-exec heavy stages.
        if signals.confidence >= 0.72 and signals.complexity <= 0.35 and signals.uncertainty <= 0.4:
            skip_reflection = True
            codes.append("ADAPT_SKIP_REFLECTION_HIGH_CONF")
        if (
            signals.confidence >= 0.78
            and signals.uncertainty <= 0.3
            and signals.complexity <= 0.3
            and signals.tool_probability < 0.25
        ):
            skip_critic = True
            codes.append("ADAPT_SKIP_CRITIC_LOW_VALUE")

        # Memory pack sizing by usefulness / ROI / latency.
        if signals.memory_usefulness < 0.25 or signals.expected_token_roi < 0.3:
            pack_items = 2
            pack_chars = 700
            codes.append("ADAPT_MEMORY_PACK_MINIMAL")
        elif signals.memory_usefulness < 0.45 or signals.latency_budget_ms <= 2000:
            pack_items = 3
            pack_chars = 1100
            codes.append("ADAPT_MEMORY_PACK_SHORT")
        elif signals.memory_usefulness >= 0.7 and signals.expected_token_roi >= 0.65:
            pack_items = 8
            pack_chars = 2400
            codes.append("ADAPT_MEMORY_PACK_FULL")
        else:
            pack_items = 5
            pack_chars = 1600
            codes.append("ADAPT_MEMORY_PACK_STANDARD")

        # Planner only when agentic / plan-only / high complexity.
        strat = str(dc.get("selected_strategy") or "").lower()
        if strat == "agentic" or bool(dc.get("planner_recommended")) or signals.complexity >= 0.65:
            skip_planner = False
            planner_nodes = 12 if signals.complexity >= 0.7 else 6
            codes.append("ADAPT_PLANNER_ENABLED")
            if signals.complexity < 0.55 and signals.latency_budget_ms < 5000:
                planner_nodes = 4
                codes.append("ADAPT_PLANNER_BOUNDED")
        else:
            skip_planner = True
            planner_nodes = 0
            codes.append("ADAPT_PLANNER_SKIPPED")

        # Variants are a real deliberation path for agentic / high-uncertainty turns.
        if profile == "agentic" and getattr(budget, "allow_response_variants", False):
            if signals.complexity >= 0.4 or signals.uncertainty >= 0.35 or signals.novelty >= 0.45:
                skip_variants = False
                codes.append("ADAPT_VARIANTS_ON")
            else:
                skip_variants = True
                codes.append("ADAPT_VARIANTS_OFF")
        elif getattr(budget, "allow_response_variants", False) and signals.uncertainty >= 0.55:
            skip_variants = False
            codes.append("ADAPT_VARIANTS_UNCERTAINTY")

        # Simulation stays active on research/agentic whenever budget allows it.
        if profile in ("research", "agentic") and getattr(budget, "allow_simulation", False):
            skip_sim = False
            codes.append("ADAPT_SIMULATION_ON")
        elif getattr(budget, "allow_simulation", False) and signals.complexity >= 0.55:
            skip_sim = False
            codes.append("ADAPT_SIMULATION_COMPLEXITY")

        if not getattr(budget, "allow_tools", True):
            tool_iters = 0
            codes.append("ADAPT_TOOLS_OFF")
        elif signals.latency_budget_ms <= 2500 and signals.tool_probability < 0.35:
            tool_iters = 1
            codes.append("ADAPT_TOOLS_ONE_ITER")
        elif signals.complexity >= 0.7 or signals.tool_probability >= 0.55:
            tool_iters = 3
        else:
            tool_iters = 2

    # Budget flags can force skips.
    if budget is not None:
        if not getattr(budget, "allow_critic_llm", True):
            skip_critic = True
        if not getattr(budget, "allow_response_variants", False):
            skip_variants = True
        if not getattr(budget, "allow_simulation", False):
            skip_sim = True
        if getattr(budget, "skip_reflection", False):
            skip_reflection = True
        if getattr(budget, "skip_critic", False):
            skip_critic = True
        if getattr(budget, "memory_pack_max_items", None) is not None:
            pack_items = min(pack_items, int(budget.memory_pack_max_items))
        if getattr(budget, "memory_pack_max_chars", None) is not None:
            pack_chars = min(pack_chars, int(budget.memory_pack_max_chars))
        if getattr(budget, "planner_max_nodes", None) is not None:
            planner_nodes = min(planner_nodes, int(budget.planner_max_nodes))

    return AdaptiveRuntimePlan(
        skip_reflection=skip_reflection,
        skip_critic=skip_critic,
        skip_response_variants=skip_variants,
        skip_planner=skip_planner,
        skip_simulation=skip_sim,
        memory_pack_max_items=max(0, pack_items),
        memory_pack_max_chars=max(0, pack_chars),
        planner_max_nodes=max(0, planner_nodes),
        max_tool_iterations=tool_iters,
        reason_codes=codes,
    )


def truncate_memory_pack_in_context(
    system_context: dict[str, Any] | None,
    plan: AdaptiveRuntimePlan,
) -> dict[str, Any]:
    """Apply adaptive pack limits to an already-built memory context pack."""
    if not isinstance(system_context, dict):
        return {"applied": False, "reason": "no_context"}
    if plan.memory_pack_max_items <= 0 or plan.memory_pack_max_chars <= 0:
        system_context["memory_context_pack_prompt"] = ""
        system_context["memory_context_pack"] = {
            "selected_ids": [],
            "used_chars": 0,
            "source_distribution": {},
            "adaptive_truncated": True,
        }
        system_context["memory_context_pack_trace"] = {
            "selected_count": 0,
            "used_chars": 0,
            "skipped": "ADAPTIVE_MEMORY_PACK_SKIPPED",
        }
        return {"applied": True, "cleared": True}

    prompt = str(system_context.get("memory_context_pack_prompt") or "")
    if len(prompt) > plan.memory_pack_max_chars:
        system_context["memory_context_pack_prompt"] = (
            prompt[: max(0, plan.memory_pack_max_chars - 24)].rstrip() + "\n…[adaptive truncate]"
        )

    pack = system_context.get("memory_context_pack")
    if isinstance(pack, dict):
        selected = list(pack.get("selected_ids") or [])
        if len(selected) > plan.memory_pack_max_items:
            keep = selected[: plan.memory_pack_max_items]
            pack["selected_ids"] = keep
            pack["adaptive_truncated"] = True
            pack["adaptive_kept"] = len(keep)
            # Drop surplus from typed lists if present.
            for key in ("facts", "preferences", "procedures", "episodes", "contradictions", "other"):
                items = pack.get(key)
                if isinstance(items, list) and items:
                    pack[key] = [
                        it
                        for it in items
                        if isinstance(it, dict) and it.get("id") in set(keep)
                    ][: plan.memory_pack_max_items]
            system_context["memory_context_pack"] = pack
            # Rebuild prompt from kept lines when possible.
            lines = [ln for ln in prompt.splitlines() if any(f"[{sid}]" in ln for sid in keep)]
            if lines:
                header = "PAMIĘĆ KONTEKSTOWA (adaptive):"
                rebuilt = header + "\n" + "\n".join(lines)
                system_context["memory_context_pack_prompt"] = rebuilt[
                    : plan.memory_pack_max_chars
                ]
        pack["used_chars"] = len(str(system_context.get("memory_context_pack_prompt") or ""))
        system_context["memory_context_pack"] = pack

    return {
        "applied": True,
        "max_items": plan.memory_pack_max_items,
        "max_chars": plan.memory_pack_max_chars,
    }
