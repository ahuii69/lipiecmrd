#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ReflectionEngine — Silnik refleksji post-akcyjnej.

Analizuje wyniki wykonanych akcji i generuje:
  - lesson_learned: wnioski z akcji
  - policy_signal: sygnał do PolicyEngine (boost/penalize/neutral)
  - recommended_adjustment: konkretna rekomendacja dla przyszłych decyzji

Działa PO zakończeniu procesu decyzyjnego (wpiąć w agent_loop.process_decision).
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from aihub.db import append_event, exec_one, fetch_all, json_dumps, json_loads, now_ts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ReflectionInput:
    """Input for reflection analysis."""

    user_id: str
    action_type: str
    parameters: Dict[str, Any]
    confidence: float
    execution_result: Dict[str, Any]
    decision_reasoning: str = ""
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReflectionOutput:
    """Output of reflection analysis."""

    reflection_id: str
    user_id: str
    action_type: str
    outcome: str  # success | partial | failure | skipped
    outcome_score: float  # 0.0-1.0
    lesson_learned: str
    policy_signal: str  # boost | penalize | neutral | caution
    policy_weight: float  # 0.0-1.0 (strength of signal)
    recommended_adjustment: str
    patterns_detected: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Hindsight fields (populated by _compute_hindsight during reflect())
    strategy_fit: str = "neutral"
    handoff_hindsight: str = "na"
    blocker_hindsight: str = "na"
    confidence_hindsight: float = 0.0
    risk_hindsight: float = 0.0
    deliberation_hindsight: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class ReflectionEngine:
    """
    Silnik refleksji post-akcyjnej.

    Po każdej akcji analizuje:
    1. Outcome classification — czy akcja zakończyła się sukcesem
    2. Pattern detection — wykrycie powtarzających się wzorców (np. powtarzające się błędy)
    3. Lesson extraction — wyciągnięcie wniosku
    4. Policy signal generation — sygnał do PolicyEngine
    """

    # Outcome scoring heuristics
    _SUCCESS_INDICATORS = ["ok", "success", "done", "completed", "found", "stored"]
    _FAILURE_INDICATORS = ["error", "fail", "timeout", "denied", "not found", "limit"]
    _PARTIAL_INDICATORS = ["partial", "degraded", "fallback", "incomplete"]

    # Pattern repetition window
    PATTERN_WINDOW = 20  # last N reflections to check for patterns

    # Minimum reflections for pattern detection
    MIN_PATTERN_COUNT = 3

    def reflect(self, rinput: ReflectionInput) -> ReflectionOutput:
        """
        Perform post-action reflection.

        This is the core method — called after every action execution.
        """
        reflection_id = hashlib.sha256(
            f"{rinput.user_id}:{rinput.action_type}:{time.time_ns()}".encode()
        ).hexdigest()[:24]

        # 1. Classify outcome
        outcome, outcome_score = self._classify_outcome(rinput.execution_result)

        # 2. Detect patterns from recent reflections
        patterns = self._detect_patterns(rinput.user_id, rinput.action_type, outcome)

        # 3. Extract lesson
        lesson = self._extract_lesson(
            rinput.action_type, outcome, outcome_score, rinput, patterns
        )

        # 4. Generate policy signal
        signal, weight = self._generate_policy_signal(
            outcome, outcome_score, patterns, rinput
        )

        # 5. Generate adjustment recommendation
        adjustment = self._recommend_adjustment(
            rinput.action_type, outcome, patterns, rinput
        )

        result = ReflectionOutput(
            reflection_id=reflection_id,
            user_id=rinput.user_id,
            action_type=rinput.action_type,
            outcome=outcome,
            outcome_score=outcome_score,
            lesson_learned=lesson,
            policy_signal=signal,
            policy_weight=weight,
            recommended_adjustment=adjustment,
            patterns_detected=patterns,
            metadata={
                "confidence": rinput.confidence,
                "reasoning": rinput.decision_reasoning[:200],
            },
        )

        # Populate hindsight fields if context carries the necessary keys
        if rinput.context.get("selected_strategy"):
            hindsight = self._compute_hindsight(rinput, outcome, outcome_score)
            result.strategy_fit = hindsight["strategy_fit"]
            result.handoff_hindsight = hindsight["handoff_hindsight"]
            result.blocker_hindsight = hindsight["blocker_hindsight"]
            result.confidence_hindsight = hindsight["confidence_hindsight"]
            result.risk_hindsight = hindsight["risk_hindsight"]

        # 6. Persist
        self._persist_reflection(result)

        # 7. Event
        append_event(
            rinput.user_id,
            "reflection.completed",
            {
                "reflection_id": reflection_id,
                "action": rinput.action_type,
                "outcome": outcome,
                "score": outcome_score,
                "signal": signal,
                "lesson": lesson[:100],
            },
        )

        return result

    # ------------------------------------------------------------------
    # Outcome classification
    # ------------------------------------------------------------------

    def _classify_outcome(self, execution_result: Dict[str, Any]) -> tuple[str, float]:
        """Classify action outcome based on execution result."""
        if not execution_result:
            return "skipped", 0.0

        # Direct indicators
        ok = execution_result.get("ok", execution_result.get("executed", False))
        error = execution_result.get("error", "")

        if isinstance(ok, bool) and ok and not error:
            # Check for partial/degraded
            if execution_result.get("degraded_flag") or execution_result.get(
                "fallback_flag"
            ):
                return "partial", 0.6

            return "success", self._compute_success_score(execution_result)

        if error:
            return "failure", self._compute_failure_score(error, execution_result)

        # Heuristic from result text
        result_str = str(execution_result).lower()

        success_hits = sum(1 for s in self._SUCCESS_INDICATORS if s in result_str)
        failure_hits = sum(1 for s in self._FAILURE_INDICATORS if s in result_str)
        partial_hits = sum(1 for s in self._PARTIAL_INDICATORS if s in result_str)

        if failure_hits > success_hits:
            return "failure", max(0.1, 0.5 - failure_hits * 0.1)
        if partial_hits > 0:
            return "partial", 0.5 + partial_hits * 0.05
        if success_hits > 0:
            return "success", min(1.0, 0.7 + success_hits * 0.05)

        # Unknown or skipped
        executed = execution_result.get("executed", None)
        if executed is False:
            return "skipped", 0.0

        return "success", 0.6  # default: weak success

    def _compute_success_score(self, result: Dict[str, Any]) -> float:
        """Compute granular success score from execution result."""
        score = 0.75

        # Bonus for rich results
        if result.get("total", 0) > 0:
            score += 0.1
        if result.get("node_id"):
            score += 0.05
        if result.get("analysis"):
            score += 0.05

        # Confidence boost
        conf = result.get("confidence", 0)
        if isinstance(conf, (int, float)) and conf > 0.7:
            score += 0.05

        return min(1.0, score)

    def _compute_failure_score(self, error: str, result: Dict[str, Any]) -> float:
        """How bad was the failure (lower = worse)."""
        score = 0.3

        error_lower = str(error).lower()
        if "timeout" in error_lower:
            score = 0.2
        elif "limit" in error_lower or "resource" in error_lower:
            score = 0.25
        elif "not found" in error_lower:
            score = 0.35
        elif "permission" in error_lower or "denied" in error_lower:
            score = 0.1

        return score

    # ------------------------------------------------------------------
    # Pattern detection
    # ------------------------------------------------------------------

    def _detect_patterns(
        self, user_id: str, action_type: str, current_outcome: str
    ) -> List[str]:
        """Detect behavioral patterns from recent reflections."""
        patterns: List[str] = []

        try:
            recent = fetch_all(
                """
                SELECT action_type, outcome, outcome_score, policy_signal, ts
                FROM reflections
                WHERE user_id=?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (user_id, self.PATTERN_WINDOW),
            )

            if len(recent) < 2:
                return patterns

            # Pattern: same action fails repeatedly
            same_action_failures = sum(
                1
                for r in recent
                if r["action_type"] == action_type and r["outcome"] == "failure"
            )
            if same_action_failures >= self.MIN_PATTERN_COUNT:
                patterns.append(
                    f"repeated_failure:{action_type}:{same_action_failures}"
                )

            # Pattern: general failure streak
            streak = 0
            for r in recent:
                if r["outcome"] in ("failure", "skipped"):
                    streak += 1
                else:
                    break
            if streak >= 3:
                patterns.append(f"failure_streak:{streak}")

            # Pattern: flip-flop (alternating success/failure)
            if len(recent) >= 4:
                outcomes = [r["outcome"] for r in recent[:6]]
                flips = sum(
                    1
                    for i in range(len(outcomes) - 1)
                    if outcomes[i] != outcomes[i + 1]
                )
                if flips >= 3:
                    patterns.append("flip_flop_pattern")

            # Pattern: same action always succeeds (reliable action)
            same_action_successes = sum(
                1
                for r in recent
                if r["action_type"] == action_type and r["outcome"] == "success"
            )
            if same_action_successes >= self.MIN_PATTERN_COUNT:
                patterns.append(
                    f"reliable_action:{action_type}:{same_action_successes}"
                )

        except Exception:
            logger.debug("Pattern detection failed", exc_info=True)

        return patterns

    # ------------------------------------------------------------------
    # Lesson extraction
    # ------------------------------------------------------------------

    def _extract_lesson(
        self,
        action_type: str,
        outcome: str,
        outcome_score: float,
        rinput: ReflectionInput,
        patterns: List[str],
    ) -> str:
        """Extract human-readable lesson from action result."""
        parts: List[str] = []

        # Core outcome lesson
        if outcome == "success":
            parts.append(
                f"Akcja '{action_type}' zakończona sukcesem (score={outcome_score:.2f})."
            )
        elif outcome == "partial":
            parts.append(
                f"Akcja '{action_type}' częściowo udana (score={outcome_score:.2f}) — "
                "potrzebne dalsze kroki."
            )
        elif outcome == "failure":
            error = rinput.execution_result.get("error", "unknown")
            parts.append(
                f"Akcja '{action_type}' nieudana: {str(error)[:100]} "
                f"(score={outcome_score:.2f})."
            )
        elif outcome == "skipped":
            reason = rinput.execution_result.get("reason", "brak danych")
            parts.append(f"Akcja '{action_type}' pominięta: {str(reason)[:100]}.")

        # Pattern-based lessons
        for p in patterns:
            if p.startswith("repeated_failure:"):
                action, count = p.split(":")[1], p.split(":")[2]
                parts.append(
                    f"UWAGA: {action} zawiodło {count}x z rzędu — rozważ alternatywę."
                )
            elif p.startswith("failure_streak:"):
                parts.append(
                    f"Seria {p.split(':')[1]} niepowodzeń — system może wymagać diagnostyki."
                )
            elif p == "flip_flop_pattern":
                parts.append(
                    "Wykryto niestabilność (flip-flop) — wyniki są nieprzewidywalne."
                )
            elif p.startswith("reliable_action:"):
                parts.append(f"'{p.split(':')[1]}' jest sprawdzoną, niezawodną akcją.")

        # Confidence lesson
        if rinput.confidence < 0.4:
            parts.append(
                "Decyzja podjęta przy niskiej pewności — "
                "w przyszłości warto zebrać więcej kontekstu."
            )

        return " ".join(parts) if parts else f"Brak wniosków dla '{action_type}'."

    # ------------------------------------------------------------------
    # Policy signal generation
    # ------------------------------------------------------------------

    def _generate_policy_signal(
        self,
        outcome: str,
        outcome_score: float,
        patterns: List[str],
        rinput: ReflectionInput,
    ) -> tuple[str, float]:
        """Generate policy signal and weight for PolicyEngine."""
        signal = "neutral"
        weight = 0.3

        if outcome == "success":
            signal = "boost"
            weight = min(1.0, 0.4 + outcome_score * 0.3)

        elif outcome == "failure":
            signal = "penalize"
            weight = min(1.0, 0.5 + (1.0 - outcome_score) * 0.3)

        elif outcome == "partial":
            signal = "caution"
            weight = 0.4

        elif outcome == "skipped":
            signal = "neutral"
            weight = 0.1

        # Pattern modifiers
        for p in patterns:
            if p.startswith("repeated_failure:"):
                signal = "penalize"
                weight = min(1.0, weight + 0.2)
            elif p.startswith("failure_streak:"):
                signal = "penalize"
                weight = min(1.0, weight + 0.15)
            elif p == "flip_flop_pattern":
                signal = "caution"
                weight = min(1.0, weight + 0.1)
            elif p.startswith("reliable_action:"):
                if signal != "penalize":
                    signal = "boost"
                    weight = min(1.0, weight + 0.1)

        return signal, round(weight, 3)

    # ------------------------------------------------------------------
    # Adjustment recommendation
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Hindsight computation
    # ------------------------------------------------------------------

    def _compute_hindsight(
        self,
        rinput: "ReflectionInput",
        outcome_str: str,
        outcome_score: float,
    ) -> Dict[str, Any]:
        """Compute hindsight analysis comparing predicted vs actual outcome.

        Returns a dict with keys:
          strategy_fit, confidence_hindsight, handoff_hindsight,
          blocker_hindsight, risk_hindsight.
        """
        ctx = rinput.context
        HEAVY = {"agentic", "research"}

        predicted = rinput.confidence or float(ctx.get("strategy_confidence") or 0.5)
        strategy = ctx.get("selected_strategy", "instant")
        handoff = bool(ctx.get("handoff_happened", False))
        blocker = bool(ctx.get("blocker_was_active", False))
        sim_risk = float(ctx.get("simulation_risk") or 0.0)

        # strategy_fit
        if outcome_str == "success":
            strategy_fit = "good"
        elif outcome_str == "failure":
            strategy_fit = "bad"
        elif outcome_str == "partial" and strategy in HEAVY:
            strategy_fit = "bad"
        else:
            strategy_fit = "neutral"

        # confidence_hindsight: positive means we underestimated, negative = overestimated
        confidence_hindsight = round(outcome_score - predicted, 2)

        # handoff_hindsight
        if outcome_str == "failure" and not handoff and strategy in HEAVY:
            handoff_hindsight = "earlier"
        elif outcome_str == "failure" and handoff:
            handoff_hindsight = "later"
        elif outcome_str == "success" and handoff:
            handoff_hindsight = "correct"
        else:
            handoff_hindsight = "correct"

        # blocker_hindsight
        if blocker:
            if outcome_str == "failure":
                blocker_hindsight = "stronger"
            elif outcome_str == "success":
                blocker_hindsight = "weaker"
            else:
                blocker_hindsight = "correct"
        elif outcome_str == "failure" and outcome_score <= 0.15:
            blocker_hindsight = "stronger"
        else:
            blocker_hindsight = "na"

        # risk_hindsight: delta between actual risk and simulated risk
        if outcome_str == "failure":
            actual_risk = max(0.5, 1.0 - outcome_score)
        else:
            actual_risk = 0.3 * (1.0 - outcome_score)
        risk_hindsight = round(actual_risk - sim_risk, 4)

        return {
            "strategy_fit": strategy_fit,
            "confidence_hindsight": confidence_hindsight,
            "handoff_hindsight": handoff_hindsight,
            "blocker_hindsight": blocker_hindsight,
            "risk_hindsight": risk_hindsight,
        }

    def _recommend_adjustment(
        self,
        action_type: str,
        outcome: str,
        patterns: List[str],
        rinput: ReflectionInput,
    ) -> str:
        """Generate concrete adjustment recommendation."""
        if outcome == "success" and not any("failure" in p for p in patterns):
            return "no_change"

        recommendations: List[str] = []

        if outcome == "failure":
            error = str(rinput.execution_result.get("error", ""))
            if "timeout" in error.lower():
                recommendations.append("increase_timeout")
            elif "limit" in error.lower() or "resource" in error.lower():
                recommendations.append("reduce_resource_usage")
            elif "not found" in error.lower():
                recommendations.append("broaden_search_scope")
            else:
                recommendations.append("try_alternative_strategy")

        for p in patterns:
            if p.startswith("repeated_failure:"):
                action = p.split(":")[1]
                recommendations.append(f"avoid_{action}")
            elif p.startswith("failure_streak:"):
                recommendations.append("diagnostic_check")
            elif p == "flip_flop_pattern":
                recommendations.append("stabilize_approach")

        if rinput.confidence < 0.4:
            recommendations.append("gather_more_context")

        return ",".join(recommendations) if recommendations else "no_change"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_reflection(self, ref: ReflectionOutput) -> None:
        """Persist reflection to DB."""
        try:
            exec_one(
                """
                INSERT INTO reflections(
                    id, user_id, action_type, outcome, outcome_score,
                    lesson_learned, policy_signal, policy_weight,
                    recommended_adjustment, patterns_detected, metadata, ts
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ref.reflection_id,
                    ref.user_id,
                    ref.action_type,
                    ref.outcome,
                    ref.outcome_score,
                    ref.lesson_learned,
                    ref.policy_signal,
                    ref.policy_weight,
                    ref.recommended_adjustment,
                    json_dumps(ref.patterns_detected),
                    json_dumps(ref.metadata),
                    now_ts(),
                ),
            )
        except Exception:
            logger.debug("Failed to persist reflection", exc_info=True)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_recent_reflections(
        self, user_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get recent reflections for diagnostics."""
        rows = fetch_all(
            """
            SELECT id, action_type, outcome, outcome_score,
                   lesson_learned, policy_signal, policy_weight,
                   recommended_adjustment, patterns_detected, ts
            FROM reflections
            WHERE user_id=?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [
            {
                "id": r["id"],
                "action_type": r["action_type"],
                "outcome": r["outcome"],
                "outcome_score": float(r["outcome_score"]),
                "lesson_learned": r["lesson_learned"],
                "policy_signal": r["policy_signal"],
                "policy_weight": float(r["policy_weight"]),
                "recommended_adjustment": r["recommended_adjustment"],
                "patterns": json_loads(r["patterns_detected"]) or [],
                "ts": float(r["ts"]),
            }
            for r in rows
        ]

    def get_lessons_for_action(
        self, user_id: str, action_type: str, limit: int = 10
    ) -> List[str]:
        """Get lessons learned for specific action type."""
        rows = fetch_all(
            """
            SELECT lesson_learned FROM reflections
            WHERE user_id=? AND action_type=?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (user_id, action_type, limit),
        )
        return [r["lesson_learned"] for r in rows]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_reflection_engine = ReflectionEngine()


def reflect_on_action(rinput: ReflectionInput) -> ReflectionOutput:
    """Public API — perform post-action reflection."""
    return _reflection_engine.reflect(rinput)


def get_reflections(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Public API — diagnostics."""
    return _reflection_engine.get_recent_reflections(user_id, limit)


def get_action_lessons(user_id: str, action_type: str, limit: int = 10) -> List[str]:
    """Public API — get lessons for action type."""
    return _reflection_engine.get_lessons_for_action(user_id, action_type, limit)
