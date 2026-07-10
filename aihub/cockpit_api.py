#!/usr/bin/env python3
"""Router API dla cockpit (health + ETAP9B/9C diagnostics)."""

from typing import Any

from fastapi import APIRouter

from aihub.consistency_engine import get_consistency_checks, get_consistency_stats
from aihub.policy_engine import get_policy_profile_data
from aihub.psyche_core import get_psyche_core
from aihub.reflection_engine import get_reflections
from aihub.simulation_engine import get_simulations

try:
    from aihub.chat_runtime import _TRACE_CACHE
except ImportError:
    _TRACE_CACHE = {}

router = APIRouter(prefix="/cockpit", tags=["cockpit"])


@router.get("/health")
def cockpit_health() -> dict[str, Any]:
    return {"ok": True, "service": "cockpit"}


@router.get("/schema-health")
def cockpit_schema_health() -> dict[str, Any]:
    """Active stack (Memory V2 + Psyche V2) SQLite schema inspection for operators."""
    from aihub.db import get_active_stack_schema_health

    return get_active_stack_schema_health()


@router.get("/consistency/{user_id}")
def cockpit_consistency(user_id: str, limit: int = 20) -> dict[str, Any]:
    """Consistency checks + stats for cockpit panel."""
    return {
        "user_id": user_id,
        "checks": get_consistency_checks(user_id, limit=limit),
        "stats": get_consistency_stats(user_id),
    }


@router.get("/reflections/{user_id}")
def cockpit_reflections(user_id: str, limit: int = 20) -> dict[str, Any]:
    """Recent post-action reflections for cockpit panel."""
    return {
        "user_id": user_id,
        "reflections": get_reflections(user_id, limit=limit),
    }


@router.get("/policy/{user_id}")
def cockpit_policy(user_id: str) -> dict[str, Any]:
    """Current policy profile derived from reflections."""
    return get_policy_profile_data(user_id)


@router.get("/simulations/{user_id}")
def cockpit_simulations(user_id: str, limit: int = 20) -> dict[str, Any]:
    """Recent simulation results and best actions."""
    return {
        "user_id": user_id,
        "simulations": get_simulations(user_id, limit=limit),
    }


@router.get("/overview/{user_id}")
def cockpit_overview(user_id: str, limit: int = 20) -> dict[str, Any]:
    """Unified ETAP9B/9C diagnostic overview for cockpit."""
    checks = get_consistency_checks(user_id, limit=limit)
    reflections = get_reflections(user_id, limit=limit)
    policy = get_policy_profile_data(user_id)
    simulations = get_simulations(user_id, limit=limit)

    return {
        "user_id": user_id,
        "consistency": {
            "stats": get_consistency_stats(user_id),
            "recent": checks,
        },
        "reflections": {
            "count": len(reflections),
            "recent": reflections,
        },
        "policy": policy,
        "simulations": {
            "count": len(simulations),
            "recent": simulations,
            "best_action": simulations[0]["best_action"] if simulations else "",
        },
    }


def cockpit_blocker_status(user_id: str) -> dict[str, Any]:
    """Return most recent blocker verdict status for user.

    Returns safe defaults when no traces exist, or structured blocker
    verdict data from the most recent trace that contains a blocker.
    """
    traces = _TRACE_CACHE.get(user_id, [])
    traces_available = len(traces)

    if not traces_available:
        return {
            "blocker_active": False,
            "hard": False,
            "blocker_type": "none",
            "resolution": "allow",
            "blocker_verdict": None,
            "traces_available": 0,
        }

    most_recent_blocker = None
    for trace in reversed(list(traces)):
        if trace.get("blocker_verdict"):
            most_recent_blocker = trace.get("blocker_verdict")
            break

    if not most_recent_blocker:
        return {
            "blocker_active": False,
            "hard": False,
            "blocker_type": "none",
            "resolution": "allow",
            "blocker_verdict": None,
            "traces_available": traces_available,
        }

    dev_view = {
        "blocker_active": most_recent_blocker.get("blocker_active", False),
        "resolution": most_recent_blocker.get("resolution", "allow"),
        "source": most_recent_blocker.get("source", "unknown"),
        "confidence": most_recent_blocker.get("confidence", 0.0),
        "contributing_signals": most_recent_blocker.get("contributing_signals", []),
        "dev_message": most_recent_blocker.get("dev_message", ""),
        "feedback_applied": most_recent_blocker.get("feedback_applied", False),
        "escalated_from_history": most_recent_blocker.get(
            "escalated_from_history", False
        ),
        "feedback_detail": most_recent_blocker.get("feedback_detail", ""),
    }

    user_view = {
        "blocker_active": most_recent_blocker.get("blocker_active", False),
        "user_message": most_recent_blocker.get("user_message", ""),
        "severity": "hard" if most_recent_blocker.get("hard", False) else "caution",
    }

    return {
        "blocker_active": most_recent_blocker.get("blocker_active", False),
        "hard": most_recent_blocker.get("hard", False),
        "blocker_type": most_recent_blocker.get("blocker_type", "none"),
        "resolution": most_recent_blocker.get("resolution", "allow"),
        "blocker_verdict": most_recent_blocker,
        "traces_available": traces_available,
        "dev": dev_view,
        "user": user_view,
    }


@router.get("/memory-v2/{user_id}")
def cockpit_memory_v2(user_id: str) -> dict[str, Any]:
    """Get memory V2 summary for cockpit (via :func:`aihub.memory_core.get_memory_core`)."""
    from aihub.memory_core import get_memory_core

    return get_memory_core().build_cockpit_memory_v2_panel(user_id)


@router.get("/psyche-v2/{user_id}")
def cockpit_psyche_v2(user_id: str) -> dict[str, Any]:
    """Get psyche V2 snapshot for cockpit."""
    service = get_psyche_core().v2_service
    snapshot = service.get_snapshot(user_id)

    return {
        "user_id": snapshot.user_id,
        "profile": {
            "core_directness": snapshot.profile.core_directness,
            "core_patience": snapshot.profile.core_patience,
            "core_curiosity": snapshot.profile.core_curiosity,
            "core_caution": snapshot.profile.core_caution,
            "relation_trust": snapshot.profile.relation_trust,
            "relation_interaction_quality_ema": snapshot.profile.relation_interaction_quality_ema,
            "relation_familiarity": snapshot.profile.relation_familiarity,
            "relation_sync": snapshot.profile.relation_sync,
            "stress_load": snapshot.profile.stress_load,
        },
        "state": {
            "mood": snapshot.state.mood,
            "energy": snapshot.state.energy,
            "focus": snapshot.state.focus,
            "pressure": snapshot.state.pressure,
            "pressure_smoothed": snapshot.state.pressure_smoothed,
            "certainty": snapshot.state.certainty,
            "current_mode": snapshot.state.current_mode,
            "pending_mode": snapshot.state.pending_mode,
            "mode_streak": snapshot.state.mode_streak,
        },
        "active_rules_count": len(snapshot.active_rules),
        "recent_events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "reason_text": event.reason_text,
                "source_ref": event.source_ref,
                "created_ts": event.created_ts,
            }
            for event in snapshot.recent_events[:5]
        ],
        "recent_events_count": len(snapshot.recent_events),
        "derived_policy": snapshot.derived_policy,
    }


@router.get("/identity/{user_id}")
def cockpit_identity(user_id: str) -> dict[str, Any]:
    """Get unified identity view for cockpit."""
    from aihub.runtime_identity_bridge import build_identity_bridge_snapshot

    snapshot = build_identity_bridge_snapshot(user_id, query_text="")

    return {
        "user_id": snapshot.user_id,
        "top_preferences": snapshot.top_preferences[:5],
        "top_procedures": snapshot.top_procedures[:3],
        "active_contradictions_count": snapshot.active_contradictions_count,
        "relation_trust": snapshot.relation_trust,
        "relation_familiarity": snapshot.relation_familiarity,
        "behavior_mode": snapshot.behavior_mode,
        "stress_load": snapshot.stress_load,
        "autobio_summary": snapshot.autobio_summary,
        "memory_v2_total": snapshot.memory_v2_total,
        "psyche_v2_certainty": snapshot.psyche_v2_certainty,
        "snapshot_ts": snapshot.snapshot_ts,
    }


@router.get("/memory-v2/retrieval/{user_id}")
async def cockpit_memory_v2_retrieval(user_id: str, query: str = "") -> dict[str, Any]:
    """Get memory retrieval explanation for cockpit."""
    from aihub.memory_core import get_memory_core

    return get_memory_core().build_cockpit_retrieval_payload(user_id, query, top_n=10)


@router.get("/psyche-v2/habits/{user_id}")
async def cockpit_psyche_v2_habits(user_id: str) -> dict[str, Any]:
    """Get psyche habits for cockpit."""
    svc = get_psyche_core().v2_service
    habits = svc.get_habits(user_id, min_intensity=0.2)

    return {
        "user_id": user_id,
        "habits": [
            {
                "habit_name": h.habit_name,
                "habit_type": h.habit_type,
                "intensity": h.intensity,
                "reinforcement_count": h.reinforcement_count,
                "last_reinforced_ts": h.last_reinforced_ts,
            }
            for h in habits
        ],
        "total_count": len(habits),
    }


@router.get("/psyche-v2/relations/{user_id}")
async def cockpit_psyche_v2_relations(user_id: str) -> dict[str, Any]:
    """Get psyche relations for cockpit."""
    svc = get_psyche_core().v2_service
    relations = svc.get_relations_summary(user_id)

    return relations


@router.get("/calibration/{user_id}")
async def cockpit_calibration_debug(user_id: str, query: str = "") -> dict[str, Any]:
    """
    Get calibration debug info showing active thresholds, behavior rules,
    and which memory/psyche signals influenced runtime.

    For operational debugging and quality validation.
    """
    from aihub.runtime_memory_bridge import build_memory_v2_runtime_context
    from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context

    memory_ctx = build_memory_v2_runtime_context(user_id, query)
    psyche_ctx = build_psyche_v2_behavior_context(user_id)

    from aihub.psyche_v2_repository import ensure_psyche_profile
    from aihub.runtime_psyche_bridge import apply_consistency_to_contexts

    _prof = ensure_psyche_profile(user_id)
    memory_ctx, psyche_ctx, consistency_result = apply_consistency_to_contexts(
        memory_ctx, psyche_ctx, _prof.core_caution
    )

    psyche_svc = get_psyche_core().v2_service

    # Active thresholds (from code)
    active_thresholds = {
        "contradiction_guard_caution_min": 0.5,
        "procedure_confidence_boost_min": 0.6,
        "pressure_structuredness_min": 0.5,
        "friction_precision_min": 0.5,
        "warmth_trust_flexibility_min": 0.7,
        "autonomy_action_min": 0.7,
    }

    # Which rules would apply?
    applied_rules = []

    if memory_ctx.contradiction_alerts and psyche_ctx.caution_bias > 0.5:
        applied_rules.append(
            {
                "rule": "contradiction_guard",
                "trigger": f"contradictions={len(memory_ctx.contradiction_alerts)}, caution={psyche_ctx.caution_bias:.2f}",
                "impact": "Add uncertainty markers to response",
            }
        )

    if memory_ctx.confidence_modifier > 0.6 and memory_ctx.top_procedures:
        applied_rules.append(
            {
                "rule": "procedure_confidence_boost",
                "trigger": f"avg_confidence={memory_ctx.confidence_modifier:.2f}, procedures={len(memory_ctx.top_procedures)}",
                "impact": "Increase execution confidence, show procedures in context",
            }
        )

    if psyche_ctx.pressure > 0.6:
        applied_rules.append(
            {
                "rule": "pressure_structuredness",
                "trigger": f"pressure={psyche_ctx.pressure:.2f}",
                "impact": "Emphasize structured, concise responses",
            }
        )

    if psyche_ctx.friction > 0.5:
        applied_rules.append(
            {
                "rule": "friction_precision",
                "trigger": f"friction={psyche_ctx.friction:.2f}",
                "impact": "Increase precision, reduce loose interpretations",
            }
        )

    if psyche_ctx.warmth > 0.7 and psyche_ctx.trust > 0.6:
        applied_rules.append(
            {
                "rule": "warmth_trust_flexibility",
                "trigger": f"warmth={psyche_ctx.warmth:.2f}, trust={psyche_ctx.trust:.2f}",
                "impact": "More natural, flexible tone",
            }
        )

    if psyche_ctx.autonomy_bias > 0.7:
        applied_rules.append(
            {
                "rule": "high_autonomy",
                "trigger": f"autonomy={psyche_ctx.autonomy_bias:.2f}",
                "impact": "Act autonomously, less asking",
            }
        )

    # Memory items promoted to prompt context
    promoted_memory_items = []
    if memory_ctx.loaded:
        promoted_memory_items = [
            {
                "type": "fact",
                "items": memory_ctx.top_facts[:3],
            },
            {
                "type": "preference",
                "items": memory_ctx.top_preferences[:3],
            },
            {
                "type": "procedure",
                "items": memory_ctx.top_procedures[:2],
            },
        ]

    # Psyche biases affecting decision
    psyche_biases = {}
    if psyche_ctx.loaded:
        psyche_biases = {
            "mode": psyche_ctx.mode,
            "directness": psyche_ctx.directness_bias,
            "verbosity": psyche_ctx.verbosity_bias,
            "caution": psyche_ctx.caution_bias,
            "tool_bias": psyche_ctx.tool_bias,
            "web_bias": psyche_ctx.web_bias,
            "autonomy": psyche_ctx.autonomy_bias,
            "structuredness": psyche_ctx.structuredness_bias,
            "pressure": psyche_ctx.pressure,
            "friction": psyche_ctx.friction,
            "trust": psyche_ctx.trust,
            "warmth": psyche_ctx.warmth,
        }

    long_horizon = {}
    if memory_ctx.loaded:
        long_horizon["stability_tier_counts"] = dict(memory_ctx.stability_tier_counts)
        long_horizon["actionable_contradictions"] = list(
            memory_ctx.contradiction_alerts
        )
        long_horizon["transient_contradiction_hints"] = list(
            memory_ctx.transient_contradiction_hints
        )
        long_horizon["actionable_contradictions_count"] = int(
            memory_ctx.actionable_contradictions_count
        )
        long_horizon["transient_contradiction_count"] = int(
            memory_ctx.transient_contradiction_count
        )
        long_horizon["stable_memory_operational"] = list(
            memory_ctx.stable_memory_operational
        )
        long_horizon["transient_memory_operational"] = list(
            memory_ctx.transient_memory_operational
        )
        long_horizon["suppressed_memory_operational"] = list(
            memory_ctx.suppressed_memory_operational
        )
        long_horizon["promotion_audit"] = list(memory_ctx.promotion_audit)
        long_horizon["procedure_confidence_raw"] = memory_ctx.confidence_modifier_raw
        long_horizon["procedure_confidence_effective"] = memory_ctx.confidence_modifier
        long_horizon["self_consistency_notes_memory"] = list(
            memory_ctx.self_consistency_notes
        )
        long_horizon["memory_consistency_decision"] = memory_ctx.consistency_decision
    snap = psyche_svc.get_snapshot(user_id)
    long_horizon["psyche"] = {
        "current_mode": snap.state.current_mode,
        "pending_mode": snap.state.pending_mode,
        "mode_streak": snap.state.mode_streak,
        "mode_switch_streak_required": 3,
        "pressure_raw": snap.state.pressure,
        "pressure_smoothed": snap.state.pressure_smoothed,
        "relation_interaction_quality_ema": snap.profile.relation_interaction_quality_ema,
        "relation_trust_raw": snap.profile.relation_trust,
        "relation_trust_policy": snap.derived_policy.get(
            "relation_trust", snap.profile.relation_trust
        ),
        "relation_drift_score": abs(
            float(snap.profile.relation_trust)
            - float(snap.profile.relation_interaction_quality_ema)
        ),
        "psyche_drift_score": float(
            abs(float(snap.state.certainty) - float(snap.profile.confidence_baseline))
            * 0.45
            + abs(
                float(
                    snap.state.pressure_smoothed
                    if snap.state.pressure_smoothed > 0.0
                    else snap.state.pressure
                )
                - float(snap.profile.stress_load)
            )
            * 0.38
        ),
    }
    habits = psyche_svc.get_habits(user_id, min_intensity=0.15)
    long_horizon["habits"] = [
        {
            "habit_name": h.habit_name,
            "intensity": h.intensity,
            "reinforcement_count": h.reinforcement_count,
        }
        for h in habits[:12]
    ]
    long_horizon["self_consistency"] = {
        "decision": consistency_result.decision,
        "reasons": list(consistency_result.reasons),
        "caution_scale": consistency_result.caution_scale,
        "procedure_conf_scale": consistency_result.procedure_conf_scale,
        "pressure_scale": consistency_result.pressure_scale,
        "structuredness_scale": consistency_result.structuredness_scale,
    }
    if psyche_ctx.loaded:
        long_horizon["psyche_runtime_after_consistency"] = {
            "consistency_decision": psyche_ctx.consistency_decision,
            "consistency_reasons": list(psyche_ctx.consistency_reasons),
            "pressure": psyche_ctx.pressure,
            "caution": psyche_ctx.caution_bias,
            "relation_quality_ema": psyche_ctx.relation_quality_ema,
        }

    return {
        "user_id": user_id,
        "query": query,
        "active_thresholds": active_thresholds,
        "applied_behavior_rules": applied_rules,
        "promoted_memory_items": promoted_memory_items,
        "psyche_biases": psyche_biases,
        "memory_context_loaded": memory_ctx.loaded,
        "psyche_context_loaded": psyche_ctx.loaded,
        "contradiction_count": (
            len(memory_ctx.contradiction_alerts) if memory_ctx.loaded else 0
        ),
        "procedure_confidence": (
            memory_ctx.confidence_modifier if memory_ctx.loaded else 0.0
        ),
        "long_horizon_stability": long_horizon,
    }
