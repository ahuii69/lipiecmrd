#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PolicyEngine — Silnik polityk decyzyjnych oparty na doświadczeniu.

Buduje "hinty" z historii refleksji i doświadczeń, które modyfikują
przyszłe decyzje CognitiveController:

  - Boost: akcja sprawdzona, zwiększ jej priorytet/pewność
  - Penalize: akcja zawodna, obniż jej priorytet
  - Caution: wymagana ostrożność (dodatkowy kontekst lub zatwierdzenie)
  - Avoid: silne "nie rób tego" na bazie powtarzających się porażek

Hinty są przechowywane w DB i dostarczane do CognitiveController.decide().
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from aihub.db import exec_one, fetch_all, json_dumps, now_ts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PolicyHint:
    """Hint for decision-making based on past experience."""

    action_type: str
    signal: str  # boost | penalize | caution | avoid
    weight: float  # 0.0-1.0
    reason: str
    source_count: int = 0  # how many reflections contributed
    avg_outcome_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyProfile:
    """Aggregated policy profile for a user."""

    user_id: str
    hints: List[PolicyHint]
    generated_at: float
    total_reflections: int = 0
    reliability_index: float = 0.5  # 0.0=unreliable, 1.0=highly reliable


@dataclass
class PolicyFeedback:
    """Feedback computed from post-execution hindsights for policy adjustment."""

    confidence_delta: float          # clipped to ±0.15; neg = was overconfident
    handoff_bias: float              # pos = should handoff sooner, neg = later
    blocker_sensitivity: float       # pos = raise blocker sensitivity, neg = lower
    simulation_risk_calibration: float  # pos = risk was under-predicted
    strategy_adjustments: Dict[str, float]  # per-action-type fitness deltas
    applied: bool                    # False when signals are empty / all-neutral
    summary: str


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class PolicyEngine:
    """
    Silnik polityk decyzyjnych.

    Buduje profil polityk z refleksji:
    1. Agreguje policy_signal z reflections per action_type
    2. Waży signały (nowsze → ważniejsze, via decay)
    3. Generuje PolicyHint per akcja
    4. Calculates reliability_index (overall system health)
    """

    # Decay factor per reflection (older reflections matter less)
    TIME_DECAY_FACTOR = 0.95  # per-slot in window

    # Minimum reflections to generate a hint
    MIN_REFLECTIONS_FOR_HINT = 2

    # Avoid threshold: repeated penalize signals
    AVOID_PENALIZE_COUNT = 4

    # Signal weights for aggregation
    SIGNAL_SCORES = {
        "boost": 1.0,
        "caution": 0.0,
        "neutral": 0.0,
        "penalize": -1.0,
    }

    def build_profile(
        self,
        user_id: str,
        window: int = 50,
    ) -> PolicyProfile:
        """
        Build policy profile from recent reflections.

        Returns PolicyProfile with hints per action type.
        """
        reflections = self._load_reflections(user_id, window)

        if not reflections:
            return PolicyProfile(
                user_id=user_id,
                hints=[],
                generated_at=now_ts(),
                total_reflections=0,
                reliability_index=0.5,
            )

        # Group by action_type
        by_action: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in reflections:
            by_action[r["action_type"]].append(r)

        hints: List[PolicyHint] = []
        for action_type, action_refs in by_action.items():
            hint = self._build_hint_for_action(action_type, action_refs)
            if hint:
                hints.append(hint)

        # Compute reliability index
        reliability = self._compute_reliability(reflections)

        profile = PolicyProfile(
            user_id=user_id,
            hints=hints,
            generated_at=now_ts(),
            total_reflections=len(reflections),
            reliability_index=reliability,
        )

        # Persist profile
        self._persist_profile(profile)

        return profile

    def get_hints_for_decision(
        self,
        user_id: str,
        action_type: str,
        window: int = 50,
    ) -> Optional[PolicyHint]:
        """
        Get policy hint for a specific action type.

        Quick lookup for CognitiveController.decide().
        """
        profile = self.build_profile(user_id, window)
        for hint in profile.hints:
            if hint.action_type == action_type:
                return hint
        return None

    def apply_hints_to_confidence(
        self,
        confidence: float,
        hints: List[PolicyHint],
        action_type: str,
    ) -> tuple[float, str]:
        """
        Modify decision confidence based on policy hints.

        Returns (adjusted_confidence, adjustment_reason).
        """
        matching = [h for h in hints if h.action_type == action_type]
        if not matching:
            return confidence, "no_policy_hint"

        hint = matching[0]  # Take first (should be unique per action_type)

        if hint.signal == "boost":
            adj = min(1.0, confidence + hint.weight * 0.15)
            return adj, f"boost +{(adj - confidence):.3f} ({hint.reason})"

        if hint.signal == "penalize":
            adj = max(0.05, confidence - hint.weight * 0.20)
            return adj, f"penalize -{(confidence - adj):.3f} ({hint.reason})"

        if hint.signal == "avoid":
            adj = max(0.05, confidence * 0.3)
            return adj, f"avoid (confidence reduced to {adj:.3f}; {hint.reason})"

        if hint.signal == "caution":
            adj = max(0.1, confidence - hint.weight * 0.10)
            return adj, f"caution -{(confidence - adj):.3f} ({hint.reason})"

        return confidence, "unknown_signal"

    def compute_feedback(
        self,
        profile: PolicyProfile,
        reflections: List[Dict[str, Any]],
    ) -> "PolicyFeedback":
        """Compute PolicyFeedback from post-execution hindsight reflections.

        Each element in *reflections* is either:
          - {"hindsight": {<hindsight-fields>}, "action_type": str}
          - a flat dict with hindsight fields directly (legacy / flat format)

        Index 0 = most recent; time-decay reduces weight of older items.
        """
        if not reflections:
            return PolicyFeedback(
                confidence_delta=0.0,
                handoff_bias=0.0,
                blocker_sensitivity=0.0,
                simulation_risk_calibration=0.0,
                strategy_adjustments={},
                applied=False,
                summary="no_reflections",
            )

        DECAY_RATE = 0.15  # per index step; index 0 is newest

        w_confidence = 0.0
        w_total = 0.0
        handoff_score = 0.0
        blocker_score = 0.0
        risk_total = 0.0
        strategy_raw: Dict[str, float] = defaultdict(float)

        for i, ref in enumerate(reflections):
            # Accept both wrapped {"hindsight": {...}, ...} and flat formats
            hs: Dict[str, Any] = ref.get("hindsight", ref)  # type: ignore[arg-type]
            act = str(ref.get("action_type", hs.get("_action_type", "unknown")))

            weight = 1.0 / (1.0 + DECAY_RATE * i)

            ch = float(hs.get("confidence_hindsight") or 0.0)
            w_confidence += ch * weight
            w_total += weight

            hh = hs.get("handoff_hindsight", "na")
            if hh == "earlier":
                handoff_score += weight
            elif hh == "later":
                handoff_score -= weight

            bh = hs.get("blocker_hindsight", "na")
            if bh == "stronger":
                blocker_score += weight
            elif bh == "weaker":
                blocker_score -= weight

            rh = float(hs.get("risk_hindsight") or 0.0)
            risk_total += rh * weight

            sf = hs.get("strategy_fit", "neutral")
            if sf == "good":
                strategy_raw[act] += 0.10 * weight
            elif sf == "bad":
                strategy_raw[act] -= 0.10 * weight

        raw_confidence = w_confidence / w_total if w_total > 0 else 0.0
        confidence_delta = round(max(-0.15, min(0.15, raw_confidence)), 4)
        handoff_bias = round(handoff_score / w_total if w_total > 0 else 0.0, 4)
        blocker_sensitivity = round(blocker_score / w_total if w_total > 0 else 0.0, 4)
        simulation_risk_calibration = round(
            risk_total / w_total if w_total > 0 else 0.0, 4
        )
        strategy_adjustments = {
            k: round(v, 4) for k, v in strategy_raw.items() if v != 0.0
        }

        applied = bool(
            confidence_delta != 0.0
            or handoff_bias != 0.0
            or blocker_sensitivity != 0.0
            or simulation_risk_calibration != 0.0
            or strategy_adjustments
        )
        summary = (
            f"delta={confidence_delta:.3f} hb={handoff_bias:.3f} "
            f"bs={blocker_sensitivity:.3f} src={len(reflections)}"
        )
        return PolicyFeedback(
            confidence_delta=confidence_delta,
            handoff_bias=handoff_bias,
            blocker_sensitivity=blocker_sensitivity,
            simulation_risk_calibration=simulation_risk_calibration,
            strategy_adjustments=strategy_adjustments,
            applied=applied,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Internal: build hint for action type
    # ------------------------------------------------------------------

    def _build_hint_for_action(
        self, action_type: str, reflections: List[Dict[str, Any]]
    ) -> Optional[PolicyHint]:
        """Build policy hint from reflections for one action type."""
        if len(reflections) < self.MIN_REFLECTIONS_FOR_HINT:
            return None

        # Weighted aggregation with time decay
        total_score = 0.0
        total_weight = 0.0
        outcome_scores: List[float] = []
        signal_counts: Dict[str, int] = defaultdict(int)

        for i, r in enumerate(reflections):
            decay = self.TIME_DECAY_FACTOR**i
            signal = r.get("policy_signal", "neutral")
            weight = float(r.get("policy_weight", 0.3))
            score = self.SIGNAL_SCORES.get(signal, 0.0)

            total_score += score * weight * decay
            total_weight += weight * decay
            outcome_scores.append(float(r.get("outcome_score", 0.5)))
            signal_counts[signal] += 1

        if total_weight == 0:
            return None

        avg_signal = total_score / total_weight
        avg_outcome = sum(outcome_scores) / len(outcome_scores)

        # Determine resulting signal
        penalize_count = signal_counts.get("penalize", 0)
        boost_count = signal_counts.get("boost", 0)
        caution_count = signal_counts.get("caution", 0)

        if penalize_count >= self.AVOID_PENALIZE_COUNT:
            signal = "avoid"
            weight = min(1.0, 0.6 + penalize_count * 0.05)
            reason = (
                f"{penalize_count} penalize z {len(reflections)} refleksji — "
                f"avg_outcome={avg_outcome:.2f}"
            )
        elif avg_signal > 0.3:
            signal = "boost"
            weight = min(1.0, abs(avg_signal))
            reason = (
                f"{boost_count} boost z {len(reflections)} refleksji — "
                f"avg_outcome={avg_outcome:.2f}"
            )
        elif avg_signal < -0.3:
            signal = "penalize"
            weight = min(1.0, abs(avg_signal))
            reason = (
                f"{penalize_count} penalize z {len(reflections)} refleksji — "
                f"avg_outcome={avg_outcome:.2f}"
            )
        elif caution_count >= 2:
            signal = "caution"
            weight = 0.4
            reason = f"{caution_count} caution z {len(reflections)} refleksji"
        else:
            signal = "neutral"
            weight = 0.2
            reason = f"Brak dominującej tendencji ({len(reflections)} refleksji)"

        return PolicyHint(
            action_type=action_type,
            signal=signal,
            weight=round(weight, 3),
            reason=reason,
            source_count=len(reflections),
            avg_outcome_score=round(avg_outcome, 3),
        )

    # ------------------------------------------------------------------
    # Reliability index
    # ------------------------------------------------------------------

    def _compute_reliability(self, reflections: List[Dict[str, Any]]) -> float:
        """
        Compute system reliability index from reflections.

        0.0 = everything fails
        0.5 = mixed
        1.0 = everything succeeds
        """
        if not reflections:
            return 0.5

        success_count = sum(1 for r in reflections if r.get("outcome") == "success")
        partial_count = sum(1 for r in reflections if r.get("outcome") == "partial")

        return round((success_count + partial_count * 0.5) / len(reflections), 3)

    # ------------------------------------------------------------------
    # DB operations
    # ------------------------------------------------------------------

    def _load_reflections(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Load recent reflections from DB."""
        rows = fetch_all(
            """
            SELECT action_type, outcome, outcome_score,
                   policy_signal, policy_weight, ts
            FROM reflections
            WHERE user_id=?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [
            {
                "action_type": r["action_type"],
                "outcome": r["outcome"],
                "outcome_score": float(r["outcome_score"]),
                "policy_signal": r["policy_signal"],
                "policy_weight": float(r["policy_weight"]),
                "ts": float(r["ts"]),
            }
            for r in rows
        ]

    def _persist_profile(self, profile: PolicyProfile) -> None:
        """Persist policy profile to DB."""
        try:
            hints_data = [
                {
                    "action_type": h.action_type,
                    "signal": h.signal,
                    "weight": h.weight,
                    "reason": h.reason,
                    "source_count": h.source_count,
                    "avg_outcome_score": h.avg_outcome_score,
                }
                for h in profile.hints
            ]
            exec_one(
                """
                INSERT INTO policy_profiles(
                    user_id, hints, reliability_index,
                    total_reflections, ts
                ) VALUES (?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    hints=excluded.hints,
                    reliability_index=excluded.reliability_index,
                    total_reflections=excluded.total_reflections,
                    ts=excluded.ts
                """,
                (
                    profile.user_id,
                    json_dumps(hints_data),
                    profile.reliability_index,
                    profile.total_reflections,
                    profile.generated_at,
                ),
            )
        except Exception:
            logger.debug("Failed to persist policy profile", exc_info=True)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_profile(self, user_id: str) -> Dict[str, Any]:
        """Get current policy profile for diagnostics."""
        profile = self.build_profile(user_id)
        return {
            "user_id": profile.user_id,
            "reliability_index": profile.reliability_index,
            "total_reflections": profile.total_reflections,
            "hints": [
                {
                    "action_type": h.action_type,
                    "signal": h.signal,
                    "weight": h.weight,
                    "reason": h.reason,
                    "source_count": h.source_count,
                    "avg_outcome_score": h.avg_outcome_score,
                }
                for h in profile.hints
            ],
            "generated_at": profile.generated_at,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_policy_engine = PolicyEngine()


def build_policy_profile(user_id: str, window: int = 50) -> PolicyProfile:
    """Public API — build full policy profile."""
    return _policy_engine.build_profile(user_id, window)


def get_policy_hints(user_id: str, action_type: str) -> Optional[PolicyHint]:
    """Public API — get hint for specific action."""
    return _policy_engine.get_hints_for_decision(user_id, action_type)


def apply_policy_to_confidence(
    confidence: float, hints: List[PolicyHint], action_type: str
) -> tuple[float, str]:
    """Public API — adjust confidence."""
    return _policy_engine.apply_hints_to_confidence(confidence, hints, action_type)


def get_policy_profile_data(user_id: str) -> Dict[str, Any]:
    """Public API — diagnostics."""
    return _policy_engine.get_profile(user_id)


def compute_policy_feedback(profile: PolicyProfile) -> PolicyFeedback:
    """
    Compute policy feedback from profile hindsight data.
    
    Returns numeric deltas for confidence, handoff, blocker, risk.
    """
    if not profile or not profile.hints:
        return PolicyFeedback(
            confidence_delta=0.0,
            handoff_bias=0.0,
            blocker_sensitivity=0.0,
            simulation_risk_calibration=0.0,
            strategy_adjustments={},
            applied=False,
            summary="No policy hints available",
        )
    
    # Aggregate signals from hints
    confidence_sum = 0.0
    handoff_sum = 0.0
    blocker_sum = 0.0
    risk_sum = 0.0
    strategy_adj: Dict[str, float] = {}
    
    for hint in profile.hints[:20]:
        weight = hint.weight
        signal = hint.signal
        
        if signal == "boost":
            confidence_sum += weight * 0.05
        elif signal == "penalize":
            confidence_sum -= weight * 0.05
        elif signal == "caution":
            blocker_sum += weight * 0.03
            risk_sum += weight * 0.02
        elif signal == "avoid":
            blocker_sum += weight * 0.05
            handoff_sum += weight * 0.02
        
        # Strategy-specific adjustments
        action_type = hint.action_type
        if action_type:
            current = strategy_adj.get(action_type, 0.0)
            if signal == "boost":
                strategy_adj[action_type] = current + weight * 0.1
            elif signal == "penalize":
                strategy_adj[action_type] = current - weight * 0.1
    
    # Clamp
    confidence_delta = max(-0.15, min(0.15, confidence_sum))
    handoff_bias = max(-0.20, min(0.20, handoff_sum))
    blocker_sensitivity = max(-0.10, min(0.20, blocker_sum))
    simulation_risk_cal = max(-0.10, min(0.15, risk_sum))
    
    applied = (
        abs(confidence_delta) >= 0.01
        or abs(handoff_bias) >= 0.01
        or abs(blocker_sensitivity) >= 0.01
        or abs(simulation_risk_cal) >= 0.01
    )
    
    summary = ""
    if applied:
        parts = []
        if confidence_delta > 0:
            parts.append(f"conf+{confidence_delta:.2f}")
        elif confidence_delta < 0:
            parts.append(f"conf{confidence_delta:.2f}")
        if handoff_bias > 0:
            parts.append(f"handoff+{handoff_bias:.2f}")
        if blocker_sensitivity > 0:
            parts.append(f"blocker+{blocker_sensitivity:.2f}")
        summary = "; ".join(parts) if parts else "Policy feedback applied"
    else:
        summary = "Policy feedback neutral"
    
    return PolicyFeedback(
        confidence_delta=confidence_delta,
        handoff_bias=handoff_bias,
        blocker_sensitivity=blocker_sensitivity,
        simulation_risk_calibration=simulation_risk_cal,
        strategy_adjustments=strategy_adj,
        applied=applied,
        summary=summary,
    )
