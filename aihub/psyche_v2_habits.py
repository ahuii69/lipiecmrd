#!/usr/bin/env python3
"""
Psyche V2 Habits - learned behavioral patterns.

Implements habit detection, reinforcement, and influence on runtime behavior.
"""

import json
import logging
import sqlite3
import time
import uuid
from typing import Any

from aihub.db import fetch_all, fetch_one, exec_one, now_ts
from aihub.psyche_v2_models import PsycheV2Habit

logger = logging.getLogger(__name__)


# ─── Repository ─────────────────────────────────────────────────────────────


def insert_habit(habit: PsycheV2Habit) -> bool:
    """Insert new habit into database."""
    try:
        exec_one(
            """
            INSERT INTO psyche_v2_habits(
                id, user_id, habit_name, habit_type, intensity,
                reinforcement_count, last_reinforced_ts, context_json,
                created_ts, updated_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                habit.id,
                habit.user_id,
                habit.habit_name,
                habit.habit_type,
                habit.intensity,
                habit.reinforcement_count,
                habit.last_reinforced_ts,
                json.dumps(habit.context),
                habit.created_ts,
                habit.updated_ts,
            ),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to insert habit: {e}")
        return False


def update_habit(habit: PsycheV2Habit) -> bool:
    """Update existing habit."""
    try:
        exec_one(
            """
            UPDATE psyche_v2_habits SET
                intensity=?, reinforcement_count=?, last_reinforced_ts=?,
                context_json=?, updated_ts=?
            WHERE id=? AND user_id=?
            """,
            (
                habit.intensity,
                habit.reinforcement_count,
                habit.last_reinforced_ts,
                json.dumps(habit.context),
                habit.updated_ts,
                habit.id,
                habit.user_id,
            ),
        )
        return True
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Failed to update habit: {e}")
        return False


def get_habits_for_user(user_id: str, min_intensity: float = 0.2, limit: int = 20) -> list[PsycheV2Habit]:
    """Get active habits for user."""
    rows = fetch_all(
        """
        SELECT * FROM psyche_v2_habits
        WHERE user_id=? AND intensity >= ?
        ORDER BY intensity DESC, last_reinforced_ts DESC
        LIMIT ?
        """,
        (user_id, min_intensity, limit),
    )
    return [_row_to_habit(r) for r in rows]


def get_habit_by_name(user_id: str, habit_name: str) -> PsycheV2Habit | None:
    """Get habit by name."""
    row = fetch_one(
        "SELECT * FROM psyche_v2_habits WHERE user_id=? AND habit_name=?",
        (user_id, habit_name),
    )
    if not row:
        return None
    return _row_to_habit(row)


def _row_to_habit(row: sqlite3.Row) -> PsycheV2Habit:
    """Convert SQLite row to PsycheV2Habit."""
    return PsycheV2Habit(
        id=row["id"],
        user_id=row["user_id"],
        habit_name=row["habit_name"],
        habit_type=row["habit_type"],
        intensity=float(row["intensity"]),
        reinforcement_count=int(row["reinforcement_count"]),
        last_reinforced_ts=float(row["last_reinforced_ts"]),
        context=json.loads(row["context_json"]) if row["context_json"] else {},
        created_ts=float(row["created_ts"]),
        updated_ts=float(row["updated_ts"]),
    )


# ─── Habit Logic ────────────────────────────────────────────────────────────


def _scaled_intensity_boost(base: float, reinforcement_count_before: int) -> float:
    """Early reinforcements contribute less — habits need repetition to stabilize."""
    if reinforcement_count_before < 2:
        return base * 0.35
    if reinforcement_count_before < 4:
        return base * 0.62
    return base


def reinforce_or_create_habit(
    user_id: str,
    habit_name: str,
    habit_type: str,
    context: dict[str, Any],
    intensity_boost: float = 0.1,
) -> PsycheV2Habit:
    """
    Reinforce existing habit or create new one.
    
    Habit types:
    - cautious_after_failure
    - confident_after_success
    - tool_preference
    - web_preference
    - planning_tendency
    - direct_execution_tendency
    """
    existing = get_habit_by_name(user_id, habit_name)
    now = now_ts()
    
    if existing:
        boost = _scaled_intensity_boost(intensity_boost, existing.reinforcement_count)
        existing.intensity = min(1.0, existing.intensity + boost)
        existing.reinforcement_count += 1
        existing.last_reinforced_ts = now
        existing.updated_ts = now
        existing.context.update(context)
        update_habit(existing)
        logger.debug(f"Reinforced habit: {habit_name} intensity={existing.intensity:.2f} user={user_id}")
        return existing
    else:
        # Create
        initial_boost = _scaled_intensity_boost(intensity_boost, 0)
        habit = PsycheV2Habit(
            id=f"habit-{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            habit_name=habit_name,
            habit_type=habit_type,
            intensity=min(1.0, 0.3 + initial_boost),
            reinforcement_count=1,
            last_reinforced_ts=now,
            context=context,
            created_ts=now,
            updated_ts=now,
        )
        insert_habit(habit)
        logger.debug(f"Created habit: {habit_name} intensity={habit.intensity:.2f} user={user_id}")
        return habit


def decay_habits(user_id: str, decay_rate: float = 0.02) -> int:
    """
    Decay habit intensity for habits not recently reinforced.
    
    Returns count of decayed habits.
    """
    habits = get_habits_for_user(user_id, min_intensity=0.0, limit=100)
    now = now_ts()
    decayed_count = 0
    
    for habit in habits:
        age_since_reinforcement = now - habit.last_reinforced_ts
        if age_since_reinforcement > 7 * 86400:  # 7 days
            habit.intensity = max(0.0, habit.intensity - decay_rate)
            habit.updated_ts = now
            update_habit(habit)
            decayed_count += 1
    
    return decayed_count


def get_dominant_habits(user_id: str, top_n: int = 3) -> list[PsycheV2Habit]:
    """Get top N most intense habits."""
    return get_habits_for_user(user_id, min_intensity=0.0, limit=top_n)
