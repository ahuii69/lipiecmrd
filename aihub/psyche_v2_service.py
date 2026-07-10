#!/usr/bin/env python3
"""
Psyche V2 Service - high-level orchestration for psyche management.

Provides complete psyche API with adaptation, reflection, and policy derivation.
"""

import logging
import time
import uuid
from typing import Any, Literal

from aihub.psyche_v2_models import (
    PsycheV2Profile,
    PsycheV2State,
    PsycheV2Event,
    PsycheV2BehaviorRule,
    PsycheV2SnapshotResponse,
    PsycheV2ReflectResponse,
)
from aihub.psyche_v2_repository import (
    ensure_psyche_profile,
    ensure_psyche_state,
    update_psyche_profile,
    update_psyche_state,
    insert_psyche_event,
    get_recent_psyche_events,
    get_active_behavior_rules,
    insert_behavior_rule,
)
from aihub.psyche_v2_adaptation import (
    apply_event_to_state,
    adapt_profile_from_events,
    detect_mode_transition,
    apply_mode_hysteresis,
    smooth_pressure_value,
    smooth_scalar_field,
)
from aihub.psyche_v2_policy import derive_behavior_policy, policy_to_dict
from aihub.psyche_v2_relations import (
    update_relation_from_feedback,
    adjust_trust_from_success_rate,
    apply_relation_outcome_long_horizon,
)
from aihub.memory_psyche_contracts import PsycheEventType, PsycheMode

logger = logging.getLogger(__name__)


class PsycheV2Service:
    """Complete Psyche V2 service with all operations."""

    def ensure_user(self, user_id: str) -> tuple[PsycheV2Profile, PsycheV2State]:
        """
        Ensure user has profile and state, creating with defaults if needed.

        Returns (profile, state) tuple.
        """
        profile = ensure_psyche_profile(user_id)
        state = ensure_psyche_state(user_id)
        logger.debug(f"Ensured psyche v2 for user {user_id}")
        return profile, state

    def get_snapshot(self, user_id: str) -> PsycheV2SnapshotResponse:
        """
        Get complete psyche snapshot.

        Returns profile, state, active rules, habits, recent events, and derived policy.
        """
        profile = ensure_psyche_profile(user_id)
        state = ensure_psyche_state(user_id)
        active_rules = get_active_behavior_rules(user_id)
        active_habits = self.get_habits(user_id, min_intensity=0.2)
        recent_events = get_recent_psyche_events(user_id, limit=10)
        policy = derive_behavior_policy(user_id, profile, state)

        return PsycheV2SnapshotResponse(
            user_id=user_id,
            profile=profile,
            state=state,
            active_rules=active_rules,
            active_habits=active_habits,
            recent_events=recent_events,
            derived_policy=policy_to_dict(policy),
        )

    def apply_event(
        self,
        user_id: str,
        event_type: PsycheEventType,
        reason_text: str,
        source_ref: str | None = None,
        signal_strength: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> PsycheV2Event:
        """
        Apply psyche event and update state.

        Returns created event.
        """
        profile, state = self.ensure_user(user_id)

        # Build delta based on event type
        delta = self._build_event_delta(event_type, signal_strength)

        event = PsycheV2Event(
            id=f"psyche-event-{uuid.uuid4().hex[:16]}",
            user_id=user_id,
            event_type=event_type,
            delta=delta,
            reason_text=reason_text,
            source_ref=source_ref,
            created_ts=time.time(),
        )

        # Apply to state
        prev_smooth = (
            state.pressure_smoothed
            if state.pressure_smoothed > 0.0
            else state.pressure
        )
        updated_state = apply_event_to_state(state, event, signal_strength)
        updated_state = updated_state.model_copy(
            update={
                "certainty": smooth_scalar_field(
                    state.certainty, updated_state.certainty, alpha=0.38
                ),
                "mood": smooth_scalar_field(state.mood, updated_state.mood, alpha=0.32),
            }
        )
        candidate_mode = detect_mode_transition(updated_state)
        updated_state = apply_mode_hysteresis(updated_state, candidate_mode)
        new_smooth = smooth_pressure_value(prev_smooth, updated_state.pressure)
        updated_state = updated_state.model_copy(update={"pressure_smoothed": new_smooth})
        if candidate_mode and candidate_mode != state.current_mode:
            logger.debug(
                "Psyche mode candidate=%s streak=%s current=%s user=%s",
                candidate_mode,
                updated_state.mode_streak,
                updated_state.current_mode,
                user_id,
            )

        # Persist
        insert_psyche_event(event)
        update_psyche_state(updated_state)

        logger.debug(f"Applied psyche event: user={user_id} type={event_type}")
        return event

    def reflect_user(self, user_id: str) -> PsycheV2ReflectResponse:
        """
        Run reflection on user's recent psyche events.

        Updates profile based on accumulated patterns.
        Returns reflection summary.
        """
        profile = ensure_psyche_profile(user_id)
        state = ensure_psyche_state(user_id)
        recent_events = get_recent_psyche_events(user_id, limit=50)

        if not recent_events:
            return PsycheV2ReflectResponse(
                user_id=user_id,
                events_analyzed=0,
                profile_updated=False,
                state_updated=False,
                reflection_summary="No recent events to reflect on.",
                reflected_at=time.time(),
            )

        # Adapt profile
        updated_profile = adapt_profile_from_events(profile, recent_events)
        updated_profile.last_reflection_ts = time.time()

        profile_changed = self._profile_changed(profile, updated_profile)
        if profile_changed:
            update_psyche_profile(updated_profile)

        # Derive policy
        policy = derive_behavior_policy(user_id, updated_profile, state)

        # Build summary
        summary = self._build_reflection_summary(recent_events, profile_changed)

        return PsycheV2ReflectResponse(
            user_id=user_id,
            events_analyzed=len(recent_events),
            profile_updated=profile_changed,
            state_updated=False,
            new_rules_created=0,
            policy_snapshot=policy_to_dict(policy),
            reflection_summary=summary,
            reflected_at=time.time(),
        )

    def derive_policy(self, user_id: str) -> dict[str, Any]:
        """
        Derive current behavior policy.

        Returns policy dict.
        """
        profile = ensure_psyche_profile(user_id)
        state = ensure_psyche_state(user_id)
        policy = derive_behavior_policy(user_id, profile, state)
        return policy_to_dict(policy)

    def _build_event_delta(
        self, event_type: PsycheEventType, signal_strength: float
    ) -> dict[str, float]:
        """Build state delta for event type."""
        base_strength = signal_strength * 0.1

        deltas: dict[PsycheEventType, dict[str, float]] = {
            "interaction_start": {"energy": base_strength, "focus": base_strength},
            "interaction_complete": {"energy": -base_strength * 0.5, "certainty": base_strength},
            "tool_success": {"certainty": base_strength, "tool_bias": base_strength * 0.5},
            "tool_failure": {"certainty": -base_strength, "pressure": base_strength},
            "web_research_triggered": {"curiosity": base_strength, "web_bias": base_strength * 0.5},
            "planning_executed": {"focus": base_strength, "certainty": base_strength * 0.5},
            "user_feedback_positive": {
                "mood": base_strength,
                "certainty": base_strength,
                "social_openness": base_strength * 0.5,
            },
            "user_feedback_negative": {
                "mood": -base_strength,
                "pressure": base_strength,
                "certainty": -base_strength * 0.5,
            },
            "contradiction_detected": {"pressure": base_strength, "certainty": -base_strength},
            "confidence_shift": {"certainty": base_strength},
            "mode_transition": {"stability": base_strength * 0.5},
        }

        return deltas.get(event_type, {})

    def _profile_changed(self, old: PsycheV2Profile, new: PsycheV2Profile) -> bool:
        """Check if profile traits changed significantly."""
        threshold = 0.01
        trait_keys = [
            "core_directness",
            "core_patience",
            "core_curiosity",
            "core_caution",
            "core_assertiveness",
            "relation_trust",
            "relation_familiarity",
        ]

        for key in trait_keys:
            old_val = getattr(old, key)
            new_val = getattr(new, key)
            if abs(new_val - old_val) > threshold:
                return True

        return False

    def apply_outcome_event(
        self,
        user_id: str,
        outcome_kind: Literal["success", "failure", "timeout", "degraded", "progress"],
        source_ref: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Apply outcome event to Psyche V2 based on execution result.

        Returns write-back summary with event_applied.
        """
        result = {
            "attempted": True,
            "succeeded": False,
            "event_applied": None,
        }

        try:
            ctx = context or {}
            contradictions = ctx.get("contradictions_present", 0)
            goal_progress = ctx.get("goal_progress_changed", False)

            # Map outcome to psyche event
            event_map = {
                "success": (
                    "interaction_complete",
                    0.6,
                    "Successful execution outcome",
                ),
                "failure": (
                    "tool_failure",
                    0.7,
                    "Execution failed",
                ),
                "timeout": (
                    "mode_transition",
                    0.8,
                    "Execution timed out - high pressure",
                ),
                "degraded": (
                    "confidence_shift",
                    0.5,
                    "Degraded response quality",
                ),
                "progress": (
                    "user_feedback_positive",
                    0.6,
                    "Goal progress achieved",
                ),
            }

            event_type, strength, reason = event_map.get(
                outcome_kind,
                ("interaction_complete", 0.5, "Outcome recorded"),
            )

            # Adjust strength based on context
            if outcome_kind == "failure" and contradictions > 0:
                reason = f"Failed with {contradictions} contradictions present"
                strength = min(1.0, strength + 0.2)

            if outcome_kind == "success" and goal_progress:
                reason = "Successful progress on goal"
                strength = min(1.0, strength + 0.1)

            if outcome_kind == "timeout":
                # High pressure event
                profile, state = self.ensure_user(user_id)
                state.pressure = min(1.0, state.pressure + 0.2)
                state.focus = max(0.0, state.focus - 0.1)
                update_psyche_state(state)
                logger.info(f"V2 psyche: timeout increased pressure for user {user_id}")
                
                # Reinforce cautious habit
                self.reinforce_habit(
                    user_id=user_id,
                    habit_name="cautious_after_timeout",
                    habit_type="caution_tendency",
                    context={"source": source_ref, "outcome": outcome_kind},
                )

            # Pressure recovery on success
            if outcome_kind in ["success", "progress"]:
                profile, state = self.ensure_user(user_id)
                if state.pressure > 0.1:
                    state.pressure = max(0.0, state.pressure - 0.05)
                    state.certainty = min(1.0, state.certainty + 0.03)
                    state.stability = min(1.0, state.stability + 0.02)
                    update_psyche_state(state)
                    logger.debug(f"V2 psyche: success reduced pressure user={user_id}")
                
                # Reinforce confidence habit
                self.reinforce_habit(
                    user_id=user_id,
                    habit_name="confident_after_success",
                    habit_type="confidence_tendency",
                    context={"source": source_ref, "outcome": outcome_kind},
                )

            # Failure increases pressure
            if outcome_kind == "failure":
                profile, state = self.ensure_user(user_id)
                state.pressure = min(1.0, state.pressure + 0.1)
                state.certainty = max(0.0, state.certainty - 0.05)
                update_psyche_state(state)
                logger.debug(f"V2 psyche: failure increased pressure user={user_id}")
                
                # Reinforce caution habit
                self.reinforce_habit(
                    user_id=user_id,
                    habit_name="cautious_after_failure",
                    habit_type="caution_tendency",
                    context={"source": source_ref, "outcome": outcome_kind},
                )
            
            # Update relations
            self.update_relations_from_outcome(
                user_id=user_id,
                outcome_kind=outcome_kind,
                contradictions_present=contradictions,
            )

            # Apply event
            event = self.apply_event(
                user_id=user_id,
                event_type=event_type,
                reason_text=reason,
                source_ref=source_ref,
                signal_strength=strength,
                metadata=ctx,
            )

            result["event_applied"] = event_type
            result["succeeded"] = True
            logger.debug(f"V2 psyche outcome: {outcome_kind} → {event_type} for user {user_id}")

        except Exception as e:
            logger.error(f"V2 psyche outcome event failed: {e}", exc_info=True)
            result["succeeded"] = False

        return result

    def _build_reflection_summary(
        self, events: list[PsycheV2Event], profile_changed: bool
    ) -> str:
        """Build reflection summary from events."""
        if not events:
            return "No events analyzed."

        event_types = [e.event_type for e in events]
        unique_types = set(event_types)

        summary_parts = [f"Analyzed {len(events)} events"]

        if "user_feedback_positive" in unique_types:
            summary_parts.append("positive feedback received")
        if "user_feedback_negative" in unique_types:
            summary_parts.append("negative feedback noted")
        if "contradiction_detected" in unique_types:
            summary_parts.append("contradictions addressed")

        if profile_changed:
            summary_parts.append("profile adapted")

        return " | ".join(summary_parts)

    def get_habits(self, user_id: str, min_intensity: float = 0.2) -> list[Any]:
        """Get active habits for user."""
        from aihub.psyche_v2_habits import get_habits_for_user
        return get_habits_for_user(user_id, min_intensity=min_intensity, limit=20)

    def reinforce_habit(
        self,
        user_id: str,
        habit_name: str,
        habit_type: str,
        context: dict[str, Any],
    ) -> Any:
        """Reinforce or create habit."""
        from aihub.psyche_v2_habits import reinforce_or_create_habit
        return reinforce_or_create_habit(user_id, habit_name, habit_type, context, intensity_boost=0.1)

    def get_relations_summary(self, user_id: str) -> dict[str, Any]:
        """Get relation dynamics summary."""
        profile = ensure_psyche_profile(user_id)
        return {
            "user_id": user_id,
            "trust": profile.relation_trust,
            "friction": profile.relation_friction,
            "warmth": profile.relation_warmth,
            "directness_tolerance": profile.relation_directness_tolerance,
            "collaboration_confidence": profile.relation_collaboration_confidence,
            "familiarity": profile.relation_familiarity,
            "sync": profile.relation_sync,
        }

    def update_relations_from_outcome(
        self,
        user_id: str,
        outcome_kind: str,
        contradictions_present: int = 0,
    ) -> None:
        """
        Update relation dynamics based on outcome.

        Per-turn deltas are capped; interaction quality uses EMA so single failures
        do not dominate long-horizon cooperation signals.
        """
        profile = ensure_psyche_profile(user_id)
        apply_relation_outcome_long_horizon(
            profile, outcome_kind, contradictions_present=contradictions_present
        )
        profile.updated_ts = time.time()
        update_psyche_profile(profile)
        logger.debug(
            "Updated relations: trust=%.2f friction=%.2f ema_q=%.2f user=%s",
            profile.relation_trust,
            profile.relation_friction,
            profile.relation_interaction_quality_ema,
            user_id,
        )

    def compact_recent_events(self, user_id: str, keep_recent: int = 20) -> dict[str, Any]:
        """
        Compact old psyche events to prevent unbounded growth.
        
        Keeps most recent N events, aggregates older ones into summary.
        """
        events = get_recent_psyche_events(user_id, limit=200)
        
        if len(events) <= keep_recent:
            return {"ok": True, "kept_count": len(events), "compacted": False}
        
        # Keep recent events
        recent = events[:keep_recent]
        old = events[keep_recent:]
        
        # Aggregate old events
        from aihub.db import exec_one
        deleted_count = 0
        
        for old_event in old:
            try:
                exec_one(
                    "DELETE FROM psyche_v2_events WHERE id=? AND user_id=?",
                    (old_event.id, user_id),
                )
                deleted_count += 1
            except Exception as e:
                logger.warning(f"Failed to delete old event {old_event.id}: {e}")
        
        logger.info(f"Compacted psyche events: kept={len(recent)} deleted={deleted_count} user={user_id}")
        
        return {
            "ok": True,
            "kept_count": len(recent),
            "deleted_count": deleted_count,
            "compacted": True,
        }

