#!/usr/bin/env python3
"""
Psyche V2 traits analysis and scoring.

Provides trait-based behavior predictions and profile analysis.
"""

import logging
from typing import Any

from aihub.psyche_v2_models import PsycheV2Profile, PsycheV2State

logger = logging.getLogger(__name__)


def analyze_trait_alignment(profile: PsycheV2Profile, state: PsycheV2State) -> dict[str, float]:
    """
    Analyze alignment between long-term traits and current state.

    Returns alignment scores for key dimensions.
    """
    return {
        "directness_alignment": abs(profile.core_directness - state.social_openness),
        "energy_stability": abs(state.energy - profile.confidence_baseline),
        "focus_pressure": state.focus * (1.0 - state.pressure),
        "overall_coherence": _calculate_coherence(profile, state),
    }


def _calculate_coherence(profile: PsycheV2Profile, state: PsycheV2State) -> float:
    """Calculate overall coherence between profile and state."""
    trait_state_pairs = [
        (profile.core_directness, state.social_openness),
        (profile.core_assertiveness, state.task_aggression),
        (profile.core_initiative, state.energy),
        (profile.core_caution, 1.0 - state.certainty),
    ]

    distances = [abs(trait - state_val) for trait, state_val in trait_state_pairs]
    avg_distance = sum(distances) / len(distances)

    return max(0.0, 1.0 - avg_distance)


def recommend_verbosity(profile: PsycheV2Profile, state: PsycheV2State) -> float:
    """
    Recommend verbosity level based on traits and state.

    Returns value in [0.0, 1.0] where 1.0 = very verbose.
    """
    # Incorporate direct state.verbosity_bias with trait-based derivation
    base_verbosity = (
        state.verbosity_bias * 0.7  # Increased from 0.6 for even stronger direct influence
        + profile.core_warmth * 0.1
        + profile.core_patience * 0.08
        + state.social_openness * 0.08
        + (1.0 - profile.core_directness) * 0.04
    )

    # Adjust for pressure and focus
    if state.pressure > 0.7:
        base_verbosity *= 0.7
    if state.focus < 0.3:
        base_verbosity *= 0.8

    return max(0.0, min(1.0, base_verbosity))


def recommend_tool_bias(profile: PsycheV2Profile, state: PsycheV2State) -> float:
    """
    Recommend tool usage bias based on traits and state.

    Returns value in [0.0, 1.0] where 1.0 = high tool preference.
    """
    # Incorporate direct state.tool_bias with trait-based derivation
    base_bias = (
        state.tool_bias * 0.4  # Direct user tool bias
        + profile.core_initiative * 0.2
        + profile.core_assertiveness * 0.15
        + state.task_aggression * 0.15
        + state.energy * 0.1
    )

    # State modulation
    if state.pressure > 0.6:
        base_bias *= 1.2
    if state.certainty < 0.4:
        base_bias *= 0.8

    return max(0.0, min(1.0, base_bias))
