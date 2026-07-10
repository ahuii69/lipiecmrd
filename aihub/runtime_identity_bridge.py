#!/usr/bin/env python3
"""
Runtime Identity Bridge.

Provides unified identity snapshot combining Memory V2 and Psyche V2.
"""

import logging
import time

from aihub.memory_core import get_memory_core
from aihub.memory_psyche_contracts import IdentityBridgeSnapshot
from aihub.runtime_memory_bridge import build_memory_v2_runtime_snapshot
from aihub.runtime_psyche_bridge import build_psyche_v2_runtime_snapshot

logger = logging.getLogger(__name__)


def build_identity_bridge_snapshot(
    user_id: str, query_text: str = ""
) -> IdentityBridgeSnapshot:
    """
    Build unified identity snapshot for runtime consumption.

    Combines Memory V2 and Psyche V2 into single coherent view.
    """
    try:
        memory_snapshot = build_memory_v2_runtime_snapshot(user_id, query_text)
        psyche_snapshot = build_psyche_v2_runtime_snapshot(user_id)

        facets = get_memory_core().v2_service.collect_identity_memory_facets(user_id)
        top_preferences = facets["top_preferences"]
        top_procedures = facets["top_procedures"]
        contradictions_count = int(facets["contradictions_count"])
        autobio = str(facets["autobio_summary"])

        # Get active habits
        active_habits = []
        if psyche_snapshot.get("loaded"):
            habits = psyche_snapshot.get("habit_biases", [])
            for h in habits[:3]:
                active_habits.append(
                    {
                        "habit_name": h.get("habit_name", ""),
                        "habit_type": h.get("habit_type", ""),
                        "intensity": h.get("intensity", 0.0),
                    }
                )

        return IdentityBridgeSnapshot(
            user_id=user_id,
            top_preferences=top_preferences,
            top_procedures=top_procedures,
            active_contradictions_count=contradictions_count,
            active_habits=active_habits,
            relation_trust=psyche_snapshot.get("relation_trust", 0.5),
            relation_familiarity=psyche_snapshot.get("relation_familiarity", 0.5),
            relation_sync=(
                psyche_snapshot.get("relation_sync", 0.5)
                if psyche_snapshot.get("loaded")
                else 0.5
            ),
            relation_friction=psyche_snapshot.get("relation_friction", 0.0),
            relation_warmth=psyche_snapshot.get("relation_warmth", 0.5),
            behavior_mode=psyche_snapshot.get("mode", "neutral"),
            stress_load=psyche_snapshot.get("stress_load", 0.0),
            pressure=psyche_snapshot.get("pressure", 0.0),
            autobio_summary=autobio,
            memory_v2_total=memory_snapshot.get("total_items", 0),
            psyche_v2_certainty=psyche_snapshot.get("certainty", 0.5),
            snapshot_ts=time.time(),
            relation_interaction_quality_ema=float(
                psyche_snapshot.get("relation_interaction_quality_ema", 0.5)
            ),
            relation_drift_score=float(
                psyche_snapshot.get("relation_drift_score", 0.0)
            ),
            psyche_drift_score=float(psyche_snapshot.get("psyche_drift_score", 0.0)),
        )
    except Exception as e:
        logger.error(f"Failed to build identity bridge snapshot: {e}")
        return IdentityBridgeSnapshot(
            user_id=user_id,
            top_preferences=[],
            top_procedures=[],
            active_contradictions_count=0,
            active_habits=[],
            relation_trust=0.5,
            relation_familiarity=0.5,
            relation_sync=0.5,
            relation_friction=0.0,
            relation_warmth=0.5,
            behavior_mode="neutral",
            stress_load=0.0,
            pressure=0.0,
            autobio_summary="Error loading identity",
            memory_v2_total=0,
            psyche_v2_certainty=0.5,
            snapshot_ts=time.time(),
        )
