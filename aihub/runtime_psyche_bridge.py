#!/usr/bin/env python3
"""
Runtime Psyche V2 Bridge.

Provides read-only snapshots of Psyche V2 for active runtime consumption,
self-consistency dampening, and operational drift signals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from aihub.memory_psyche_contracts import (
    MemoryV2RuntimeContext,
    PsycheV2BehaviorContext,
    SelfConsistencyDecision,
)
from aihub.psyche_core import get_psyche_core

logger = logging.getLogger(__name__)


def _canonical_v2() -> Any:
    """Process-wide :class:`PsycheV2Service` (same instance as HTTP v2 and cockpit)."""
    return get_psyche_core().v2_service


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _habit_stability_from_habits(habits: list[Any]) -> float:
    """Higher when habits are repeated (reinforcement) and moderately intense."""
    if not habits:
        return 0.5
    parts: list[float] = []
    for h in habits:
        rc = int(getattr(h, "reinforcement_count", 0) or 0)
        intensity = float(getattr(h, "intensity", 0.0) or 0.0)
        sample = min(1.0, rc / 8.0) * 0.55 + min(1.0, intensity) * 0.45
        parts.append(sample)
    return _clamp(sum(parts) / len(parts))


def _psyche_drift_score(profile: Any, state: Any) -> float:
    """How far transient state diverges from long-term profile baselines."""
    cert_gap = abs(float(state.certainty) - float(profile.confidence_baseline))
    press = float(
        state.pressure_smoothed
        if getattr(state, "pressure_smoothed", 0.0) > 0.0
        else state.pressure
    )
    press_gap = abs(press - float(profile.stress_load))
    mood_gap = abs(float(state.mood) - 0.5) * 0.4
    return _clamp(cert_gap * 0.42 + press_gap * 0.38 + mood_gap * 0.2)


def _relation_drift_score(profile: Any) -> float:
    return _clamp(
        abs(
            float(profile.relation_trust)
            - float(profile.relation_interaction_quality_ema)
        )
    )


@dataclass
class ConsistencyResult:
    decision: SelfConsistencyDecision
    reasons: list[str] = field(default_factory=list)
    caution_scale: float = 1.0
    procedure_conf_scale: float = 1.0
    pressure_scale: float = 1.0
    structuredness_scale: float = 1.0
    verbosity_scale: float = 1.0
    trust_scale: float = 1.0
    directness_scale: float = 1.0


def evaluate_self_consistency(
    *,
    memory_ctx: MemoryV2RuntimeContext | None,
    psyche_ctx: PsycheV2BehaviorContext | None,
    core_caution_baseline: float = 0.5,
) -> ConsistencyResult:
    """
    Detect transient noise overriding long-horizon identity.

    Operational decisions: allow | dampen | suppress | promote_later.
    """
    reasons: list[str] = []
    decision: SelfConsistencyDecision = "allow"
    caution_scale = 1.0
    proc_scale = 1.0
    pressure_scale = 1.0
    struct_scale = 1.0
    verbosity_scale = 1.0
    trust_scale = 1.0
    directness_scale = 1.0

    if not memory_ctx and not psyche_ctx:
        return ConsistencyResult(decision="allow")

    stable_alerts = (
        len(memory_ctx.contradiction_alerts) if memory_ctx and memory_ctx.loaded else 0
    )
    transient_hints = (
        len(memory_ctx.transient_contradiction_hints)
        if memory_ctx and memory_ctx.loaded
        else 0
    )

    if stable_alerts == 0 and transient_hints > 0 and psyche_ctx and psyche_ctx.loaded:
        if psyche_ctx.caution_bias > 0.55:
            caution_scale *= 0.82
            reasons.append("contradiction_signal_mostly_transient_dampen_caution")
            decision = "dampen"

    if memory_ctx and memory_ctx.loaded and memory_ctx.confidence_modifier_raw > 0:
        if (
            memory_ctx.confidence_modifier_raw > 0.75
            and memory_ctx.confidence_modifier < 0.5
        ):
            proc_scale *= 0.95
            reasons.append("procedure_confidence_sample_dampened")
            if decision == "allow":
                decision = "dampen"

    max_proc_ev = 0
    if memory_ctx and memory_ctx.loaded and memory_ctx.top_procedures:
        max_proc_ev = max(
            int(p.get("evidence_count") or 0) for p in memory_ctx.top_procedures
        )
    if memory_ctx and memory_ctx.loaded and max_proc_ev > 0 and max_proc_ev < 3:
        if memory_ctx.confidence_modifier_raw > 0.72:
            proc_scale *= 0.88
            reasons.append("procedure_raw_overconfidence_small_evidence")
            decision = "dampen" if decision == "allow" else decision

    if psyche_ctx and psyche_ctx.loaded:
        ema_q = psyche_ctx.relation_quality_ema
        if psyche_ctx.pressure > 0.65 and ema_q > 0.55:
            pressure_scale *= 0.75
            struct_scale *= 0.92
            reasons.append("pressure_spike_against_positive_relation_ema_dampen")
            decision = "dampen" if decision == "allow" else decision

        if psyche_ctx.caution_bias > 0.78 and core_caution_baseline < 0.45:
            caution_scale *= 0.88
            reasons.append("caution_above_core_baseline_dampen")
            decision = "dampen" if decision == "allow" else decision

        drift = psyche_ctx.psyche_drift_score
        if (
            drift > 0.38
            and psyche_ctx.caution_bias > 0.74
            and core_caution_baseline < 0.48
        ):
            caution_scale *= 0.84
            verbosity_scale *= 0.96
            directness_scale *= 0.97
            reasons.append("transient_state_vs_identity_profile_dampen")
            decision = "dampen" if decision == "allow" else decision

    if (
        memory_ctx
        and memory_ctx.loaded
        and psyche_ctx
        and psyche_ctx.loaded
        and len(memory_ctx.contradiction_alerts) == 0
        and len(memory_ctx.transient_contradiction_hints) >= 2
        and psyche_ctx.caution_bias > 0.72
        and core_caution_baseline < 0.42
    ):
        caution_scale *= 0.78
        struct_scale *= 0.94
        reasons.append("transient_contradiction_overhang_suppress_caution")
        decision = "suppress"

    if memory_ctx and memory_ctx.loaded:
        sc = memory_ctx.stability_tier_counts or {}
        if sc.get("developing", 0) >= 2 and sc.get("stable", 0) == 0:
            proc_scale *= 0.92
            reasons.append("multiple_developing_items_promote_later_not_identity_lock")
            if decision == "allow":
                decision = "promote_later"

    return ConsistencyResult(
        decision=decision,
        reasons=reasons,
        caution_scale=caution_scale,
        procedure_conf_scale=proc_scale,
        pressure_scale=pressure_scale,
        structuredness_scale=struct_scale,
        verbosity_scale=verbosity_scale,
        trust_scale=trust_scale,
        directness_scale=directness_scale,
    )


def apply_consistency_to_contexts(
    memory_ctx: MemoryV2RuntimeContext | None,
    psyche_ctx: PsycheV2BehaviorContext | None,
    core_caution_baseline: float = 0.5,
) -> tuple[
    MemoryV2RuntimeContext | None, PsycheV2BehaviorContext | None, ConsistencyResult
]:
    """Apply consistency scales to runtime contexts (mutates in place)."""
    result = evaluate_self_consistency(
        memory_ctx=memory_ctx,
        psyche_ctx=psyche_ctx,
        core_caution_baseline=core_caution_baseline,
    )

    if memory_ctx and memory_ctx.loaded:
        memory_ctx.confidence_modifier = _clamp(
            memory_ctx.confidence_modifier * result.procedure_conf_scale
        )
        memory_ctx.self_consistency_notes = list(result.reasons)
        memory_ctx.consistency_decision = result.decision

    if psyche_ctx and psyche_ctx.loaded:
        psyche_ctx.caution_bias = _clamp(psyche_ctx.caution_bias * result.caution_scale)
        psyche_ctx.pressure = _clamp(psyche_ctx.pressure * result.pressure_scale)
        psyche_ctx.structuredness_bias = _clamp(
            psyche_ctx.structuredness_bias * result.structuredness_scale
        )
        psyche_ctx.verbosity_bias = _clamp(
            psyche_ctx.verbosity_bias * result.verbosity_scale
        )
        psyche_ctx.trust = _clamp(
            psyche_ctx.trust * result.trust_scale
            + psyche_ctx.relation_quality_ema * (1.0 - result.trust_scale) * 0.25
        )
        psyche_ctx.directness_bias = _clamp(
            psyche_ctx.directness_bias * result.directness_scale
        )
        psyche_ctx.consistency_decision = result.decision
        psyche_ctx.consistency_reasons = list(result.reasons)

    return memory_ctx, psyche_ctx, result


def build_psyche_v2_runtime_snapshot(user_id: str) -> dict[str, Any]:
    """
    Build runtime snapshot of Psyche V2 for chat/agent decision context.

    Returns lightweight summary optimized for runtime consumption.
    """
    try:
        snapshot = _canonical_v2().get_snapshot(user_id)
        policy = snapshot.derived_policy
        prof = snapshot.profile
        st = snapshot.state

        habit_biases = []
        for habit in snapshot.active_habits:
            if habit.intensity > 0.4:
                habit_biases.append(
                    {
                        "habit_name": habit.habit_name,
                        "habit_type": habit.habit_type,
                        "intensity": habit.intensity,
                        "reinforcement_count": habit.reinforcement_count,
                    }
                )

        pressure_display = (
            st.pressure_smoothed if st.pressure_smoothed > 0.0 else st.pressure
        )
        habit_stability = _habit_stability_from_habits(snapshot.active_habits)
        psyche_drift = _psyche_drift_score(prof, st)
        relation_drift = _relation_drift_score(prof)

        return {
            "loaded": True,
            "mode": st.current_mode,
            "relation_trust": float(policy.get("relation_trust", prof.relation_trust)),
            "relation_trust_raw": prof.relation_trust,
            "relation_interaction_quality_ema": prof.relation_interaction_quality_ema,
            "relation_familiarity": prof.relation_familiarity,
            "relation_friction": prof.relation_friction,
            "relation_warmth": prof.relation_warmth,
            "certainty": st.certainty,
            "energy": st.energy,
            "focus": st.focus,
            "pressure": pressure_display,
            "pressure_raw": st.pressure,
            "pending_mode": st.pending_mode,
            "mode_streak": st.mode_streak,
            "stress_load": prof.stress_load,
            "active_rules_count": len(snapshot.active_rules),
            "active_habits_count": len(snapshot.active_habits),
            "habit_biases": habit_biases,
            "habit_stability_score": habit_stability,
            "psyche_drift_score": psyche_drift,
            "relation_drift_score": relation_drift,
            "recent_events_count": len(snapshot.recent_events),
            "behavior_policy": snapshot.derived_policy,
            "confidence_baseline": prof.confidence_baseline,
            "core_caution": prof.core_caution,
        }
    except Exception as e:
        logger.warning("Failed to build psyche v2 snapshot: %s", e)
        return {
            "loaded": False,
            "error": str(e),
        }


def summarize_psyche_v2_for_chat(user_id: str) -> str:
    """Generate compact text summary of Psyche V2 for chat context injection."""
    try:
        snapshot = build_psyche_v2_runtime_snapshot(user_id)

        if not snapshot.get("loaded"):
            return ""

        mode = snapshot.get("mode", "neutral")
        trust = snapshot.get("relation_trust", 0.5)
        certainty = snapshot.get("certainty", 0.5)

        trust_label = (
            "high trust"
            if trust > 0.7
            else "building trust" if trust > 0.4 else "low trust"
        )
        certainty_label = (
            "confident"
            if certainty > 0.7
            else "uncertain" if certainty < 0.4 else "moderate"
        )

        return f"Mode: {mode} | {trust_label} | {certainty_label}"
    except Exception as e:
        logger.warning("Failed to summarize psyche v2 for chat: %s", e)
        return ""


def summarize_psyche_v2_for_agent(user_id: str) -> dict[str, Any]:
    """Structured psyche insights for strategy selection."""
    try:
        snapshot = build_psyche_v2_runtime_snapshot(user_id)

        if not snapshot.get("loaded"):
            return {"available": False}

        return {
            "available": True,
            "mode": snapshot.get("mode", "neutral"),
            "relation_trust": snapshot.get("relation_trust", 0.5),
            "relation_friction": snapshot.get("relation_friction", 0.0),
            "certainty": snapshot.get("certainty", 0.5),
            "energy": snapshot.get("energy", 0.5),
            "pressure": snapshot.get("pressure", 0.0),
            "stress_load": snapshot.get("stress_load", 0.0),
            "active_rules_count": snapshot.get("active_rules_count", 0),
            "active_habits_count": snapshot.get("active_habits_count", 0),
            "habit_biases": snapshot.get("habit_biases", []),
            "habit_stability_score": snapshot.get("habit_stability_score", 0.5),
            "psyche_drift_score": snapshot.get("psyche_drift_score", 0.0),
            "relation_drift_score": snapshot.get("relation_drift_score", 0.0),
        }
    except Exception as e:
        logger.warning("Failed to summarize psyche v2 for agent: %s", e)
        return {"available": False, "error": str(e)}


def build_psyche_v2_behavior_context(user_id: str) -> PsycheV2BehaviorContext:
    """
    Build enriched Psyche V2 behavior context for runtime injection.
    """
    try:
        snapshot = build_psyche_v2_runtime_snapshot(user_id)

        if not snapshot.get("loaded"):
            return PsycheV2BehaviorContext(
                loaded=False,
                mode="neutral",
                pressure=0.0,
                trust=0.5,
                friction=0.0,
                warmth=0.5,
                directness_bias=0.5,
                reassurance_bias=0.5,
                autonomy_bias=0.5,
                structuredness_bias=0.5,
                tool_bias=0.5,
                web_bias=0.5,
                caution_bias=0.5,
                verbosity_bias=0.5,
            )

        behavior_policy = snapshot.get("behavior_policy", {})
        conf_style = float(behavior_policy.get("confidence_style", 0.5))

        return PsycheV2BehaviorContext(
            loaded=True,
            mode=snapshot.get("mode", "neutral"),
            pressure=float(snapshot.get("pressure", 0.0)),
            trust=float(
                snapshot.get("relation_trust_raw", snapshot.get("relation_trust", 0.5))
            ),
            friction=float(snapshot.get("relation_friction", 0.0)),
            warmth=float(snapshot.get("relation_warmth", 0.5)),
            directness_bias=float(behavior_policy.get("directness", 0.5)),
            reassurance_bias=float(behavior_policy.get("reassurance_bias", 0.5)),
            autonomy_bias=float(behavior_policy.get("autonomy_bias", 0.5)),
            structuredness_bias=float(behavior_policy.get("structuredness_bias", 0.5)),
            tool_bias=float(behavior_policy.get("tool_bias", 0.5)),
            web_bias=float(behavior_policy.get("web_bias", 0.5)),
            caution_bias=float(behavior_policy.get("caution", 0.5)),
            verbosity_bias=float(behavior_policy.get("verbosity", 0.5)),
            relation_quality_ema=float(
                snapshot.get("relation_interaction_quality_ema", 0.5)
            ),
            habit_stability_score=float(snapshot.get("habit_stability_score", 0.5)),
            psyche_drift_score=float(snapshot.get("psyche_drift_score", 0.0)),
            relation_drift_score=float(snapshot.get("relation_drift_score", 0.0)),
            confidence_style_effective=conf_style,
        )
    except Exception as e:
        logger.warning("Failed to build psyche v2 behavior context: %s", e)
        return PsycheV2BehaviorContext(
            loaded=False,
            mode="neutral",
            pressure=0.0,
            trust=0.5,
            friction=0.0,
            warmth=0.5,
            directness_bias=0.5,
            reassurance_bias=0.5,
            autonomy_bias=0.5,
            structuredness_bias=0.5,
            tool_bias=0.5,
            web_bias=0.5,
            caution_bias=0.5,
            verbosity_bias=0.5,
        )
