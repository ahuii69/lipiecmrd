#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Prediction Engine - System do predykcji i antycypacji.

Odpowiada za:
- Predykcję kontekstu
- Anticipation przyszłych akcji
- Pattern recognition
- User behavior modeling
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from aihub.db import append_event, now_ts

logger = logging.getLogger(__name__)


@dataclass
class Prediction:
    """Pojedyncza predykcja."""

    prediction_type: str
    content: str
    confidence: float
    reasoning: str
    timestamp: Optional[float] = field(default_factory=now_ts)


class PredictionEngine:
    """Engine do predykcji i antycypacji."""

    def __init__(self):
        self.predictions_cache: Dict[str, List[Prediction]] = {}

    def predict_next_action(
        self, user_id: str, context: Dict[str, Any]
    ) -> List[Prediction]:
        """Predict what user will do next based on actual context keys."""
        try:
            predictions = []

            # Extract actual context fields from pipeline
            psyche_state = context.get("psyche_state", {})
            if not isinstance(psyche_state, dict):
                psyche_state = {}
            urgency = context.get("urgency_score", 0.0)
            relevance = context.get("relevance_score", 0.0)
            intent = context.get("intent", "")
            memory_pressure = context.get("memory_pressure", 0.0)

            # Pattern 1: High focus + high relevance → user likely continues current task
            focus = psyche_state.get("focus", 0.5)
            if focus > 0.6 and relevance > 0.5:
                predictions.append(
                    Prediction(
                        prediction_type="continue_task",
                        content="User likely to continue current task",
                        confidence=min(1.0, 0.5 + focus * 0.3 + relevance * 0.2),
                        reasoning=f"High focus ({focus:.2f}) + relevance ({relevance:.2f})",
                    )
                )

            # Pattern 2: High urgency → user needs quick response
            if urgency > 0.7:
                predictions.append(
                    Prediction(
                        prediction_type="urgent_response",
                        content="User needs quick, direct answer",
                        confidence=min(1.0, 0.5 + urgency * 0.4),
                        reasoning=f"High urgency score ({urgency:.2f})",
                    )
                )

            # Pattern 3: Low energy → user may disengage
            energy = psyche_state.get("energy", 0.5)
            if energy < 0.3:
                predictions.append(
                    Prediction(
                        prediction_type="disengage_risk",
                        content="User energy low - keep responses concise",
                        confidence=min(1.0, 0.5 + (1.0 - energy) * 0.3),
                        reasoning=f"Low energy ({energy:.2f}) indicates fatigue",
                    )
                )

            # Pattern 4: High memory pressure → may need cleanup
            if memory_pressure > 0.7:
                predictions.append(
                    Prediction(
                        prediction_type="memory_cleanup",
                        content="Memory pressure high - consider GC",
                        confidence=min(1.0, 0.4 + memory_pressure * 0.4),
                        reasoning=f"Memory pressure at {memory_pressure:.2f}",
                    )
                )

            # Pattern 5: Intent-based prediction
            if intent == "research":
                predictions.append(
                    Prediction(
                        prediction_type="research_followup",
                        content="User may ask follow-up research questions",
                        confidence=0.65,
                        reasoning="Intent is research, likely follow-up",
                    )
                )

            self.predictions_cache[user_id] = predictions

            if not predictions:
                logger.info(
                    "No predictions for %s: insufficient context signals", user_id
                )
                append_event(
                    user_id,
                    "prediction.no_prediction",
                    {"reason": "no matching patterns in context"},
                )
                return []

            logger.info("Generated %d predictions for %s", len(predictions), user_id)
            append_event(
                user_id,
                "prediction.generated",
                {
                    "count": len(predictions),
                    "avg_confidence": sum(p.confidence for p in predictions)
                    / len(predictions),
                },
            )

            return predictions

        except Exception as e:
            logger.error("Error predicting next action: %s", e)
            append_event(user_id, "prediction.error", {"error": str(e)})
            return []

    def predict_context_needs(self, user_id: str, message: str) -> Dict[str, Any]:
        """Predict what context user will need."""
        try:
            needs = {
                "likely_topics": [],
                "likely_tools": [],
                "confidence": 0.0,
            }

            message_lower = message.lower()

            # Detect topics
            if any(w in message_lower for w in ["python", "code", "script"]):
                needs["likely_topics"].append("programming")
                needs["likely_tools"].append("code_executor")

            if any(w in message_lower for w in ["research", "paper", "study"]):
                needs["likely_topics"].append("research")
                needs["likely_tools"].append("web_search")

            if any(w in message_lower for w in ["memory", "remember", "recall"]):
                needs["likely_topics"].append("memory")
                needs["likely_tools"].append("memory_retrieval")

            if needs["likely_topics"]:
                needs["confidence"] = 0.65
                logger.debug(f"Predicted context needs: {needs}")

            return needs

        except Exception as e:
            logger.error(f"Error predicting context: {e}")
            return {}

    def predict_conflicts(self, user_id: str, action_sequence: List[str]) -> List[str]:
        """Predict potential conflicts in action sequence."""
        try:
            conflicts = []

            destructive_actions = {"delete", "drop", "remove", "destroy"}
            has_destructive = any(a in action_sequence for a in destructive_actions)

            if has_destructive:
                conflicts.append("Destructive action detected - recommend caution")

            if "create" in action_sequence and "delete" in action_sequence:
                idx_create = action_sequence.index("create")
                idx_delete = action_sequence.index("delete")
                if idx_delete > idx_create:
                    conflicts.append("Delete follows create - unusual pattern")

            if conflicts:
                append_event(
                    user_id,
                    "prediction.conflicts_detected",
                    {
                        "count": len(conflicts),
                        "conflicts": conflicts,
                    },
                )

            return conflicts

        except Exception as e:
            logger.error(f"Error predicting conflicts: {e}")
            return []

    def get_predictions(self, user_id: str) -> List[Prediction]:
        """Get cached predictions for user."""
        return self.predictions_cache.get(user_id, [])


# Singleton
_predictor = PredictionEngine()


def predict_next_action(user_id: str, context: Dict[str, Any]) -> List[Prediction]:
    """Public API."""
    return _predictor.predict_next_action(user_id, context)


def predict_context_needs(user_id: str, message: str) -> Dict[str, Any]:
    """Public API."""
    return _predictor.predict_context_needs(user_id, message)


def predict_conflicts(user_id: str, action_sequence: List[str]) -> List[str]:
    """Public API."""
    return _predictor.predict_conflicts(user_id, action_sequence)
