#!/usr/bin/env python3
"""
Psyche V2 policy derivation.

Derives actionable behavior policy from psyche profile and state.
"""

import logging
import time
from typing import Any

from aihub.psyche_v2_models import PsycheV2Profile, PsycheV2State
from aihub.memory_psyche_contracts import PsycheV2PolicyView
from aihub.psyche_v2_traits import recommend_verbosity, recommend_tool_bias

logger = logging.getLogger(__name__)


def derive_behavior_policy(
    user_id: str, profile: PsycheV2Profile, state: PsycheV2State
) -> PsycheV2PolicyView:
    """
    Derive complete behavior policy from profile and state.

    Returns policy view for runtime consumption.
    """
    state_certainty_effective = (0.52 * state.certainty) + (0.48 * profile.confidence_baseline)
    state_certainty_effective = max(0.0, min(1.0, state_certainty_effective))

    directness = (profile.core_directness * 0.62) + (state.social_openness * 0.38)

    verbosity = recommend_verbosity(profile, state)

    caution = (profile.core_caution * 0.64) + ((1.0 - state_certainty_effective) * 0.36)

    initiative = (profile.core_initiative * 0.6) + (state.task_aggression * 0.4)

    tool_bias = recommend_tool_bias(profile, state)

    # Web bias: incorporate state.web_bias (direct user bias) with curiosity/energy
    web_bias = (state.web_bias * 0.5) + (profile.core_curiosity * 0.3) + (state.energy * 0.2)

    confidence_style = (profile.confidence_baseline * 0.72) + (state_certainty_effective * 0.28)

    response_compression = 1.0 - verbosity

    pressure_effective = (
        state.pressure_smoothed
        if getattr(state, "pressure_smoothed", 0.0) > 0.0
        else state.pressure
    )

    escalation_bias = (profile.core_assertiveness * 0.6) + (pressure_effective * 0.4)

    reassurance_bias = (profile.core_warmth * 0.5) + (profile.relation_warmth * 0.3) + ((1.0 - state.certainty) * 0.2)

    autonomy_bias = (profile.core_initiative * 0.6) + (profile.relation_collaboration_confidence * 0.4)

    structuredness_bias = (profile.core_formality * 0.5) + (caution * 0.3) + ((1.0 - pressure_effective) * 0.2)

    warmth_adj = max(
        0.0,
        min(
            1.0,
            0.55 * profile.relation_warmth
            + 0.45 * (0.35 + 0.65 * profile.relation_interaction_quality_ema),
        ),
    )
    friction_adj = max(
        0.0,
        min(
            1.0,
            profile.relation_friction
            * (1.0 - 0.4 * profile.relation_interaction_quality_ema),
        ),
    )

    return PsycheV2PolicyView(
        user_id=user_id,
        directness=max(0.0, min(1.0, directness)),
        verbosity=max(0.0, min(1.0, verbosity)),
        caution=max(0.0, min(1.0, caution)),
        initiative=max(0.0, min(1.0, initiative)),
        tool_bias=max(0.0, min(1.0, tool_bias)),
        web_bias=max(0.0, min(1.0, web_bias)),
        confidence_style=max(0.0, min(1.0, confidence_style)),
        response_compression=max(0.0, min(1.0, response_compression)),
        escalation_bias=max(0.0, min(1.0, escalation_bias)),
        reassurance_bias=max(0.0, min(1.0, reassurance_bias)),
        autonomy_bias=max(0.0, min(1.0, autonomy_bias)),
        structuredness_bias=max(0.0, min(1.0, structuredness_bias)),
        relation_trust=profile.relation_trust,
        relation_familiarity=profile.relation_familiarity,
        relation_friction=friction_adj,
        relation_warmth=warmth_adj,
        current_mode=state.current_mode,
        stress_load=profile.stress_load,
        derived_at=time.time(),
    )


def policy_to_dict(policy: PsycheV2PolicyView) -> dict[str, Any]:
    """Convert policy view to dictionary for API serialization."""
    return {
        "user_id": policy.user_id,
        "directness": policy.directness,
        "verbosity": policy.verbosity,
        "caution": policy.caution,
        "initiative": policy.initiative,
        "tool_bias": policy.tool_bias,
        "web_bias": policy.web_bias,
        "confidence_style": policy.confidence_style,
        "response_compression": policy.response_compression,
        "escalation_bias": policy.escalation_bias,
        "reassurance_bias": policy.reassurance_bias,
        "autonomy_bias": policy.autonomy_bias,
        "structuredness_bias": policy.structuredness_bias,
        "relation_trust": policy.relation_trust,
        "relation_familiarity": policy.relation_familiarity,
        "relation_friction": policy.relation_friction,
        "relation_warmth": policy.relation_warmth,
        "current_mode": policy.current_mode,
        "stress_load": policy.stress_load,
        "derived_at": policy.derived_at,
    }
