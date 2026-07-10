#!/usr/bin/env python3
"""Mounted FastAPI router for the structured Psyche V2 HTTP API.

The router exposes snapshots, events, reflection, policy, habits, relations and
the exact runtime context consumed by chat/agent. All operations use the shared
``PsycheCanonicalCore`` service instance.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aihub.memory_psyche_contracts import PsycheEventType
from aihub.psyche_core import get_psyche_core
from aihub.psyche_v2_models import (
    PsycheV2Event,
    PsycheV2ReflectResponse,
    PsycheV2SnapshotResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/psyche/v2", tags=["psyche-v2"])


class ApplyEventRequest(BaseModel):
    """Request to apply psyche event."""

    user_id: str
    event_type: PsycheEventType
    reason_text: str
    source_ref: str | None = None
    signal_strength: float = 0.5
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApplyEventResponse(BaseModel):
    """Response for event application."""

    success: bool
    event_id: str
    state_updated: bool = True
    mode_transitioned: bool = False
    new_mode: str | None = None


@router.get("/{user_id}", response_model=PsycheV2SnapshotResponse)
async def get_psyche_snapshot(user_id: str) -> PsycheV2SnapshotResponse:
    """Get complete psyche snapshot for user."""
    try:
        snapshot = get_psyche_core().v2_service.get_snapshot(user_id)
        logger.info(f"Psyche snapshot requested for user {user_id}")
        return snapshot
    except Exception as e:
        logger.error(f"Failed to get psyche snapshot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runtime/{user_id}", response_model=dict[str, Any])
async def get_psyche_runtime_context(user_id: str) -> dict[str, Any]:
    """Get the exact Psyche V2 runtime context consumed by chat/agent."""
    try:
        from aihub.runtime_psyche_bridge import (
            build_psyche_v2_behavior_context,
            build_psyche_v2_runtime_snapshot,
            summarize_psyche_v2_for_agent,
            summarize_psyche_v2_for_chat,
        )

        behavior = build_psyche_v2_behavior_context(user_id)
        return {
            "ok": True,
            "user_id": user_id,
            "snapshot": build_psyche_v2_runtime_snapshot(user_id),
            "behavior_context": behavior.__dict__,
            "chat_summary": summarize_psyche_v2_for_chat(user_id),
            "agent_summary": summarize_psyche_v2_for_agent(user_id),
        }
    except Exception as e:
        logger.error(f"Failed to get psyche runtime context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/event", response_model=ApplyEventResponse)
async def apply_psyche_event(request: ApplyEventRequest) -> ApplyEventResponse:
    """Apply psyche event and update state."""
    try:
        event = get_psyche_core().v2_service.apply_event(
            user_id=request.user_id,
            event_type=request.event_type,
            reason_text=request.reason_text,
            source_ref=request.source_ref,
            signal_strength=request.signal_strength,
            metadata=request.metadata,
        )

        return ApplyEventResponse(
            success=True,
            event_id=event.id,
            state_updated=True,
        )
    except Exception as e:
        logger.error(f"Failed to apply psyche event: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reflect/{user_id}", response_model=PsycheV2ReflectResponse)
async def reflect_psyche(user_id: str) -> PsycheV2ReflectResponse:
    """Run reflection on user's psyche events."""
    try:
        result = get_psyche_core().v2_service.reflect_user(user_id)
        logger.info(
            f"Psyche reflection for user {user_id}: {result.events_analyzed} events analyzed"
        )
        return result
    except Exception as e:
        logger.error(f"Failed to reflect psyche: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/policy/{user_id}", response_model=dict[str, Any])
async def get_behavior_policy(user_id: str) -> dict[str, Any]:
    """Get derived behavior policy for user."""
    try:
        policy = get_psyche_core().v2_service.derive_policy(user_id)
        logger.info(f"Behavior policy requested for user {user_id}")
        return policy
    except Exception as e:
        logger.error(f"Failed to get behavior policy: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{user_id}", response_model=list[PsycheV2Event])
async def get_psyche_history(user_id: str, limit: int = 50) -> list[PsycheV2Event]:
    """Get psyche event history for user."""
    try:
        events = get_psyche_core().v2_event_history(user_id, limit=limit)
        logger.info(
            f"Psyche history requested for user {user_id}: {len(events)} events"
        )
        return events
    except Exception as e:
        logger.error(f"Failed to get psyche history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class HabitsResponse(BaseModel):
    """Response for habits endpoint."""

    user_id: str
    habits: list[dict[str, Any]] = Field(default_factory=list)
    total_count: int = 0


@router.get("/habits/{user_id}", response_model=HabitsResponse)
async def get_user_habits(user_id: str, min_intensity: float = 0.2) -> HabitsResponse:
    """Get active habits for user."""
    try:
        habits = get_psyche_core().v2_service.get_habits(
            user_id, min_intensity=min_intensity
        )
        habits_dict = [
            {
                "id": h.id,
                "habit_name": h.habit_name,
                "habit_type": h.habit_type,
                "intensity": h.intensity,
                "reinforcement_count": h.reinforcement_count,
                "last_reinforced_ts": h.last_reinforced_ts,
                "context": h.context,
            }
            for h in habits
        ]

        logger.info(f"Habits requested for user {user_id}: {len(habits)} found")
        return HabitsResponse(
            user_id=user_id, habits=habits_dict, total_count=len(habits)
        )
    except Exception as e:
        logger.error(f"Failed to get habits: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class RelationsResponse(BaseModel):
    """Response for relations endpoint."""

    user_id: str
    trust: float = 0.5
    friction: float = 0.0
    warmth: float = 0.5
    directness_tolerance: float = 0.5
    collaboration_confidence: float = 0.5
    familiarity: float = 0.5
    sync: float = 0.5


@router.get("/relations/{user_id}", response_model=RelationsResponse)
async def get_user_relations(user_id: str) -> RelationsResponse:
    """Get relation dynamics for user."""
    try:
        result = get_psyche_core().v2_service.get_relations_summary(user_id)
        logger.info(f"Relations requested for user {user_id}")
        return RelationsResponse(**result)
    except Exception as e:
        logger.error(f"Failed to get relations: {e}")
        raise HTTPException(status_code=500, detail=str(e))
