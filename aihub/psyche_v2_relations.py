#!/usr/bin/env python3
"""
Psyche V2 relational stance management.

Tracks trust, familiarity, and sync with user over time.
"""

import logging
from typing import Any

from aihub.psyche_v2_models import PsycheV2Profile, PsycheV2Event

logger = logging.getLogger(__name__)


def update_relation_from_feedback(
    profile: PsycheV2Profile, feedback_type: str, magnitude: float = 0.05
) -> PsycheV2Profile:
    """
    Update relational stance based on feedback.

    feedback_type: "positive", "negative", "neutral"
    magnitude: adjustment strength
    """
    if feedback_type == "positive":
        profile.relation_trust = min(1.0, profile.relation_trust + magnitude)
        profile.relation_sync = min(1.0, profile.relation_sync + magnitude * 0.5)
    elif feedback_type == "negative":
        profile.relation_trust = max(0.0, profile.relation_trust - magnitude)
        profile.relation_sync = max(0.0, profile.relation_sync - magnitude * 0.5)

    # Familiarity always increases with interactions
    profile.relation_familiarity = min(1.0, profile.relation_familiarity + 0.01)

    return profile


def adjust_trust_from_success_rate(
    profile: PsycheV2Profile, success_rate: float, sample_size: int
) -> PsycheV2Profile:
    """
    Adjust trust based on observed success rate.

    Higher success rate over larger sample increases trust.
    """
    if sample_size < 5:
        return profile

    trust_delta = (success_rate - 0.5) * 0.1 * min(1.0, sample_size / 20)
    profile.relation_trust = max(0.0, min(1.0, profile.relation_trust + trust_delta))

    return profile


RELATION_DELTA_CAP = 0.012
RELATION_QUALITY_EMA_ALPHA = 0.12


def _outcome_to_quality_signal(outcome_kind: str) -> float:
    if outcome_kind == "success":
        return 0.78
    if outcome_kind == "progress":
        return 0.72
    if outcome_kind == "failure":
        return 0.42
    if outcome_kind == "timeout":
        return 0.38
    if outcome_kind == "degraded":
        return 0.45
    return 0.5


def apply_relation_outcome_long_horizon(
    profile: PsycheV2Profile,
    outcome_kind: str,
    *,
    contradictions_present: int = 0,
) -> PsycheV2Profile:
    """
    Capped per-turn trust/friction deltas plus EMA of perceived interaction quality.

    Single failures cannot swing trust or friction as much as repeated patterns.
    """
    signal = _outcome_to_quality_signal(outcome_kind)
    ema = profile.relation_interaction_quality_ema
    profile.relation_interaction_quality_ema = max(
        0.0, min(1.0, (1.0 - RELATION_QUALITY_EMA_ALPHA) * ema + RELATION_QUALITY_EMA_ALPHA * signal)
    )

    cap = RELATION_DELTA_CAP

    if outcome_kind == "success":
        profile.relation_trust = min(1.0, profile.relation_trust + min(cap, 0.018))
        profile.relation_friction = max(0.0, profile.relation_friction - min(cap, 0.022))
        profile.relation_warmth = min(1.0, profile.relation_warmth + min(cap, 0.010))
        profile.relation_collaboration_confidence = min(
            1.0, profile.relation_collaboration_confidence + min(cap, 0.015)
        )
    elif outcome_kind == "failure":
        profile.relation_trust = max(0.0, profile.relation_trust - min(cap, 0.008))
        profile.relation_friction = min(1.0, profile.relation_friction + min(cap, 0.020))
    elif outcome_kind == "progress":
        profile.relation_trust = min(1.0, profile.relation_trust + min(cap, 0.022))
        profile.relation_sync = min(1.0, profile.relation_sync + min(cap, 0.018))
    elif outcome_kind == "timeout":
        profile.relation_friction = min(1.0, profile.relation_friction + min(cap, 0.014))

    if contradictions_present > 0:
        profile.relation_friction = min(1.0, profile.relation_friction + min(cap, 0.010))

    return profile


def build_relation_summary(profile: PsycheV2Profile) -> str:
    """Build human-readable relation summary."""
    trust_label = "high" if profile.relation_trust > 0.7 else "moderate" if profile.relation_trust > 0.4 else "building"
    familiarity_label = "established" if profile.relation_familiarity > 0.6 else "developing" if profile.relation_familiarity > 0.3 else "new"
    sync_label = "strong" if profile.relation_sync > 0.7 else "moderate" if profile.relation_sync > 0.4 else "adapting"

    return f"Trust: {trust_label} | Familiarity: {familiarity_label} | Sync: {sync_label}"
