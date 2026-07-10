#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Cognitive Controller - Centralny system decyzyjny agenta.

Nie jest ścieżką ``POST /chat/turn`` — turę czatu obsługuje ``ChatRuntime._run_turn_core``.
Ten moduł służy cyklom agenta / tłu (np. ``agent_loop``), żeby uniknąć mylenia dwóch routerów.

Odpowiada za:
- Wybór narzędzi (tools/actions)
- Zarządzanie kontekstem
- Decyzję czy research
- Decyzję czy learning
- Priorytetyzacja zadań
- Zasoby i ograniczenia
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from aihub.attention_controller import AttentionController
from aihub.conflict_detector import ConflictCheck, ConflictDetector
from aihub.db import append_event, now_ts
from aihub.knowledge_graph import KnowledgeGraph
from aihub.meta_memory import check_stale
from aihub.metrics_engine import record_latency
from aihub.prediction_engine import predict_next_action
from aihub.psyche_core import get_psyche_core

logger = logging.getLogger(__name__)


@dataclass
class CognitiveState:
    """Stan poznawczy agenta."""

    user_id: str
    decision_context: Dict[str, Any] = field(default_factory=dict)
    current_focus: Optional[str] = None
    active_goals: List[str] = field(default_factory=list)
    resource_usage: Dict[str, float] = field(default_factory=dict)
    recent_decisions: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=now_ts)


@dataclass
class DecisionRequest:
    """Request for cognitive decision."""

    user_id: str
    message: str
    context: Dict[str, Any]
    available_tools: List[str]
    constraints: Optional[Dict[str, Any]] = None


@dataclass
class DecisionResult:
    """Result of cognitive decision."""

    action_type: str  # "web_fetch", "memory_add", "learn", "research", "reflect"
    parameters: Dict[str, Any]
    reasoning: str
    confidence: float  # 0.0-1.0
    skip_reason: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)


class CognitiveController:
    """
    Centralny kontroler poznawczy.

    Integruje wszystkie systemy i podejmuje decyzje.
    """

    def __init__(self):
        self.attention = AttentionController()
        self.conflict_detector = ConflictDetector()
        self.knowledge_graph = KnowledgeGraph()
        self.states: Dict[str, CognitiveState] = {}

        # Konfiguracja limitów zasobów
        self.resource_limits = {
            "max_web_requests": 3,
            "max_memory_operations": 5,
            "max_learning_samples": 10,
            "context_token_budget": 4096,
        }

        # Konfiguracja postaw
        self.decision_thresholds = {
            "research_threshold": 0.7,  # When to trigger research
            "learning_threshold": 0.6,  # When to trigger learning
            "conflict_threshold": 0.8,  # When conflict is serious
            "urgency_threshold": 0.7,  # When to skip some steps
        }

    # Resource limit TTL — reset counters after this many seconds
    RESOURCE_LIMIT_TTL = 300  # 5 minutes

    def _get_state(self, user_id: str) -> CognitiveState:
        """Get or create cognitive state for user."""
        if user_id not in self.states:
            self.states[user_id] = CognitiveState(user_id=user_id)
        return self.states[user_id]

    def _check_resources(
        self, user_id: str, action_type: str, resource_cost: int = 1
    ) -> tuple[bool, str]:
        """Check if we have resources for action. Resets counters after TTL."""
        state = self._get_state(user_id)

        # Auto-reset resource counters if state is older than TTL
        age = now_ts() - state.timestamp
        if age > self.RESOURCE_LIMIT_TTL:
            state.resource_usage = {}
            state.timestamp = now_ts()
            logger.debug(
                "Resource counters reset for %s (TTL expired after %.0fs)", user_id, age
            )

        key = f"action_{action_type}"

        current = state.resource_usage.get(key, 0)
        limit = self.resource_limits.get(f"max_{action_type}s", 5)

        if current >= limit:
            reason = f"Resource limit for {action_type}: {current}/{limit}"
            return False, reason

        state.resource_usage[key] = current + resource_cost
        return True, ""

    def _detect_conflicts(
        self, user_id: str, proposed_actions: List[Dict[str, Any]]
    ) -> Optional[ConflictCheck]:
        """Detect conflicts between proposed actions."""
        try:
            conflict = self.conflict_detector.check_conflict(user_id, proposed_actions)
            if conflict.has_conflict:
                logger.warning(
                    "Conflict detected for %s: %s", user_id, conflict.conflict_type
                )
            return conflict
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error in conflict detection: %s", e)
            return None

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        """
        Make cognitive decision about what to do.

        Decision pipeline:
        1. Rank messages by attention
        2. Build context
        3. Check conflicts
        4. Select action
        5. Validate resources
        6. Generate decision

        Args:
            request: DecisionRequest with message and context

        Returns:
            DecisionResult with chosen action
        """
        try:
            get_psyche_core().ensure_user(request.user_id)
            state = self._get_state(request.user_id)

            logger.info(
                "Cognitive decision for %s: %s", request.user_id, request.message[:50]
            )

            # 1. Get psyche state
            psyche = get_psyche_core().ensure_user(request.user_id)

            # 2. Build decision context
            decision_context = {
                "message": request.message,
                "psyche": psyche,
                "message_count": len(request.context.get("messages", [])),
                "memory_pressure": await self._estimate_memory_pressure(
                    request.user_id
                ),
                "urgency": psyche.get("energy", 0.5),  # Energy level as urgency
            }

            # 3. Extract intent from message
            intent = self._extract_intent(request.message)
            decision_context["intent"] = intent

            # 3b. Merge request context (psyche_state, urgency_score, relevance_score)
            decision_context.update(request.context)

            # 3c. Run predictions
            predictions = predict_next_action(request.user_id, decision_context)
            if predictions:
                decision_context["predictions"] = [
                    {"type": p.prediction_type, "confidence": p.confidence}
                    for p in predictions
                ]

            # 4. Decide on action
            if intent == "query":
                action = await self._decide_query(
                    request.user_id, request, decision_context
                )
            elif intent == "learn":
                action = await self._decide_learn(
                    request.user_id, request, decision_context
                )
            elif intent == "research":
                action = await self._decide_research(
                    request.user_id, request, decision_context
                )
            elif intent == "action":
                action = await self._decide_action(
                    request.user_id, request, decision_context
                )
            else:
                action = DecisionResult(
                    action_type="reflect",
                    parameters={},
                    reasoning="Unknown intent - reflect first",
                    confidence=0.5,
                )

            # 5. Check conflicts
            conflicts = self._detect_conflicts(
                request.user_id,
                [{"type": action.action_type, "parameters": action.parameters}],
            )
            if conflicts and conflicts.has_conflict:
                if conflicts.severity >= self.decision_thresholds["conflict_threshold"]:
                    action.skip_reason = f"Conflict: {conflicts.conflict_description}"
                    action.action_type = "skip"
                    logger.warning(
                        "Skipping action due to conflict: %s",
                        conflicts.conflict_description,
                    )

            # 6. Log decision
            state.recent_decisions.append(
                {
                    "message": request.message[:100],
                    "action": action.action_type,
                    "confidence": action.confidence,
                    "ts": now_ts(),
                }
            )

            append_event(
                request.user_id,
                "cognitive.decision",
                {
                    "message": request.message[:100],
                    "action": action.action_type,
                    "confidence": action.confidence,
                    "intent": intent,
                },
            )

            logger.info(
                "Decision: %s (confidence: %s)", action.action_type, action.confidence
            )
            return action

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error in cognitive decision: %s", e, exc_info=True)
            append_event(request.user_id, "cognitive.error", {"error": str(e)})
            return DecisionResult(
                action_type="skip",
                parameters={},
                reasoning=f"Error in cognitive processing: {str(e)}",
                confidence=0.0,
                skip_reason=str(e),
            )

    def _extract_intent(self, message: str) -> str:
        """Extract user intent from message."""
        message_lower = message.lower()

        # Research signals
        if any(w in message_lower for w in ["sprawdź", "wyszukaj", "research", "find"]):
            return "research"

        # Action signals
        if any(w in message_lower for w in ["stwórz", "napisz", "execute", "make"]):
            return "action"

        # Learning signals
        if any(w in message_lower for w in ["nauczę", "learn", "teach", "explain"]):
            return "learn"

        # Default: query
        return "query"

    async def _decide_query(
        self,
        user_id: str,
        request: DecisionRequest,
        context: Dict[str, Any],
    ) -> DecisionResult:
        """Decide on query handling."""
        has_resources, reason = self._check_resources(user_id, "memory_operation")
        if not has_resources:
            logger.warning("Resource limit hit for query: %s", reason)
            record_latency("resource_limit.query", 0, success=False)
            return DecisionResult(
                action_type="skip",
                parameters={},
                reasoning=reason,
                confidence=0.8,
                skip_reason=reason,
            )

        # Use context to adjust confidence and limit
        energy = context.get("urgency", 0.5)
        relevance = (
            context.get("relevance_score", 0.5) if "relevance_score" in context else 0.5
        )
        focus = (
            context.get("psyche_state", {}).get("focus", 0.5)
            if isinstance(context.get("psyche_state"), dict)
            else 0.5
        )
        adjusted_confidence = min(1.0, 0.7 + (relevance * 0.2) + (focus * 0.1))
        limit = 10 if energy < 0.3 else 20

        return DecisionResult(
            action_type="memory_search",
            parameters={
                "query": request.message,
                "limit": limit,
            },
            reasoning=f"Query with energy={energy:.2f}, relevance={relevance:.2f}, focus={focus:.2f}",
            confidence=adjusted_confidence,
        )

    async def _decide_learn(
        self,
        user_id: str,
        request: DecisionRequest,
        context: Dict[str, Any],
    ) -> DecisionResult:
        """Decide on learning."""
        has_resources, reason = self._check_resources(user_id, "learning_sample")

        if not has_resources:
            logger.warning("Resource limit hit for learning: %s", reason)
            record_latency("resource_limit.learn", 0, success=False)
            return DecisionResult(
                action_type="skip",
                parameters={},
                reasoning=reason,
                confidence=0.8,
                skip_reason=reason,
            )

        # Use context to modulate learning
        energy = context.get("urgency", 0.5)
        focus = (
            context.get("psyche_state", {}).get("focus", 0.5)
            if isinstance(context.get("psyche_state"), dict)
            else 0.5
        )
        adjusted_confidence = min(1.0, 0.65 + (energy * 0.1) + (focus * 0.15))

        return DecisionResult(
            action_type="learn",
            parameters={
                "message": request.message,
            },
            reasoning=f"Learning with energy={energy:.2f}, focus={focus:.2f}",
            confidence=adjusted_confidence,
        )

    async def _decide_research(
        self,
        user_id: str,
        request: DecisionRequest,
        context: Dict[str, Any],
    ) -> DecisionResult:
        """Decide on research."""
        has_resources, reason = self._check_resources(user_id, "web_request")

        if not has_resources:
            logger.warning("Resource limit hit for research: %s", reason)
            record_latency("resource_limit.research", 0, success=False)
            return DecisionResult(
                action_type="skip",
                parameters={},
                reasoning=reason,
                confidence=0.8,
                skip_reason=reason,
            )

        # Use context for research depth
        urgency = (
            context.get("urgency_score", 0.5) if "urgency_score" in context else 0.5
        )
        memory_pressure = context.get("memory_pressure", 0.0)
        research_type = "deep" if urgency > 0.7 and memory_pressure < 0.5 else "web"
        adjusted_confidence = min(1.0, 0.7 + (urgency * 0.2))

        return DecisionResult(
            action_type="research",
            parameters={
                "query": request.message,
                "research_type": research_type,
            },
            reasoning=f"Research ({research_type}) with urgency={urgency:.2f}, mem_pressure={memory_pressure:.2f}",
            confidence=adjusted_confidence,
        )

    async def _decide_action(
        self,
        user_id: str,
        request: DecisionRequest,
        context: Dict[str, Any],
    ) -> DecisionResult:
        """Decide on action execution."""
        has_resources, reason = self._check_resources(user_id, "web_request")
        if not has_resources:
            logger.warning("Resource limit hit for action: %s", reason)
            record_latency("resource_limit.action", 0, success=False)
            return DecisionResult(
                action_type="skip",
                parameters={},
                reasoning=reason,
                confidence=0.8,
                skip_reason=reason,
            )

        # Use context to adjust confidence
        urgency = (
            context.get("urgency_score", 0.5) if "urgency_score" in context else 0.5
        )
        focus = (
            context.get("psyche_state", {}).get("focus", 0.5)
            if isinstance(context.get("psyche_state"), dict)
            else 0.5
        )
        adjusted_confidence = min(1.0, 0.55 + (urgency * 0.2) + (focus * 0.15))

        return DecisionResult(
            action_type="execute",
            parameters={
                "instruction": request.message,
            },
            reasoning=f"Action with urgency={urgency:.2f}, focus={focus:.2f}",
            confidence=adjusted_confidence,
        )

    async def _estimate_memory_pressure(self, user_id: str) -> float:
        """Estimate memory pressure (0.0-1.0)."""
        try:
            # Check how much memory is being used
            stale = check_stale(user_id, days_threshold=30)

            # Estimate based on stale facts
            pressure = len(stale) / 500.0 if stale else 0.0

            return min(1.0, pressure)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("Error estimating memory pressure: %s", e)
            return 0.5

    def reset_state(self, user_id: str) -> None:
        """Reset cognitive state for user."""
        if user_id in self.states:
            self.states[user_id] = CognitiveState(user_id=user_id)
            logger.info("Reset cognitive state for %s", user_id)


# Singleton
_controller = CognitiveController()


def get_cognitive_controller() -> CognitiveController:
    """Zwraca współdzieloną instancję CognitiveController."""
    return _controller


async def decide(request: DecisionRequest) -> DecisionResult:
    """Public API."""
    return await _controller.decide(request)


def reset_state(user_id: str) -> None:
    """Public API."""
    return _controller.reset_state(user_id)
