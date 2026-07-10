#!/usr/bin/env python3
"""
Psyche V2 Repository - data access layer for psyche_v2_* tables.

Handles all CRUD operations for Psyche V2 entities.
"""

import logging
import sqlite3
from typing import Any

from aihub.db import fetch_all, fetch_one, exec_one, now_ts, json_dumps, json_loads
from aihub.psyche_v2_models import (
    PsycheV2Profile,
    PsycheV2State,
    PsycheV2Event,
    PsycheV2BehaviorRule,
)

logger = logging.getLogger(__name__)


# ─── Psyche Profile ─────────────────────────────────────────────────────────


def ensure_psyche_profile(user_id: str) -> PsycheV2Profile:
    """Ensure user has a psyche profile, create with defaults if not."""
    row = fetch_one("SELECT * FROM psyche_v2_profile WHERE user_id=?", (user_id,))

    if row:
        return _row_to_profile(row)

    # Create default profile
    now = now_ts()
    exec_one(
        """
        INSERT INTO psyche_v2_profile(
            user_id, core_directness, core_patience, core_curiosity, core_caution,
            core_assertiveness, core_formality, core_warmth, core_initiative,
            core_skepticism, core_creativity, relation_trust, relation_familiarity,
            relation_sync, relation_friction, relation_warmth, relation_directness_tolerance,
            relation_collaboration_confidence, relation_interaction_quality_ema,
            stress_load, confidence_baseline, adaptation_velocity,
            last_reflection_ts, updated_ts
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (user_id, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.0, 0.5, 0.5, 0.5, 0.5, 0.0, 0.5, 0.2, None, now),
    )

    logger.debug(f"Created default psyche profile for user {user_id}")
    return PsycheV2Profile(user_id=user_id, updated_ts=now)


def update_psyche_profile(profile: PsycheV2Profile) -> bool:
    """Update psyche profile."""
    try:
        exec_one(
            """
            UPDATE psyche_v2_profile SET
                core_directness=?, core_patience=?, core_curiosity=?, core_caution=?,
                core_assertiveness=?, core_formality=?, core_warmth=?, core_initiative=?,
                core_skepticism=?, core_creativity=?, relation_trust=?, relation_familiarity=?,
                relation_sync=?, relation_friction=?, relation_warmth=?,
                relation_directness_tolerance=?, relation_collaboration_confidence=?,
                relation_interaction_quality_ema=?,
                stress_load=?, confidence_baseline=?, adaptation_velocity=?,
                last_reflection_ts=?, updated_ts=?
            WHERE user_id=?
            """,
            (
                profile.core_directness,
                profile.core_patience,
                profile.core_curiosity,
                profile.core_caution,
                profile.core_assertiveness,
                profile.core_formality,
                profile.core_warmth,
                profile.core_initiative,
                profile.core_skepticism,
                profile.core_creativity,
                profile.relation_trust,
                profile.relation_familiarity,
                profile.relation_sync,
                profile.relation_friction,
                profile.relation_warmth,
                profile.relation_directness_tolerance,
                profile.relation_collaboration_confidence,
                profile.relation_interaction_quality_ema,
                profile.stress_load,
                profile.confidence_baseline,
                profile.adaptation_velocity,
                profile.last_reflection_ts,
                profile.updated_ts,
                profile.user_id,
            ),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to update psyche profile: {e}")
        return False


def _row_to_profile(row: sqlite3.Row) -> PsycheV2Profile:
    """Convert SQLite row to PsycheV2Profile."""
    return PsycheV2Profile(
        user_id=row["user_id"],
        core_directness=float(row["core_directness"]),
        core_patience=float(row["core_patience"]),
        core_curiosity=float(row["core_curiosity"]),
        core_caution=float(row["core_caution"]),
        core_assertiveness=float(row["core_assertiveness"]),
        core_formality=float(row["core_formality"]),
        core_warmth=float(row["core_warmth"]),
        core_initiative=float(row["core_initiative"]),
        core_skepticism=float(row["core_skepticism"]),
        core_creativity=float(row["core_creativity"]),
        relation_trust=float(row["relation_trust"]),
        relation_familiarity=float(row["relation_familiarity"]),
        relation_sync=float(row["relation_sync"]),
        relation_friction=float(row["relation_friction"] if row["relation_friction"] is not None else 0.0),
        relation_warmth=float(row["relation_warmth"] if row["relation_warmth"] is not None else 0.5),
        relation_directness_tolerance=float(row["relation_directness_tolerance"] if row["relation_directness_tolerance"] is not None else 0.5),
        relation_collaboration_confidence=float(row["relation_collaboration_confidence"] if row["relation_collaboration_confidence"] is not None else 0.5),
        relation_interaction_quality_ema=float(
            row["relation_interaction_quality_ema"]
            if "relation_interaction_quality_ema" in row.keys()
            and row["relation_interaction_quality_ema"] is not None
            else 0.5
        ),
        stress_load=float(row["stress_load"]),
        confidence_baseline=float(row["confidence_baseline"]),
        adaptation_velocity=float(row["adaptation_velocity"]),
        last_reflection_ts=float(row["last_reflection_ts"]) if row["last_reflection_ts"] else None,
        updated_ts=float(row["updated_ts"]),
    )


# ─── Psyche State ───────────────────────────────────────────────────────────


def ensure_psyche_state(user_id: str) -> PsycheV2State:
    """Ensure user has a psyche state, create with defaults if not."""
    row = fetch_one("SELECT * FROM psyche_v2_state WHERE user_id=?", (user_id,))

    if row:
        return _row_to_state(row)

    # Create default state
    now = now_ts()
    exec_one(
        """
        INSERT INTO psyche_v2_state(
            user_id, mood, energy, focus, pressure, stability, certainty,
            social_openness, task_aggression, verbosity_bias, tool_bias, web_bias,
            current_mode, pending_mode, mode_streak, pressure_smoothed, updated_ts
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (user_id, 0.5, 0.5, 0.5, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, "neutral", "", 0, 0.0, now),
    )

    logger.debug(f"Created default psyche state for user {user_id}")
    return PsycheV2State(user_id=user_id, updated_ts=now)


def update_psyche_state(state: PsycheV2State) -> bool:
    """Update psyche state."""
    try:
        exec_one(
            """
            UPDATE psyche_v2_state SET
                mood=?, energy=?, focus=?, pressure=?, stability=?, certainty=?,
                social_openness=?, task_aggression=?, verbosity_bias=?,
                tool_bias=?, web_bias=?, current_mode=?, pending_mode=?, mode_streak=?,
                pressure_smoothed=?, updated_ts=?
            WHERE user_id=?
            """,
            (
                state.mood,
                state.energy,
                state.focus,
                state.pressure,
                state.stability,
                state.certainty,
                state.social_openness,
                state.task_aggression,
                state.verbosity_bias,
                state.tool_bias,
                state.web_bias,
                state.current_mode,
                state.pending_mode,
                state.mode_streak,
                state.pressure_smoothed,
                state.updated_ts,
                state.user_id,
            ),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to update psyche state: {e}")
        return False


def _row_to_state(row: sqlite3.Row) -> PsycheV2State:
    """Convert SQLite row to PsycheV2State."""
    keys = row.keys()
    raw_p = float(row["pressure"])
    psmooth = (
        float(row["pressure_smoothed"])
        if "pressure_smoothed" in keys and row["pressure_smoothed"] is not None
        else 0.0
    )
    if "pressure_smoothed" not in keys or psmooth == 0.0:
        psmooth = raw_p
    return PsycheV2State(
        user_id=row["user_id"],
        mood=float(row["mood"]),
        energy=float(row["energy"]),
        focus=float(row["focus"]),
        pressure=raw_p,
        stability=float(row["stability"]),
        certainty=float(row["certainty"]),
        social_openness=float(row["social_openness"]),
        task_aggression=float(row["task_aggression"]),
        verbosity_bias=float(row["verbosity_bias"]),
        tool_bias=float(row["tool_bias"]),
        web_bias=float(row["web_bias"]),
        current_mode=row["current_mode"],
        pending_mode=str(row["pending_mode"] if "pending_mode" in keys and row["pending_mode"] is not None else ""),
        mode_streak=int(row["mode_streak"] if "mode_streak" in keys and row["mode_streak"] is not None else 0),
        pressure_smoothed=psmooth,
        updated_ts=float(row["updated_ts"]),
    )


# ─── Psyche Events ──────────────────────────────────────────────────────────


def insert_psyche_event(event: PsycheV2Event) -> bool:
    """Insert psyche event."""
    try:
        exec_one(
            """
            INSERT INTO psyche_v2_events(
                id, user_id, event_type, delta_json, reason_text, source_ref, created_ts
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                event.id,
                event.user_id,
                event.event_type,
                json_dumps(event.delta),
                event.reason_text,
                event.source_ref,
                event.created_ts,
            ),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to insert psyche event: {e}")
        return False


def get_recent_psyche_events(user_id: str, limit: int = 20) -> list[PsycheV2Event]:
    """Get recent psyche events for user."""
    rows = fetch_all(
        "SELECT * FROM psyche_v2_events WHERE user_id=? ORDER BY created_ts DESC LIMIT ?",
        (user_id, limit),
    )
    return [_row_to_event(r) for r in rows]


def _row_to_event(row: sqlite3.Row) -> PsycheV2Event:
    """Convert SQLite row to PsycheV2Event."""
    return PsycheV2Event(
        id=row["id"],
        user_id=row["user_id"],
        event_type=row["event_type"],
        delta=json_loads(row["delta_json"]) or {},
        reason_text=row["reason_text"],
        source_ref=row["source_ref"],
        created_ts=float(row["created_ts"]),
    )


# ─── Behavior Rules ─────────────────────────────────────────────────────────


def insert_behavior_rule(rule: PsycheV2BehaviorRule) -> bool:
    """Insert behavior rule."""
    try:
        exec_one(
            """
            INSERT INTO psyche_v2_behavior_rules(
                id, user_id, rule_name, trigger_json, behavior_adjustment_json,
                priority, is_active, created_ts, updated_ts
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                rule.id,
                rule.user_id,
                rule.rule_name,
                json_dumps(rule.trigger),
                json_dumps(rule.behavior_adjustment),
                rule.priority,
                int(rule.is_active),
                rule.created_ts,
                rule.updated_ts,
            ),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to insert behavior rule: {e}")
        return False


def get_active_behavior_rules(user_id: str) -> list[PsycheV2BehaviorRule]:
    """Get active behavior rules for user."""
    rows = fetch_all(
        """
        SELECT * FROM psyche_v2_behavior_rules
        WHERE user_id=? AND is_active=1
        ORDER BY priority DESC, created_ts DESC
        """,
        (user_id,),
    )
    return [_row_to_behavior_rule(r) for r in rows]


def _row_to_behavior_rule(row: sqlite3.Row) -> PsycheV2BehaviorRule:
    """Convert SQLite row to PsycheV2BehaviorRule."""
    return PsycheV2BehaviorRule(
        id=row["id"],
        user_id=row["user_id"],
        rule_name=row["rule_name"],
        trigger=json_loads(row["trigger_json"]) or {},
        behavior_adjustment=json_loads(row["behavior_adjustment_json"]) or {},
        priority=int(row["priority"]),
        is_active=bool(row["is_active"]),
        created_ts=float(row["created_ts"]),
        updated_ts=float(row["updated_ts"]),
    )
