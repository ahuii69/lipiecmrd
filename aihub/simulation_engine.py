#!/usr/bin/env python3

"""
SimulationEngine — Silnik symulacji wariantów akcji.

Evaluuje wiele alternatywnych ścieżek decyzyjnych PRZED wykonaniem,
oceniając każdą pod kątem:
  - risk: prawdopodobieństwo porażki (na bazie historii refleksji)
  - confidence: pewność sukcesu (psyche + policy hints)
  - utility: użyteczność wyniku (dopasowanie do celu)
  - cost: koszt zasobowy (tokeny, czas, operacje)

Zwraca ranking wariantów z uzasadnieniem, wybierając najlepszą ścieżkę.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aihub.db import (
    append_event,
    exec_one,
    fetch_all,
    json_dumps,
    json_loads,
    now_ts,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SimulationVariant:
    """One candidate action variant to evaluate."""

    variant_id: str
    action_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass
class VariantScore:
    """Score for a single variant."""

    variant_id: str
    action_type: str
    risk: float  # 0.0 (safe) - 1.0 (risky)
    confidence: float  # 0.0 (no confidence) - 1.0 (very confident)
    utility: float  # 0.0 (useless) - 1.0 (very useful)
    cost: float  # 0.0 (free) - 1.0 (expensive)
    composite_score: float  # final ranking score
    reasoning: str = ""
    breakdown: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationResult:
    """Full simulation output."""

    simulation_id: str
    user_id: str
    variants_evaluated: int
    ranked_variants: list[VariantScore]
    best_variant: VariantScore | None = None
    simulation_time_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class SimulationEngine:
    """
    Silnik symulacji wariantów.

    Proces:
    1. Generuje warianty z aktualnej decyzji + alternatywy
    2. Scoruje każdy wariant na bazie historii (refleksji, doświadczeń)
    3. Rankinguje warianty
    4. Zwraca rekomendację
    """

    # Score weights for composite
    WEIGHT_RISK = 0.25
    WEIGHT_CONFIDENCE = 0.30
    WEIGHT_UTILITY = 0.30
    WEIGHT_COST = 0.15

    # Resource cost estimates per action type
    ACTION_COSTS = {
        "memory_search": 0.1,
        "learn": 0.15,
        "research": 0.6,
        "web_request": 0.7,
        "action": 0.5,
        "reflect": 0.05,
        "skip": 0.0,
        "reason": 0.2,
    }

    # Alternative strategies to consider per action type
    ALTERNATIVES = {
        "memory_search": ["research", "reason"],
        "learn": ["memory_search", "reason"],
        "research": ["memory_search", "reason"],
        "action": ["research", "reason", "memory_search"],
        "reason": ["memory_search", "research"],
        "web_request": ["memory_search", "research"],
    }

    def simulate(
        self,
        user_id: str,
        primary_action: str,
        primary_params: dict[str, Any],
        context: dict[str, Any],
        *,
        max_variants: int = 5,
    ) -> SimulationResult:
        """
        Run simulation for a planned action.

        Evaluates the primary action + alternatives, returns ranked variants.
        """
        start = time.time()
        sim_id = hashlib.sha256(
            f"{user_id}:{primary_action}:{time.time_ns()}".encode()
        ).hexdigest()[:24]

        # 1. Build variant list
        variants = self._build_variants(primary_action, primary_params, max_variants)

        # 2. Load historical data for scoring
        history = self._load_action_history(user_id)
        psyche = context.get("psyche_state", {})
        policy_hints = context.get("policy_hints", [])

        # 3. Score each variant
        scored: list[VariantScore] = []
        for v in variants:
            score = self._score_variant(v, history, psyche, policy_hints, context)
            scored.append(score)

        # 4. Rank by composite score (descending)
        scored.sort(key=lambda s: s.composite_score, reverse=True)

        best = scored[0] if scored else None
        elapsed_ms = (time.time() - start) * 1000

        result = SimulationResult(
            simulation_id=sim_id,
            user_id=user_id,
            variants_evaluated=len(scored),
            ranked_variants=scored,
            best_variant=best,
            simulation_time_ms=round(elapsed_ms, 2),
            metadata={
                "primary_action": primary_action,
                "history_size": len(history),
            },
        )

        # 5. Persist
        self._persist_simulation(result)

        # 6. Event
        append_event(
            user_id,
            "simulation.completed",
            {
                "simulation_id": sim_id,
                "variants": len(scored),
                "best": best.action_type if best else "none",
                "best_score": best.composite_score if best else 0,
                "time_ms": elapsed_ms,
            },
        )

        return result

    # ------------------------------------------------------------------
    # Variant building
    # ------------------------------------------------------------------

    def _build_variants(
        self,
        primary_action: str,
        primary_params: dict[str, Any],
        max_variants: int,
    ) -> list[SimulationVariant]:
        """Build list of variant actions to evaluate."""
        counter = 0
        variants: list[SimulationVariant] = []

        # Primary variant
        variants.append(
            SimulationVariant(
                variant_id=f"v_{counter}",
                action_type=primary_action,
                parameters=dict(primary_params),
                description=f"Primary: {primary_action}",
            )
        )
        counter += 1

        # Alternative variants
        alts = self.ALTERNATIVES.get(primary_action, ["reason"])
        for alt_action in alts:
            if counter >= max_variants:
                break
            variants.append(
                SimulationVariant(
                    variant_id=f"v_{counter}",
                    action_type=alt_action,
                    parameters=self._derive_alt_params(alt_action, primary_params),
                    description=f"Alt: {alt_action} (instead of {primary_action})",
                )
            )
            counter += 1

        # "Skip" variant (do nothing)
        if counter < max_variants:
            variants.append(
                SimulationVariant(
                    variant_id=f"v_{counter}",
                    action_type="skip",
                    parameters={},
                    description="Do nothing (skip)",
                )
            )

        return variants

    def _derive_alt_params(
        self, alt_action: str, primary_params: dict[str, Any]
    ) -> dict[str, Any]:
        """Derive parameters for alternative action from primary params."""
        params: dict[str, Any] = {}

        # Transfer query parameter if present
        if "query" in primary_params:
            params["query"] = primary_params["query"]
        if "message" in primary_params:
            params["message"] = primary_params["message"]
        if "limit" in primary_params:
            params["limit"] = primary_params["limit"]

        return params

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score_variant(
        self,
        variant: SimulationVariant,
        history: dict[str, dict[str, Any]],
        psyche: dict[str, Any],
        policy_hints: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> VariantScore:
        """Score a single variant based on history + psyche + policy."""
        action = variant.action_type
        action_hist = history.get(action, {})

        risk = self._compute_risk(action, action_hist)
        confidence = self._compute_confidence(action, action_hist, psyche, policy_hints)
        utility = self._compute_utility(action, context)
        cost = self._compute_cost(action)

        # Composite: maximize (confidence + utility) - (risk + cost)
        composite = (
            self.WEIGHT_CONFIDENCE * confidence
            + self.WEIGHT_UTILITY * utility
            - self.WEIGHT_RISK * risk
            - self.WEIGHT_COST * cost
        )
        composite = max(0.0, min(1.0, composite))

        reasoning = self._build_reasoning(
            action, risk, confidence, utility, cost, composite, action_hist
        )

        return VariantScore(
            variant_id=variant.variant_id,
            action_type=action,
            risk=round(risk, 3),
            confidence=round(confidence, 3),
            utility=round(utility, 3),
            cost=round(cost, 3),
            composite_score=round(composite, 3),
            reasoning=reasoning,
            breakdown={
                "risk_weight": self.WEIGHT_RISK,
                "confidence_weight": self.WEIGHT_CONFIDENCE,
                "utility_weight": self.WEIGHT_UTILITY,
                "cost_weight": self.WEIGHT_COST,
                "history_samples": action_hist.get("total", 0),
            },
        )

    def _compute_risk(self, action: str, action_hist: dict[str, Any]) -> float:
        """
        Compute risk = probability of failure.

        Based on historical failure rate with smoothing.
        """
        total = action_hist.get("total", 0)
        failures = action_hist.get("failures", 0)

        if total == 0:
            # Unknown action: moderate risk
            return 0.4

        # Laplace smoothing: (failures + 1) / (total + 2)
        risk = (failures + 1) / (total + 2)

        # Boost risk if recent failures
        recent_fail_rate = action_hist.get("recent_fail_rate", 0)
        if recent_fail_rate > 0.5:
            risk = min(1.0, risk + 0.15)

        return min(1.0, risk)

    def _compute_confidence(
        self,
        action: str,
        action_hist: dict[str, Any],
        psyche: dict[str, Any],
        policy_hints: list[dict[str, Any]],
    ) -> float:
        """
        Compute confidence based on history + psyche + policy.
        """
        base = 0.5

        # Historical success rate
        total = action_hist.get("total", 0)
        successes = action_hist.get("successes", 0)
        if total > 0:
            base = successes / total

        # Psyche modulation
        energy = float(psyche.get("energy", 0.7))
        focus = float(psyche.get("focus", 0.65))
        base += (energy - 0.5) * 0.1 + (focus - 0.5) * 0.1

        # Policy hint modulation
        for hint in policy_hints:
            if hint.get("action_type") == action:
                signal = hint.get("signal", "neutral")
                weight = float(hint.get("weight", 0.3))
                if signal == "boost":
                    base += weight * 0.15
                elif signal == "penalize":
                    base -= weight * 0.20
                elif signal == "avoid":
                    base -= weight * 0.40
                elif signal == "caution":
                    base -= weight * 0.10

        return max(0.05, min(1.0, base))

    def _compute_utility(self, action: str, context: dict[str, Any]) -> float:
        """
        Estimate utility of action for current goal.

        Heuristic based on intent matching + context signals.
        """
        intent = context.get("intent", "query")
        urgency = float(context.get("urgency_score", 0.5))
        relevance = float(context.get("relevance_score", 0.5))

        # Base utility from intent matching
        intent_match = {
            "query": {"memory_search": 0.9, "research": 0.6, "reason": 0.5},
            "learn": {"learn": 0.9, "memory_search": 0.5, "reason": 0.4},
            "research": {
                "research": 0.9,
                "memory_search": 0.4,
                "web_request": 0.8,
            },
            "action": {"action": 0.9, "research": 0.4, "reason": 0.5},
        }

        base = intent_match.get(intent, {}).get(action, 0.3)

        # Skip has very low utility (unless urgency is also low)
        if action == "skip":
            base = max(0.05, 0.2 - urgency * 0.15)

        # Urgency modulates: high urgency → prefer direct action
        if (
            urgency > 0.7
            and action in ("research", "memory_search")
            or urgency < 0.3
            and action == "skip"
        ):
            base += 0.1

        # Relevance modulates
        base += relevance * 0.1

        return max(0.0, min(1.0, base))

    def _compute_cost(self, action: str) -> float:
        """Estimate resource cost of action."""
        return self.ACTION_COSTS.get(action, 0.3)

    def _build_reasoning(
        self,
        action: str,
        risk: float,
        confidence: float,
        utility: float,
        cost: float,
        composite: float,
        action_hist: dict[str, Any],
    ) -> str:
        """Build reasoning string for variant score."""
        total = action_hist.get("total", 0)
        parts = [f"'{action}' score={composite:.3f}"]

        if total > 0:
            succ = action_hist.get("successes", 0)
            parts.append(f"history={succ}/{total}")
        else:
            parts.append("no history")

        parts.append(f"risk={risk:.2f}")
        parts.append(f"conf={confidence:.2f}")
        parts.append(f"util={utility:.2f}")
        parts.append(f"cost={cost:.2f}")

        return " | ".join(parts)

    # ------------------------------------------------------------------
    # Historical data loading
    # ------------------------------------------------------------------

    def _load_action_history(
        self, user_id: str, window: int = 50
    ) -> dict[str, dict[str, Any]]:
        """
        Load action history from reflections.

        Returns: {action_type: {total, successes, failures, avg_score, recent_fail_rate}}
        """
        result: dict[str, dict[str, Any]] = {}

        try:
            rows = fetch_all(
                """
                SELECT action_type, outcome, outcome_score, ts
                FROM reflections
                WHERE user_id=?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (user_id, window),
            )

            if not rows:
                return result

            # Aggregate per action type
            from collections import defaultdict

            by_action: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in rows:
                by_action[r["action_type"]].append({
                    "outcome": r["outcome"],
                    "score": float(r["outcome_score"]),
                })

            for action, refs in by_action.items():
                total = len(refs)
                successes = sum(1 for r in refs if r["outcome"] == "success")
                failures = sum(1 for r in refs if r["outcome"] == "failure")
                avg_score = sum(r["score"] for r in refs) / total if total else 0

                # Recent fail rate (last 5)
                recent = refs[:5]
                recent_fails = sum(1 for r in recent if r["outcome"] == "failure")
                recent_fail_rate = recent_fails / len(recent) if recent else 0

                result[action] = {
                    "total": total,
                    "successes": successes,
                    "failures": failures,
                    "avg_score": round(avg_score, 3),
                    "recent_fail_rate": round(recent_fail_rate, 3),
                }

        except Exception:
            logger.debug("Failed to load action history", exc_info=True)

        return result

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_simulation(self, result: SimulationResult) -> None:
        """Persist simulation to DB."""
        try:
            ranked_data = [
                {
                    "variant_id": v.variant_id,
                    "action_type": v.action_type,
                    "risk": v.risk,
                    "confidence": v.confidence,
                    "utility": v.utility,
                    "cost": v.cost,
                    "composite": v.composite_score,
                    "reasoning": v.reasoning,
                }
                for v in result.ranked_variants
            ]
            exec_one(
                """
                INSERT INTO simulations(
                    id, user_id, variants_evaluated,
                    best_action, best_score,
                    ranked_data, simulation_time_ms, metadata, ts
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    result.simulation_id,
                    result.user_id,
                    result.variants_evaluated,
                    result.best_variant.action_type if result.best_variant else "",
                    result.best_variant.composite_score if result.best_variant else 0.0,
                    json_dumps(ranked_data),
                    result.simulation_time_ms,
                    json_dumps(result.metadata),
                    now_ts(),
                ),
            )
        except Exception:
            logger.debug("Failed to persist simulation", exc_info=True)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_recent_simulations(
        self, user_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get recent simulations for cockpit."""
        rows = fetch_all(
            """
            SELECT id, variants_evaluated, best_action, best_score,
                   ranked_data, simulation_time_ms, ts
            FROM simulations
            WHERE user_id=?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [
            {
                "id": r["id"],
                "variants_evaluated": int(r["variants_evaluated"]),
                "best_action": r["best_action"],
                "best_score": float(r["best_score"]),
                "ranked_data": json_loads(r["ranked_data"]) or [],
                "simulation_time_ms": float(r["simulation_time_ms"]),
                "ts": float(r["ts"]),
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_simulation_engine = SimulationEngine()


def simulate_action(
    user_id: str,
    primary_action: str,
    primary_params: dict[str, Any],
    context: dict[str, Any],
    *,
    max_variants: int = 5,
) -> SimulationResult:
    """Public API — run simulation."""
    return _simulation_engine.simulate(
        user_id, primary_action, primary_params, context, max_variants=max_variants
    )


def get_simulations(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Public API — diagnostics."""
    return _simulation_engine.get_recent_simulations(user_id, limit)
