#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Attention Controller - System rankingowania i focused na istotne informacje.

Odpowiada za:
- Ranking wiadomości po ważności
- Filtrowanie szumu
- Zarządzanie focus
- Selekcja danych do kontekstu
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

from aihub.psyche_core import get_psyche_core

logger = logging.getLogger(__name__)


@dataclass
class AttentionRanking:
    """Ranking wiadomości pod względem uwagi."""

    message: Dict[str, Any]
    score: float  # 0.0-1.0
    category: str  # "urgent", "relevant", "learning", "noise"
    urgency: float  # kto szybko
    relevance: float  # jak istotne
    reasoning: str = ""


class AttentionController:
    """
    System kontroli uwagi.

    Rankuje wiadomości i informacje po ważności dla agenta.
    Filtruje szum i skupia się na istotnym.
    """

    def __init__(self):
        self.urgency_keywords = {
            "urgent": ["pilnie", "natychmiast", "emergency", "sos", "help"],
            "important": ["ważne", "important", "critical", "key", "must"],
            "routine": ["normalnie", "można", "optional", "maybe", "could"],
        }

        self.relevance_patterns = {
            "self_reference": ["ja", "mi", "mam", "robiłem"],
            "query": ["co", "czy", "jak", "gdzie", "kto", "pytanie"],
            "action": ["zrób", "napisz", "stwórz", "wyślij", "usuń"],
        }

    def rank_messages(
        self, user_id: str, messages: List[Dict[str, Any]]
    ) -> List[AttentionRanking]:
        """
        Rank messages by importance.

        Args:
            user_id: User ID
            messages: List of messages to rank

        Returns:
            Sorted list of AttentionRanking objects
        """
        try:
            get_psyche_core().ensure_user(user_id)

            rankings: List[AttentionRanking] = []

            for msg in messages:
                content = msg.get("content", "").lower()

                # Calculate components
                urgency = self._calculate_urgency(content)
                relevance = self._calculate_relevance(content, user_id)
                category = self._categorize(content, urgency, relevance)

                # Combined score
                score = (urgency * 0.4) + (relevance * 0.6)

                ranking = AttentionRanking(
                    message=msg,
                    score=score,
                    category=category,
                    urgency=urgency,
                    relevance=relevance,
                    reasoning=f"Urgency: {urgency:.2f}, Relevance: {relevance:.2f}",
                )
                rankings.append(ranking)

            # Sort by score descending
            rankings.sort(key=lambda r: r.score, reverse=True)

            logger.debug(f"Ranked {len(rankings)} messages for {user_id}")
            return rankings

        except Exception as e:
            logger.error(f"Error ranking messages: {e}", exc_info=True)
            # Fallback: equal ranking for all
            return [
                AttentionRanking(
                    message=m,
                    score=0.5,
                    category="unknown",
                    urgency=0.5,
                    relevance=0.5,
                )
                for m in messages
            ]

    def _calculate_urgency(self, content: str) -> float:
        """Calculate urgency level (0.0-1.0)."""
        urgency = 0.3  # Default baseline

        for keyword in self.urgency_keywords.get("urgent", []):
            if keyword in content:
                return 0.95

        for keyword in self.urgency_keywords.get("important", []):
            if keyword in content:
                urgency = max(urgency, 0.7)

        for keyword in self.urgency_keywords.get("routine", []):
            if keyword in content:
                urgency = max(urgency, 0.3)

        return min(1.0, urgency)

    def _calculate_relevance(self, content: str, user_id: str) -> float:
        """Calculate relevance to user context."""
        relevance = 0.5  # Default baseline

        # Self-references are more relevant
        if any(w in content for w in self.relevance_patterns["self_reference"]):
            relevance = max(relevance, 0.75)

        # Queries need context
        if any(w in content for w in self.relevance_patterns["query"]):
            relevance = max(relevance, 0.7)

        # Actions are very relevant
        if any(w in content for w in self.relevance_patterns["action"]):
            relevance = max(relevance, 0.85)

        # Length heuristic - too long or too short is less relevant
        if len(content) < 5 or len(content) > 5000:
            relevance *= 0.8

        return min(1.0, relevance)

    def _categorize(self, content: str, urgency: float, relevance: float) -> str:
        """Categorize message."""
        if urgency > 0.8:
            return "urgent"
        elif relevance > 0.75:
            return "relevant"
        elif urgency > 0.5 or relevance > 0.5:
            return "learning"
        else:
            return "noise"

    def focus_on(self, user_id: str, category: str) -> tuple[List[Dict[str, Any]], str]:
        """
        Get focused set of messages for category.

        Args:
            user_id: User ID
            category: Focus category (urgent, relevant, learning, noise)

        Returns:
            Tuple of (filtered messages, focus description)
        """
        try:
            from aihub.db import get_stm

            valid_categories = {"urgent", "relevant", "learning", "noise"}
            requested = (category or "relevant").strip().lower()
            if requested not in valid_categories:
                requested = "relevant"

            raw_messages = get_stm(user_id, 200)
            normalized: list[dict[str, Any]] = []
            for row in raw_messages:
                if isinstance(row, dict):
                    content = str(row.get("content") or row.get("text") or row.get("message") or "")
                    normalized.append({**row, "content": content})
                else:
                    normalized.append({"content": str(row)})

            ranked = self.rank_messages(user_id, normalized)
            selected = [r for r in ranked if r.category == requested]
            if not selected and requested != "noise":
                selected = [r for r in ranked if r.category in {"urgent", "relevant", "learning"}]
            if not selected:
                selected = ranked

            messages: list[dict[str, Any]] = []
            for r in selected[:25]:
                item = dict(r.message)
                item["attention_score"] = r.score
                item["attention_category"] = r.category
                item["attention_reasoning"] = r.reasoning
                messages.append(item)

            focus_desc = (
                f"Focusing on {requested}: selected {len(messages)} of {len(normalized)} STM messages"
            )
            logger.debug("Focus for %s: %s", user_id, focus_desc)
            return messages, focus_desc
        except Exception as e:
            logger.error(f"Error in focus_on: {e}", exc_info=True)
            return [], "Error in focusing"


# Singleton
_attention = AttentionController()


def rank_messages(
    user_id: str, messages: List[Dict[str, Any]]
) -> List[AttentionRanking]:
    """Public API."""
    return _attention.rank_messages(user_id, messages)


def focus_on(user_id: str, category: str) -> tuple[List[Dict[str, Any]], str]:
    """Public API."""
    return _attention.focus_on(user_id, category)
