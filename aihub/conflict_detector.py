#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Conflict Detector - System detekcji sprzeczności i bezpieczeństwa.

Odpowiada za:
- Detektowanie sprzecznych akcji
- Weryfikacja bezpieczeństwa
- Wykrywanie anomalii
- Walidacja decyzji
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from aihub.knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)


@dataclass
class ConflictCheck:
    """Wynik sprawdzenia sprzeczności."""

    has_conflict: bool
    conflict_type: str  # "contradictory_action", "resource_limit", "policy_violation", "safety_risk"
    conflict_description: str
    severity: float  # 0.0-1.0
    resolution: Optional[str] = None


class ConflictDetector:
    """
    Detektor sprzeczności i walidator bezpieczeństwa.

    Sprawdza czy proponowane akcje są:
    - Logicznie spójne
    - Bezpieczne
    - Zgodne z polityką
    - Realizowalne z ograniczeniami zasobów
    """

    def __init__(self):
        self.knowledge_graph = KnowledgeGraph()

        # Security policies
        self.forbidden_actions = [
            "delete_all_memory",
            "factory_reset",
            "system_shutdown",
        ]

        # Resource conflicts
        self.resource_pools = {
            "web_requests": {"limit": 100, "per_hour": True},
            "memory_writes": {"limit": 1000, "per_hour": False},
            "computations": {"limit": 50, "per_hour": True},
        }

    def check_conflict(
        self, user_id: str, actions: List[Dict[str, Any]]
    ) -> ConflictCheck:
        """
        Check if actions conflict.

        Args:
            user_id: User ID
            actions: List of proposed actions

        Returns:
            ConflictCheck with conflict details
        """
        try:
            # Validate action format at boundary
            for i, action in enumerate(actions):
                if not isinstance(action, dict) or "type" not in action:
                    logger.error(
                        "Invalid action format at index %d: expected dict with 'type' key, got %s",
                        i,
                        type(action).__name__,
                    )
                    return ConflictCheck(
                        has_conflict=True,
                        conflict_type="validation_error",
                        conflict_description=f"Invalid action at index {i}: missing 'type' field",
                        severity=0.9,
                    )

            logger.debug("Checking conflicts for %d actions", len(actions))

            # 1. Check security
            sec_check = self._check_security(actions)
            if sec_check.has_conflict:
                return sec_check

            # 2. Check logical consistency
            logic_check = self._check_logical_consistency(user_id, actions)
            if logic_check.has_conflict:
                return logic_check

            # 3. Check resource constraints
            resource_check = self._check_resource_constraints(actions)
            if resource_check.has_conflict:
                return resource_check

            # No conflicts
            return ConflictCheck(
                has_conflict=False,
                conflict_type="none",
                conflict_description="No conflicts detected",
                severity=0.0,
            )

        except Exception as e:
            logger.error(f"Error in conflict check: {e}", exc_info=True)
            return ConflictCheck(
                has_conflict=True,
                conflict_type="error",
                conflict_description=f"Error in conflict detection: {str(e)}",
                severity=0.9,
            )

    def _check_security(self, actions: List[Dict[str, Any]]) -> ConflictCheck:
        """Check security constraints."""
        for action in actions:
            action_type = action.get("type", "")

            if action_type in self.forbidden_actions:
                return ConflictCheck(
                    has_conflict=True,
                    conflict_type="security_violation",
                    conflict_description=f"Action '{action_type}' is forbidden",
                    severity=1.0,
                    resolution="Remove this action from the plan",
                )

            # Check for dangerous instruction patterns
            instruction = action.get("instruction", "").lower()
            if any(w in instruction for w in ["delete", "remove", "drop", "destroy"]):
                # Medium severity - needs review
                severity = 0.6
                return ConflictCheck(
                    has_conflict=True,
                    conflict_type="safety_risk",
                    conflict_description=f"Destructive action detected: {instruction[:50]}",
                    severity=severity,
                    resolution="Manual approval required",
                )

        return ConflictCheck(
            has_conflict=False,
            conflict_type="none",
            conflict_description="Security check passed",
            severity=0.0,
        )

    def _check_logical_consistency(
        self, user_id: str, actions: List[Dict[str, Any]]
    ) -> ConflictCheck:
        """Check if actions are logically consistent."""
        try:
            if len(actions) <= 1:
                return ConflictCheck(
                    has_conflict=False,
                    conflict_type="none",
                    conflict_description="Single action - no logical conflicts",
                    severity=0.0,
                )

            # Simple check: detect contradictory actions
            action_types = [a.get("type", "") for a in actions]

            # Example: can't write and delete same file
            write_targets = set(
                a.get("path", "") for a in actions if a.get("type") == "fs.write"
            )
            delete_targets = set(
                a.get("path", "") for a in actions if a.get("type") == "fs.delete"
            )

            overlap = write_targets & delete_targets
            if overlap:
                return ConflictCheck(
                    has_conflict=True,
                    conflict_type="contradictory_action",
                    conflict_description=f"Cannot write and delete same files: {overlap}",
                    severity=0.95,
                )

            return ConflictCheck(
                has_conflict=False,
                conflict_type="none",
                conflict_description="Logical consistency check passed",
                severity=0.0,
            )

        except Exception as e:
            logger.warning(f"Error in logical consistency check: {e}")
            return ConflictCheck(
                has_conflict=False,
                conflict_type="none",
                conflict_description="Could not verify logical consistency",
                severity=0.0,
            )

    def _check_resource_constraints(
        self, actions: List[Dict[str, Any]]
    ) -> ConflictCheck:
        """Check resource constraints."""
        web_requests = sum(1 for a in actions if a.get("type") == "web.fetch")
        memory_ops = sum(1 for a in actions if a.get("type") == "memory.add")

        if web_requests > self.resource_pools["web_requests"]["limit"]:
            return ConflictCheck(
                has_conflict=True,
                conflict_type="resource_limit",
                conflict_description=f"Too many web requests: {web_requests} > {self.resource_pools['web_requests']['limit']}",
                severity=0.7,
            )

        if memory_ops > self.resource_pools["memory_writes"]["limit"]:
            return ConflictCheck(
                has_conflict=True,
                conflict_type="resource_limit",
                conflict_description=f"Too many memory operations: {memory_ops}",
                severity=0.5,
            )

        return ConflictCheck(
            has_conflict=False,
            conflict_type="none",
            conflict_description="Resource constraints satisfied",
            severity=0.0,
        )


# Singleton
_detector = ConflictDetector()


def check_conflict(user_id: str, actions: List[Dict[str, Any]]) -> ConflictCheck:
    """Public API."""
    return _detector.check_conflict(user_id, actions)
