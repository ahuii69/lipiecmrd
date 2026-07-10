#!/usr/bin/env python3
"""
Memory V2 scoring and salience calculations.

Implements multi-dimensional scoring for memory importance and relevance.
"""

import logging
import math
import time
from typing import Any, Literal

from aihub.memory_psyche_contracts import (
    DecayBucket,
    MemoryStabilityTier,
    MemoryV2ScoringWeights,
)

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = MemoryV2ScoringWeights()

# ─── Long-horizon stability: promotion / demotion / runtime gates ─────────────

MIN_REINFORCEMENT_FOR_RUNTIME_WEIGHT = 2
MIN_REINFORCEMENT_FOR_OUTCOME_FULL_SIGNAL = 3

TRANSIENT_TO_DEVELOPING_RC = 3
TRANSIENT_TO_DEVELOPING_SUCC = 2
DEVELOPING_TO_STABLE_RC = 7
DEVELOPING_TO_STABLE_SUCC = 5
MIN_AGE_SEC_FOR_STABLE = 86_400.0

STABLE_DEMOTE_NET_FAIL = 3
DEVELOPING_DEMOTE_FAIL_DOM = 4

STALE_NO_REINFORCE_SEC = 45 * 86400
VERY_STALE_SEC = 120 * 86400
ARCHIVE_STALE_SEC = 200 * 86400

MemoryHorizonKind = Literal[
    "stable_preference",
    "stable_fact",
    "stable_procedure",
    "transient_mood_event",
    "transient_outcome",
    "temporary_contradiction",
    "reinforced_long_term_pattern",
]


def calculate_salience(
    importance_score: float,
    recurrence_score: float,
    emotional_weight: float,
    identity_relevance_score: float,
    confidence_score: float,
    freshness_score: float,
    weights: MemoryV2ScoringWeights = DEFAULT_WEIGHTS,
) -> float:
    """
    Calculate salience score using weighted formula.

    salience = sum(weight_i * score_i)

    All scores and weights should be in [0.0, 1.0].
    Returns clamped value in [0.0, 1.0].
    """
    salience = (
        weights.importance * importance_score
        + weights.recurrence * recurrence_score
        + weights.emotional * emotional_weight
        + weights.identity_relevance * identity_relevance_score
        + weights.confidence * confidence_score
        + weights.freshness * freshness_score
    )
    return max(0.0, min(1.0, salience))


def calculate_freshness(created_ts: float, is_pinned: bool = False) -> float:
    """
    Calculate freshness score based on age.

    - Newer items have higher freshness.
    - Pinned items decay slower.
    - Returns value in [0.0, 1.0].
    """
    now = time.time()
    age_seconds = now - created_ts

    if age_seconds < 0:
        return 1.0

    # Decay curve: exponential decay with configurable half-life
    # For pinned items: 30 days half-life
    # For normal items: 14 days half-life
    half_life_seconds = 30 * 86400 if is_pinned else 14 * 86400

    freshness = math.exp(-age_seconds / half_life_seconds)
    return max(0.0, min(1.0, freshness))


def calculate_identity_relevance(
    memory_type: str, scope: str, emotional_weight: float
) -> float:
    """
    Calculate how relevant this memory is to user identity.

    - Preferences and relationships are highly identity-relevant.
    - Autobiographical and lessons are moderately relevant.
    - Facts and procedural are less relevant unless emotional.
    """
    base_relevance = 0.5

    if memory_type == "preference":
        base_relevance = 0.85
    elif memory_type == "relationship":
        base_relevance = 0.80
    elif memory_type == "autobiographical":
        base_relevance = 0.75
    elif memory_type == "lesson":
        base_relevance = 0.65
    elif memory_type == "procedural":
        base_relevance = 0.55
    elif memory_type == "fact":
        base_relevance = 0.40

    if scope == "user":
        base_relevance = min(1.0, base_relevance + 0.15)
    elif scope == "session":
        base_relevance = max(0.0, base_relevance - 0.10)

    # Emotional weight boosts identity relevance
    emotional_boost = emotional_weight * 0.2
    return max(0.0, min(1.0, base_relevance + emotional_boost))


def recalculate_salience_for_item(item_dict: dict[str, Any]) -> float:
    """Recalculate salience for an existing memory item dict."""
    return calculate_salience(
        importance_score=float(item_dict.get("importance_score", 0.0)),
        recurrence_score=float(item_dict.get("recurrence_score", 0.0)),
        emotional_weight=float(item_dict.get("emotional_weight", 0.0)),
        identity_relevance_score=float(item_dict.get("identity_relevance_score", 0.0)),
        confidence_score=float(item_dict.get("confidence_score", 0.0)),
        freshness_score=float(item_dict.get("freshness_score", 0.0)),
    )


def calculate_relation_relevance(
    memory_type: str,
    scope: str,
    source_kind: str,
    emotional_weight: float,
) -> float:
    """
    Calculate how relevant this memory is to the current user-agent relation.

    - Relationship memories: high
    - Interaction outcomes: moderate
    - User preferences in user scope: high
    - General facts: low
    """
    base = 0.3

    if memory_type == "relationship":
        base = 0.9
    elif memory_type == "preference" and scope == "user":
        base = 0.8
    elif memory_type == "autobiographical":
        base = 0.7
    elif source_kind in ["agent_cycle", "chat_turn"]:
        base = 0.6

    # Emotional weight boosts relation relevance
    emotional_boost = emotional_weight * 0.15

    return max(0.0, min(1.0, base + emotional_boost))


def calculate_retrieval_priority(
    salience_score: float,
    recurrence_score: float,
    freshness_score: float,
    identity_relevance_score: float,
    relation_relevance_score: float,
    outcome_reinforcement_score: float,
    source_reliability_score: float,
    contradiction_state: str,
    is_pinned: bool,
    is_suppressed: bool,
    decay_bucket: str,
) -> float:
    """
    Calculate unified retrieval priority score.

    Combines all factors:
    - salience (base quality)
    - recurrence (how often reinforced)
    - freshness (recency)
    - identity relevance
    - relation relevance
    - outcome reinforcement (success/failure feedback)
    - source reliability
    - contradiction penalty
    - decay suppression
    - pinned boost

    Returns score in [0.0, 1.0].
    """
    if is_suppressed:
        return 0.0

    # Base priority from salience
    priority = salience_score * 0.25

    # Add recurrence weight
    priority += recurrence_score * 0.15

    # Add freshness
    priority += freshness_score * 0.12

    # Add identity and relation relevance
    priority += identity_relevance_score * 0.15
    priority += relation_relevance_score * 0.12

    # Add outcome reinforcement
    priority += outcome_reinforcement_score * 0.10

    # Add source reliability
    priority += source_reliability_score * 0.08

    # Contradiction penalty
    if contradiction_state == "conflicted":
        priority *= 0.3
    elif contradiction_state == "suspected":
        priority *= 0.6
    elif contradiction_state == "superseded":
        priority *= 0.2

    # Decay bucket penalty
    if decay_bucket == "cooling":
        priority *= 0.7
    elif decay_bucket == "archive_candidate":
        priority *= 0.4
    elif decay_bucket == "warm":
        priority *= 0.9

    # Pinned boost
    if is_pinned:
        priority = min(1.0, priority * 1.3)

    return max(0.0, min(1.0, priority))


def calculate_outcome_reinforcement(
    success_reinforcements: int,
    failure_reinforcements: int,
    total_reinforcements: int,
) -> float:
    """
    Outcome reinforcement toward retrieval priority.

    With fewer than MIN_REINFORCEMENT_FOR_OUTCOME_FULL_SIGNAL samples, the score
    is pulled toward neutral so one success or failure cannot dominate ranking.
    """
    if total_reinforcements == 0:
        return 0.5

    success_rate = success_reinforcements / max(1, total_reinforcements)
    failure_rate = failure_reinforcements / max(1, total_reinforcements)

    base = success_rate * 0.8
    base -= failure_rate * 0.3
    reinforcement_factor = min(1.0, total_reinforcements / 10.0)
    base += reinforcement_factor * 0.2
    raw = max(0.0, min(1.0, base))

    sample_weight = min(
        1.0,
        float(total_reinforcements) / float(MIN_REINFORCEMENT_FOR_OUTCOME_FULL_SIGNAL),
    )
    neutral = 0.5
    blended = neutral + (raw - neutral) * sample_weight
    return max(0.0, min(1.0, blended))


def tier_runtime_weight(tier: MemoryStabilityTier, reinforcement_count: int) -> float:
    """Scale how much this item biases retrieval and behavior (0..1)."""
    if reinforcement_count < MIN_REINFORCEMENT_FOR_RUNTIME_WEIGHT:
        return 0.35
    if tier == "stable":
        return 1.0
    if tier == "developing":
        return 0.72
    return 0.45


def classify_memory_horizon_kind(
    memory_type: str,
    scope: str,
    source_kind: str,
    contradiction_state: str,
    stability_tier: MemoryStabilityTier,
    reinforcement_count: int,
) -> MemoryHorizonKind:
    """
    Semantic stable vs transient classification (independent of promotion tier).

    Promotion tier (`stability_tier`) still gates runtime weight via reinforcement.
    """
    if contradiction_state in ("suspected", "conflicted", "superseded"):
        return "temporary_contradiction"
    if memory_type == "preference" and scope == "user":
        return "stable_preference"
    if memory_type == "fact" and scope == "user":
        return "stable_fact"
    if memory_type == "procedural":
        return "stable_procedure"
    if memory_type == "lesson" and source_kind == "chat_turn":
        return "transient_outcome"
    if scope in ("session", "interaction"):
        return "transient_mood_event"
    if stability_tier == "stable" and reinforcement_count >= 5:
        return "reinforced_long_term_pattern"
    if memory_type in ("autobiographical", "relationship") and scope == "user":
        return "reinforced_long_term_pattern"
    return "transient_mood_event"


def promotion_gate_explanation(item: Any) -> str:
    """Single human-readable reason for current tier / suppression (cockpit + trace)."""
    rc = int(getattr(item, "reinforcement_count", 0))
    succ = int(getattr(item, "success_reinforcements", 0))
    fail = int(getattr(item, "failure_reinforcements", 0))
    tier = getattr(item, "stability_tier", "transient")
    if getattr(item, "is_suppressed", False):
        return "suppressed: manual or forgetting sweep"
    if getattr(item, "decay_bucket", "active") == "archive_candidate":
        return "archival_candidate: stale with weak reinforcement"
    if rc < MIN_REINFORCEMENT_FOR_RUNTIME_WEIGHT:
        return f"low_sample: rc={rc} < {MIN_REINFORCEMENT_FOR_RUNTIME_WEIGHT} (limited runtime pull)"
    if tier == "transient":
        need = f"need rc>={TRANSIENT_TO_DEVELOPING_RC} and succ>={TRANSIENT_TO_DEVELOPING_SUCC} for developing"
        return f"transient_tier: {need}; have rc={rc} succ={succ} fail={fail}"
    if tier == "developing":
        return (
            f"developing: promote_to_stable needs rc>={DEVELOPING_TO_STABLE_RC}, "
            f"succ>={DEVELOPING_TO_STABLE_SUCC}, age>={int(MIN_AGE_SEC_FOR_STABLE)}s; "
            f"have rc={rc} succ={succ}"
        )
    return f"stable: sustained pattern rc={rc} succ={succ} fail={fail}"


def initial_stability_tier_for_new_item(
    memory_type: str, scope: str, source_kind: str
) -> MemoryStabilityTier:
    """New items never start stable; explicit user-scope learning can start developing."""
    if (
        source_kind == "explicit_learning"
        and scope == "user"
        and memory_type
        in (
            "preference",
            "fact",
            "relationship",
        )
    ):
        return "developing"
    if source_kind == "consolidation" and scope == "user":
        return "developing"
    return "transient"


def evaluate_decay_bucket_for_item(item: Any, now: float | None = None) -> DecayBucket:
    """Return updated decay_bucket for persistence (caller may set is_archived separately)."""
    from aihub.memory_v2_models import MemoryV2Item

    now = now or time.time()
    if not isinstance(item, MemoryV2Item):
        raw = getattr(item, "decay_bucket", "active")
        return (
            raw
            if raw in ("active", "warm", "cooling", "archive_candidate")
            else "active"
        )

    if item.is_pinned:
        return "active"

    last_touch = float(item.last_reinforced_ts or item.created_ts)
    stale = max(0.0, now - last_touch)
    rc = int(item.reinforcement_count)

    if item.stability_tier == "stable" and rc >= 5:
        if stale > STALE_NO_REINFORCE_SEC:
            return "warm"
        return "active"

    if stale > ARCHIVE_STALE_SEC and rc < 2:
        return "archive_candidate"
    if stale > VERY_STALE_SEC and rc < 4:
        return "cooling"
    if stale > STALE_NO_REINFORCE_SEC and item.stability_tier == "transient":
        return "warm"

    return item.decay_bucket if item.decay_bucket else "active"


def is_transient_contradiction_item(item: Any) -> bool:
    from aihub.memory_v2_models import MemoryV2Item

    if not isinstance(item, MemoryV2Item):
        return False
    return item.contradiction_state == "suspected"


def is_runtime_actionable_contradiction(item: Any) -> bool:
    from aihub.memory_v2_models import MemoryV2Item

    if not isinstance(item, MemoryV2Item):
        return False
    return item.contradiction_state in ("conflicted", "superseded")


def evaluate_stability_tier_after_update(
    item: Any, now: float | None = None
) -> MemoryStabilityTier:
    from aihub.memory_v2_models import MemoryV2Item

    if not isinstance(item, MemoryV2Item):
        return "transient"

    now = now or time.time()
    age = max(0.0, now - float(item.created_ts))
    rc = int(item.reinforcement_count)
    succ = int(item.success_reinforcements)
    fail = int(item.failure_reinforcements)
    net_pos = succ - fail
    net_neg = fail - succ
    tier = item.stability_tier

    if tier == "stable":
        if net_neg >= STABLE_DEMOTE_NET_FAIL and fail >= 3:
            return "developing"
        return "stable"

    if tier == "developing":
        if (
            rc >= DEVELOPING_TO_STABLE_RC
            and succ >= DEVELOPING_TO_STABLE_SUCC
            and net_pos >= 2
            and age >= MIN_AGE_SEC_FOR_STABLE
        ):
            return "stable"
        if fail >= DEVELOPING_DEMOTE_FAIL_DOM and succ <= 1:
            return "transient"
        if (
            rc >= TRANSIENT_TO_DEVELOPING_RC
            and succ >= TRANSIENT_TO_DEVELOPING_SUCC
            and net_pos >= 1
        ):
            return "developing"
        return "developing"

    if (
        rc >= TRANSIENT_TO_DEVELOPING_RC
        and succ >= TRANSIENT_TO_DEVELOPING_SUCC
        and net_pos >= 1
    ):
        return "developing"
    return "transient"


def apply_stability_evaluation(item: Any) -> Any:
    from aihub.memory_v2_models import MemoryV2Item

    if not isinstance(item, MemoryV2Item):
        return item
    new_tier = evaluate_stability_tier_after_update(item)
    new_decay = evaluate_decay_bucket_for_item(
        item.model_copy(update={"stability_tier": new_tier}), time.time()
    )
    return item.model_copy(
        update={
            "stability_tier": new_tier,
            "decay_bucket": new_decay,
            "updated_ts": time.time(),
        }
    )


def effective_procedure_confidence(
    confidence_score: float,
    evidence_count: int,
    success_count: int,
    failure_count: int,
) -> float:
    n = max(0, int(evidence_count))
    if n <= 1:
        return min(confidence_score, 0.35)
    if n < 3:
        scale = 0.45 + 0.18 * n
        return min(1.0, confidence_score * scale)
    if n < 5:
        scale = 0.82 + 0.045 * (n - 3)
        return min(1.0, confidence_score * scale)
    total_sf = max(1, success_count + failure_count)
    fail_ratio = failure_count / total_sf
    if fail_ratio > 0.35:
        return max(0.0, confidence_score * (1.0 - 0.25 * (fail_ratio - 0.35)))
    return confidence_score


def memory_item_to_stability_meta(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "memory_type": item.memory_type,
        "stability_tier": item.stability_tier,
        "horizon_kind": classify_memory_horizon_kind(
            item.memory_type,
            item.scope,
            item.source_kind,
            item.contradiction_state,
            item.stability_tier,
            item.reinforcement_count,
        ),
        "reinforcement_count": item.reinforcement_count,
        "contradiction_state": item.contradiction_state,
        "runtime_weight": tier_runtime_weight(
            item.stability_tier, item.reinforcement_count
        ),
        "promotion_explanation": promotion_gate_explanation(item),
    }
