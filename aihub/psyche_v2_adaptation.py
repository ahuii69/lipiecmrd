#!/usr/bin/env python3
"""
Psyche V2 adaptation engine.

Handles psyche evolution based on events and interaction patterns.
"""

import logging
import time
from typing import Any

from aihub.psyche_v2_models import PsycheV2Profile, PsycheV2State, PsycheV2Event
from aihub.psyche_v2_repository import update_psyche_profile, update_psyche_state

logger = logging.getLogger(__name__)

MODE_SWITCH_STREAK_REQUIRED = 3


def smooth_scalar_field(prev: float, new: float, alpha: float = 0.38) -> float:
    """EMA-style blend for state scalars so single-turn spikes do not dominate."""
    prev = max(0.0, min(1.0, prev))
    new = max(0.0, min(1.0, new))
    return max(0.0, min(1.0, alpha * new + (1.0 - alpha) * prev))


def apply_event_to_state(
    state: PsycheV2State, event: PsycheV2Event, signal_strength: float = 0.5
) -> PsycheV2State:
    """
    Apply psyche event to current state.

    Returns updated state (not yet persisted).
    """
    delta = event.delta

    updates = {}
    for key, value in delta.items():
        if hasattr(state, key):
            current = getattr(state, key)
            if isinstance(current, float):
                adjusted_delta = value * signal_strength
                new_value = max(0.0, min(1.0, current + adjusted_delta))
                updates[key] = new_value

    updates["updated_ts"] = time.time()

    return state.model_copy(update=updates)


def adapt_profile_from_events(
    profile: PsycheV2Profile, recent_events: list[PsycheV2Event]
) -> PsycheV2Profile:
    """
    Gradually adapt profile traits based on accumulated events.

    Long-term traits change slowly (adaptation_velocity controls rate).
    """
    if not recent_events:
        return profile

    # Accumulate deltas
    accumulated: dict[str, float] = {}
    for event in recent_events:
        for key, value in event.delta.items():
            accumulated[key] = accumulated.get(key, 0.0) + value

    # Apply to profile with velocity damping
    velocity = profile.adaptation_velocity
    for key, total_delta in accumulated.items():
        trait_key = f"core_{key}" if not key.startswith("core_") and not key.startswith("relation_") else key

        if hasattr(profile, trait_key):
            current = getattr(profile, trait_key)
            if isinstance(current, float):
                adjusted_delta = (total_delta / len(recent_events)) * velocity
                new_value = max(0.0, min(1.0, current + adjusted_delta))
                setattr(profile, trait_key, new_value)

    profile.updated_ts = time.time()
    return profile


def smooth_pressure_value(previous_smoothed: float, raw_pressure: float, alpha: float = 0.32) -> float:
    """
    EMA on pressure so single-turn spikes do not dominate long-horizon behavior.

    previous_smoothed: last smoothed value; if zero and raw is zero, return zero.
    """
    raw_pressure = max(0.0, min(1.0, raw_pressure))
    if previous_smoothed <= 0.0 and raw_pressure <= 0.0:
        return 0.0
    if previous_smoothed <= 0.0:
        return raw_pressure
    return max(0.0, min(1.0, alpha * raw_pressure + (1.0 - alpha) * previous_smoothed))


def apply_mode_hysteresis(
    state: PsycheV2State, candidate_mode: str | None
) -> PsycheV2State:
    """
    Require two consecutive agreeing signals before changing current_mode.

    Prevents oscillation between focused/cautious/exploratory on single volatile turns.
    """
    if candidate_mode is None:
        return state.model_copy(update={"pending_mode": "", "mode_streak": 0})

    if candidate_mode == state.current_mode:
        return state.model_copy(update={"pending_mode": "", "mode_streak": 0})

    if candidate_mode == state.pending_mode:
        streak = state.mode_streak + 1
    else:
        streak = 1

    if streak >= MODE_SWITCH_STREAK_REQUIRED:
        return state.model_copy(
            update={
                "current_mode": candidate_mode,  # type: ignore[arg-type]
                "pending_mode": "",
                "mode_streak": 0,
            }
        )
    return state.model_copy(update={"pending_mode": candidate_mode, "mode_streak": streak})


def detect_mode_transition(state: PsycheV2State) -> str | None:
    """
    Detect if state warrants mode transition.

    Thresholds are intentionally strict so one weak turn rarely flips mode;
    `apply_mode_hysteresis` still requires repeated agreeing ticks.
    """
    if state.pressure > 0.78 and state.certainty < 0.36:
        return "cautious"

    if state.focus > 0.76 and state.energy > 0.64:
        return "focused"

    if state.mood > 0.66 and state.pressure < 0.28:
        return "exploratory"

    if state.task_aggression > 0.74 and state.energy > 0.55:
        return "assertive"

    if state.social_openness > 0.64 and state.stability > 0.62:
        return "collaborative"

    if state.certainty < 0.36 and state.focus > 0.55:
        return "analytical"

    if (
        0.42 < state.mood < 0.58
        and 0.42 < state.energy < 0.58
        and state.pressure < 0.38
    ):
        return "neutral"

    return None
