#!/usr/bin/env python3

"""GoalEngine: persistent goal-driven layer for unified runtime.

GoalEngine upgrades AI-Hub from mostly reactive execution to explicit
goal-driven operation. Goals are first-class persistent objects with lifecycle,
scoring, selection and traceable execution linkage.

ExecutiveController consumes GoalEngine each cycle to:
- extract/update goals from input and context,
- select a dominant goal,
- bias strategy/planning with goal signals,
- track progress and lifecycle outcomes after execution.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aihub.db import exec_one, fetch_all, fetch_one, json_dumps, json_loads, now_ts

logger = logging.getLogger(__name__)


class GoalType(str, Enum):
    TASK = "task"
    INFORMATION_NEED = "information_need"
    RESEARCH_GOAL = "research_goal"
    MAINTENANCE_GOAL = "maintenance_goal"
    LEARNING_GOAL = "learning_goal"
    USER_INTENT_GOAL = "user_intent_goal"
    SYSTEM_GOAL = "system_goal"
    LONG_TERM_GOAL = "long_term_goal"


class GoalStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    BLOCKED = "blocked"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


_OPEN_STATUSES = {
    GoalStatus.PROPOSED.value,
    GoalStatus.ACTIVE.value,
    GoalStatus.BLOCKED.value,
    GoalStatus.SCHEDULED.value,
}

# Cleanup / actionable-filter contract (24.07)
GOAL_CLEANUP_VERSION = "24.07.1"
GOAL_CLEANUP_REASON = "non_goal_meta_or_smalltalk"

_NON_ACTIONABLE_TITLE_PREFIXES = (
    "uzupełnić brak kontekstu:",
    "uzupelnic brak kontekstu:",
    "informacja:",
)

_SMALLTALK_EXACT = frozenset(
    {
        "elo",
        "hej",
        "cześć",
        "czesc",
        "siema",
        "hi",
        "hello",
        "hey",
        "yo",
        "dzięki",
        "dzieki",
        "dziękuję",
        "dziekuje",
        "thx",
        "thanks",
        "ok",
        "okej",
        "spoko",
        "git",
        "super",
        "lol",
        "haha",
    }
)


def _goal_query_blob(goal: Goal | GoalCandidate) -> str:
    meta = dict(getattr(goal, "metadata", None) or {})
    parts = [
        str(getattr(goal, "title", "") or ""),
        str(getattr(goal, "description", "") or ""),
        str(meta.get("query") or ""),
        str(meta.get("intent_text") or ""),
    ]
    title = str(getattr(goal, "title", "") or "")
    for prefix in _NON_ACTIONABLE_TITLE_PREFIXES:
        low = title.lower()
        if low.startswith(prefix):
            parts.append(title[len(prefix) :].strip())
            break
    return "\n".join(p for p in parts if p).strip()


def is_non_actionable_meta_or_smalltalk_goal(goal: Goal | GoalCandidate) -> bool:
    """True for greeting/meta/small-talk goals that must not drive routing."""
    from aihub.strategy_selector import is_assistant_meta_ask, is_simple_greeting

    meta = dict(getattr(goal, "metadata", None) or {})
    if meta.get("cleanup_reason") == GOAL_CLEANUP_REASON:
        return True
    if meta.get("non_actionable") is True:
        return True
    # Explicit user pin / long-horizon → keep (even if title looks soft)
    if meta.get("user_pinned") or meta.get("user_accepted") or meta.get("manually_accepted"):
        return False
    if meta.get("long_horizon_task_id") or getattr(goal, "parent_goal_id", None):
        return False
    if meta.get("execution_nodes") or meta.get("depends_on") or meta.get("dependency_ids"):
        return False

    goal_type = str(getattr(goal, "goal_type", "") or "")
    source = str(getattr(goal, "source", "") or "")
    blob = _goal_query_blob(goal)
    blob_l = blob.lower().strip()
    title_l = str(getattr(goal, "title", "") or "").lower().strip()

    # Clear meta/greeting text wins over spurious progress on information_need.
    meta_text = is_assistant_meta_ask(blob) or is_simple_greeting(blob) or blob_l in _SMALLTALK_EXACT
    if not meta_text:
        rest = title_l
        for p in _NON_ACTIONABLE_TITLE_PREFIXES:
            if rest.startswith(p):
                rest = rest[len(p) :].strip()
                break
        meta_text = (
            is_assistant_meta_ask(rest)
            or is_simple_greeting(rest)
            or rest in _SMALLTALK_EXACT
        )

    if goal_type == GoalType.INFORMATION_NEED.value and source in {
        "user_input",
        "memory_gap",
    }:
        if meta_text:
            return True
        if any(title_l.startswith(p) for p in _NON_ACTIONABLE_TITLE_PREFIXES):
            # Generic memory-gap fill without multi-step / future commitment
            if source == "memory_gap" and not any(
                k in blob_l
                for k in (
                    "zaplanuj",
                    "migracj",
                    "etap",
                    "śledź",
                    "sledz",
                    "planuj",
                    "zrób",
                    "zrob",
                    "wdroż",
                    "wdroz",
                )
            ):
                words = [w for w in blob.split() if w]
                if len(words) <= 40:
                    return True

    if goal_type in {GoalType.INFORMATION_NEED.value, GoalType.USER_INTENT_GOAL.value}:
        if meta_text:
            return True
    return False


def is_actionable_goal(goal: Goal) -> bool:
    """Routing-facing filter: open goals that are real trackable work."""
    status = str(goal.status or "")
    if status in {
        GoalStatus.CANCELLED.value,
        GoalStatus.EXPIRED.value,
        GoalStatus.COMPLETED.value,
        GoalStatus.FAILED.value,
    }:
        return False
    if status not in {
        GoalStatus.ACTIVE.value,
        GoalStatus.SCHEDULED.value,
        GoalStatus.BLOCKED.value,
        GoalStatus.PROPOSED.value,
    }:
        return False
    # Meta/small-talk junk never actionable — even with spurious progress.
    if is_non_actionable_meta_or_smalltalk_goal(goal):
        return False
    meta = dict(goal.metadata or {})
    if meta.get("non_actionable") is True:
        return False
    if meta.get("user_pinned") or meta.get("user_accepted") or meta.get("long_horizon_task_id"):
        return True
    if goal.parent_goal_id or float(goal.progress or 0.0) > 0.05:
        return True
    if goal.goal_type in {
        GoalType.TASK.value,
        GoalType.RESEARCH_GOAL.value,
        GoalType.LONG_TERM_GOAL.value,
        GoalType.USER_INTENT_GOAL.value,
        GoalType.LEARNING_GOAL.value,
        GoalType.MAINTENANCE_GOAL.value,
        GoalType.SYSTEM_GOAL.value,
    }:
        return True
    if goal.goal_type == GoalType.INFORMATION_NEED.value:
        blob = _goal_query_blob(goal).lower()
        if any(
            k in blob
            for k in (
                "zaplanuj",
                "migracj",
                "etap",
                "research",
                "zbadaj",
                "wyszukaj",
                "śledź",
                "sledz",
            )
        ):
            return True
        return False
    return True


def classify_goal_cleanup(goal: Goal) -> tuple[bool, str]:
    """Return (should_cleanup, reason). Idempotent-safe for already cleaned rows."""
    meta = dict(goal.metadata or {})
    if goal.status == GoalStatus.CANCELLED.value and meta.get("cleanup_reason") == GOAL_CLEANUP_REASON:
        return False, "already_cleaned"
    if goal.status not in _OPEN_STATUSES and goal.status != GoalStatus.PROPOSED.value:
        return False, f"status_{goal.status}"
    if meta.get("user_pinned") or meta.get("user_accepted") or meta.get("manually_accepted"):
        return False, "user_pinned_or_accepted"
    if meta.get("long_horizon_task_id") or goal.parent_goal_id:
        return False, "has_long_horizon_or_parent"
    if meta.get("execution_nodes") or meta.get("depends_on") or meta.get("dependency_ids"):
        return False, "has_execution_or_deps"
    # Meta/small-talk information_need: clean even if spurious progress was recorded.
    if is_non_actionable_meta_or_smalltalk_goal(goal):
        return True, GOAL_CLEANUP_REASON
    if float(goal.progress or 0.0) > 0.05:
        return False, "has_progress"
    if goal.goal_type == GoalType.INFORMATION_NEED.value and str(goal.source or "") in {
        "user_input",
        "memory_gap",
    }:
        from aihub.strategy_selector import is_assistant_meta_ask, is_simple_greeting

        blob = _goal_query_blob(goal)
        if is_assistant_meta_ask(blob) or is_simple_greeting(blob):
            return True, GOAL_CLEANUP_REASON
        title_l = (goal.title or "").lower()
        if any(title_l.startswith(p) for p in _NON_ACTIONABLE_TITLE_PREFIXES):
            rest = title_l
            for p in _NON_ACTIONABLE_TITLE_PREFIXES:
                if rest.startswith(p):
                    rest = rest[len(p) :].strip()
                    break
            if is_assistant_meta_ask(rest) or is_simple_greeting(rest) or len(rest.split()) <= 12:
                if not any(
                    k in blob.lower()
                    for k in ("zaplanuj", "migracj", "etap", "śledź", "sledz", "wdroż", "wdroz")
                ):
                    return True, GOAL_CLEANUP_REASON
    return False, "actionable_or_unknown"


_ALLOWED_TRANSITIONS = {
    GoalStatus.PROPOSED.value: {
        GoalStatus.ACTIVE.value,
        GoalStatus.SCHEDULED.value,
        GoalStatus.CANCELLED.value,
        GoalStatus.EXPIRED.value,
    },
    GoalStatus.SCHEDULED.value: {
        GoalStatus.ACTIVE.value,
        GoalStatus.BLOCKED.value,
        GoalStatus.CANCELLED.value,
        GoalStatus.EXPIRED.value,
    },
    GoalStatus.ACTIVE.value: {
        GoalStatus.BLOCKED.value,
        GoalStatus.SCHEDULED.value,
        GoalStatus.COMPLETED.value,
        GoalStatus.FAILED.value,
        GoalStatus.CANCELLED.value,
        GoalStatus.EXPIRED.value,
    },
    GoalStatus.BLOCKED.value: {
        GoalStatus.ACTIVE.value,
        GoalStatus.SCHEDULED.value,
        GoalStatus.FAILED.value,
        GoalStatus.CANCELLED.value,
        GoalStatus.EXPIRED.value,
    },
    GoalStatus.COMPLETED.value: set(),
    GoalStatus.FAILED.value: set(),
    GoalStatus.EXPIRED.value: set(),
    GoalStatus.CANCELLED.value: set(),
}


@dataclass
class Goal:
    goal_id: str
    user_id: str
    title: str
    description: str
    goal_type: str
    source: str
    status: str
    priority: float
    urgency: float
    importance: float
    confidence: float
    created_at: float
    updated_at: float
    expires_at: float | None = None
    parent_goal_id: str | None = None
    tags: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    failure_criteria: list[str] = field(default_factory=list)
    progress: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GoalCandidate:
    user_id: str
    title: str
    description: str
    goal_type: str
    source: str
    priority: float = 0.5
    urgency: float = 0.5
    importance: float = 0.5
    confidence: float = 0.5
    expires_at: float | None = None
    parent_goal_id: str | None = None
    tags: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    failure_criteria: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GoalUpdate:
    user_id: str
    goal_id: str
    status: str | None = None
    priority: float | None = None
    urgency: float | None = None
    importance: float | None = None
    confidence: float | None = None
    progress: float | None = None
    expires_at: float | None = None
    metadata: dict[str, Any] | None = None
    reason: str = "update"


@dataclass
class GoalScore:
    goal_id: str
    score: float
    breakdown: dict[str, float]
    reasons: list[str] = field(default_factory=list)


@dataclass
class GoalExecutionHint:
    goal_id: str
    recommended_strategy: str
    planning_bias: str
    research_intensity: str
    create_followups: bool


@dataclass
class GoalContext:
    user_id: str
    active_goals: list[Goal] = field(default_factory=list)
    top_scores: list[GoalScore] = field(default_factory=list)
    selected_goal: Goal | None = None
    selected_reason: str = ""
    execution_hint: GoalExecutionHint | None = None
    candidates: list[GoalCandidate] = field(default_factory=list)
    created_goal_ids: list[str] = field(default_factory=list)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _goal_fingerprint(user_id: str, goal_type: str, title: str) -> str:
    raw = "\0".join([
        _normalize_text(user_id),
        _normalize_text(goal_type),
        _normalize_text(title),
    ])
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def _goal_id(user_id: str, goal_type: str, title: str) -> str:
    raw = "\0".join([
        user_id,
        goal_type,
        title,
        str(time.time_ns()),
    ])
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:32]


def _to_goal(row: dict[str, Any]) -> Goal:
    return Goal(
        goal_id=str(row["goal_id"]),
        user_id=str(row["user_id"]),
        title=str(row["title"]),
        description=str(row["description"]),
        goal_type=str(row["goal_type"]),
        source=str(row["source"]),
        status=str(row["status"]),
        priority=float(row["priority"]),
        urgency=float(row["urgency"]),
        importance=float(row["importance"]),
        confidence=float(row["confidence"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        expires_at=(
            float(row["expires_at"]) if row["expires_at"] is not None else None
        ),
        parent_goal_id=(str(row["parent_goal_id"]) if row["parent_goal_id"] else None),
        tags=json_loads(row["tags"]) or [],
        success_criteria=json_loads(row["success_criteria"]) or [],
        failure_criteria=json_loads(row["failure_criteria"]) or [],
        progress=float(row["progress"]),
        metadata=json_loads(row["metadata"]) or {},
    )


def _goal_similarity_key(goal: Goal) -> str:
    fp = str(goal.metadata.get("goal_fingerprint", "") or "").strip()
    if fp:
        return fp
    return _goal_fingerprint(goal.user_id, goal.goal_type, goal.title)


class GoalEngine:
    """Persistent lifecycle + scoring engine for goals."""

    def _append_goal_event(
        self,
        user_id: str,
        goal_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        payload = dict(data or {})
        exec_one(
            "INSERT INTO goal_events(goal_id,user_id,event_type,data,ts) VALUES(?,?,?,?,?)",
            (goal_id, user_id, event_type, json_dumps(payload), now_ts()),
        )

    def _append_goal_link(
        self,
        user_id: str,
        goal_id: str,
        link_type: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        exec_one(
            "INSERT INTO goal_links(goal_id,user_id,link_type,entity_type,entity_id,payload,ts) VALUES(?,?,?,?,?,?,?)",
            (
                goal_id,
                user_id,
                link_type,
                entity_type,
                entity_id,
                json_dumps(payload or {}),
                now_ts(),
            ),
        )

    def _get_goal(self, user_id: str, goal_id: str) -> Goal | None:
        row = fetch_one(
            "SELECT * FROM goals WHERE user_id=? AND goal_id=?",
            (user_id, goal_id),
        )
        if not row:
            return None
        return _to_goal(dict(row))

    def _find_similar_open_goal(
        self,
        user_id: str,
        goal_type: str,
        title: str,
    ) -> Goal | None:
        fingerprint = _goal_fingerprint(user_id, goal_type, title)
        from aihub.db.sql_json import json_text_eq

        row = fetch_one(
            f"""
            SELECT * FROM goals
            WHERE user_id=?
              AND status IN ('proposed','active','blocked','scheduled')
              AND {json_text_eq("metadata", "goal_fingerprint")}
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id, fingerprint),
        )
        if row:
            return _to_goal(dict(row))

        row = fetch_one(
            """
            SELECT * FROM goals
            WHERE user_id=?
              AND status IN ('proposed','active','blocked','scheduled')
              AND goal_type=?
              AND lower(title)=lower(?)
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id, goal_type, title),
        )
        if not row:
            return None
        return _to_goal(dict(row))

    def _validate_transition(self, current_status: str, target_status: str) -> None:
        allowed = _ALLOWED_TRANSITIONS.get(current_status, set())
        if target_status not in allowed and current_status != target_status:
            raise ValueError(
                f"invalid goal status transition: {current_status} -> {target_status}"
            )

    def extract_goal_candidates(
        self,
        input_event: dict[str, Any] | None = None,
        memory_context: dict[str, Any] | None = None,
        cognitive_signal: dict[str, Any] | None = None,
        system_conditions: dict[str, Any] | None = None,
        user_id: str = "default",
    ) -> list[GoalCandidate]:
        """Extract goal candidates from event/context without LLM dependencies."""
        event = dict(input_event or {})
        mem = dict(memory_context or {})
        cog = dict(cognitive_signal or {})
        cond = dict(system_conditions or {})

        text = str(event.get("text") or event.get("message") or "").strip()
        text_l = text.lower()

        skip, _ = self._should_skip_goal_extraction(text)
        if skip:
            logger.info(
                "goal.candidates user=%s extracted=0 text=%s memory_total=%d cognitive_action= skipped=simple_meta",
                user_id,
                bool(text),
                int(dict(memory_context or {}).get("total", 0) or 0),
            )
            return []

        candidates: list[GoalCandidate] = []

        if text:
            if any(
                k in text_l
                for k in ["wyszukaj", "sprawdź", "find", "research", "zbadaj"]
            ):
                candidates.append(
                    GoalCandidate(
                        user_id=user_id,
                        title=f"Research: {text[:120]}",
                        description=text,
                        goal_type=GoalType.RESEARCH_GOAL.value,
                        source="user_input",
                        priority=0.75,
                        urgency=0.70,
                        importance=0.72,
                        confidence=0.76,
                        tags=["research", "input"],
                        success_criteria=["at_least_one_result", "at_least_one_fact"],
                        failure_criteria=["repeated_empty_results"],
                        metadata={"query": text},
                    )
                )

            # Avoid substring false positive: "jak działa" ⊂ "jak działasz"
            _info_need = any(k in text_l for k in ("co to", "wyjaśnij", "explain"))
            if re.search(r"(?i)\bjak działa\b", text_l) and not re.search(
                r"(?i)\bjak działa(sz|cie|my)\b", text_l
            ):
                _info_need = True
            if _info_need:
                candidates.append(
                    GoalCandidate(
                        user_id=user_id,
                        title=f"Informacja: {text[:120]}",
                        description=text,
                        goal_type=GoalType.INFORMATION_NEED.value,
                        source="user_input",
                        priority=0.68,
                        urgency=0.60,
                        importance=0.66,
                        confidence=0.70,
                        tags=["information_need"],
                        success_criteria=["context_has_answer"],
                        failure_criteria=["no_context_hits"],
                        metadata={"query": text},
                    )
                )

            if any(k in text_l for k in ["chcę", "zamierzam", "muszę", "planuję"]):
                goal_type = (
                    GoalType.LONG_TERM_GOAL.value
                    if any(
                        k in text_l
                        for k in ["długotermin", "w tym roku", "w przyszłości"]
                    )
                    else GoalType.USER_INTENT_GOAL.value
                )
                candidates.append(
                    GoalCandidate(
                        user_id=user_id,
                        title=f"Intencja użytkownika: {text[:100]}",
                        description=text,
                        goal_type=goal_type,
                        source="user_input",
                        priority=0.70,
                        urgency=0.62,
                        importance=0.74,
                        confidence=0.73,
                        tags=["intent", "user"],
                        success_criteria=["explicit_progress"],
                        failure_criteria=["blocked_by_dependency"],
                        metadata={"intent_text": text},
                    )
                )

        # repeated unresolved or sparse context
        memory_total = int(mem.get("total", 0) or 0)
        if text and memory_total == 0:
            candidates.append(
                GoalCandidate(
                    user_id=user_id,
                    title=f"Uzupełnić brak kontekstu: {text[:100]}",
                    description="Brak trafień w pamięci dla bieżącego zapytania",
                    goal_type=GoalType.INFORMATION_NEED.value,
                    source="memory_gap",
                    priority=0.64,
                    urgency=0.66,
                    importance=0.65,
                    confidence=0.60,
                    tags=["memory_gap"],
                    success_criteria=["memory_total_gt_zero"],
                    failure_criteria=["multiple_empty_attempts"],
                    metadata={"query": text, "memory_total": memory_total},
                )
            )

        # cognitive output based goals
        action = str(cog.get("action_type") or "").strip().lower()
        if action == "research":
            candidates.append(
                GoalCandidate(
                    user_id=user_id,
                    title="Wykonać research z rekomendacji cognitive",
                    description=str(
                        cog.get("reasoning") or "cognitive requested research"
                    ),
                    goal_type=GoalType.RESEARCH_GOAL.value,
                    source="cognitive",
                    priority=0.72,
                    urgency=0.63,
                    importance=0.68,
                    confidence=_clamp01(float(cog.get("confidence", 0.65) or 0.65)),
                    tags=["cognitive"],
                    success_criteria=["research_results"],
                    failure_criteria=["research_error"],
                    metadata={"cognitive_action": action},
                )
            )
        elif action == "learn":
            candidates.append(
                GoalCandidate(
                    user_id=user_id,
                    title="Zaktualizować wiedzę z rekomendacji cognitive",
                    description=str(
                        cog.get("reasoning") or "cognitive requested learning"
                    ),
                    goal_type=GoalType.LEARNING_GOAL.value,
                    source="cognitive",
                    priority=0.66,
                    urgency=0.58,
                    importance=0.67,
                    confidence=_clamp01(float(cog.get("confidence", 0.62) or 0.62)),
                    tags=["learning", "cognitive"],
                    success_criteria=["new_fact_saved"],
                    failure_criteria=["learning_failed"],
                    metadata={"cognitive_action": action},
                )
            )

        # system maintenance candidates
        pressure = float(cond.get("memory_pressure", 0.0) or 0.0)
        if pressure > 0.72 or bool(cond.get("maintenance_due", False)):
            candidates.append(
                GoalCandidate(
                    user_id=user_id,
                    title="Maintenance: odciążyć pamięć i housekeeping",
                    description="Wysoka presja pamięci lub zaplanowana konserwacja",
                    goal_type=GoalType.MAINTENANCE_GOAL.value,
                    source="system",
                    priority=0.62,
                    urgency=min(0.95, 0.45 + pressure * 0.5),
                    importance=0.60,
                    confidence=0.80,
                    tags=["system", "maintenance"],
                    success_criteria=["memory_pressure_lowered"],
                    failure_criteria=["maintenance_repeated_failure"],
                    metadata={"memory_pressure": pressure},
                )
            )

        # deduplicate candidate list by fingerprint
        uniq: dict[str, GoalCandidate] = {}
        for c in candidates:
            fp = _goal_fingerprint(c.user_id, c.goal_type, c.title)
            if fp not in uniq:
                c.metadata = dict(c.metadata)
                c.metadata["goal_fingerprint"] = fp
                uniq[fp] = c

        out = list(uniq.values())
        logger.info(
            "goal.candidates user=%s extracted=%d text=%s memory_total=%d cognitive_action=%s",
            user_id,
            len(out),
            bool(text),
            memory_total,
            action,
        )
        return out

    def create_goal(self, goal: GoalCandidate) -> Goal:
        """Create persistent goal, deduplicating against open similar goals."""
        if goal.goal_type not in {g.value for g in GoalType}:
            raise ValueError(f"unsupported goal_type: {goal.goal_type}")

        existing = self._find_similar_open_goal(
            goal.user_id, goal.goal_type, goal.title
        )
        if existing is not None:
            merged_meta = dict(existing.metadata)
            merged_meta["duplicate_seen_at"] = now_ts()
            self.update_goal(
                GoalUpdate(
                    user_id=goal.user_id,
                    goal_id=existing.goal_id,
                    urgency=max(existing.urgency, _clamp01(goal.urgency)),
                    importance=max(existing.importance, _clamp01(goal.importance)),
                    confidence=max(existing.confidence, _clamp01(goal.confidence)),
                    metadata=merged_meta,
                    reason="deduplicated_candidate",
                )
            )
            logger.info(
                "goal.create deduplicated user=%s goal_id=%s title=%s",
                goal.user_id,
                existing.goal_id,
                existing.title,
            )
            return self._get_goal(goal.user_id, existing.goal_id) or existing

        created = now_ts()
        goal_id = _goal_id(goal.user_id, goal.goal_type, goal.title)
        priority = _clamp01(goal.priority)
        urgency = _clamp01(goal.urgency)
        importance = _clamp01(goal.importance)
        confidence = _clamp01(goal.confidence)

        metadata = dict(goal.metadata or {})
        metadata.setdefault(
            "goal_fingerprint",
            _goal_fingerprint(goal.user_id, goal.goal_type, goal.title),
        )

        exec_one(
            """
            INSERT INTO goals(
                goal_id,user_id,title,description,goal_type,source,status,
                priority,urgency,importance,confidence,
                created_at,updated_at,expires_at,parent_goal_id,
                tags,success_criteria,failure_criteria,progress,metadata
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                goal_id,
                goal.user_id,
                goal.title,
                goal.description,
                goal.goal_type,
                goal.source,
                GoalStatus.PROPOSED.value,
                priority,
                urgency,
                importance,
                confidence,
                created,
                created,
                goal.expires_at,
                goal.parent_goal_id,
                json_dumps(goal.tags or []),
                json_dumps(goal.success_criteria or []),
                json_dumps(goal.failure_criteria or []),
                0.0,
                json_dumps(metadata),
            ),
        )
        self._append_goal_event(
            goal.user_id,
            goal_id,
            "created",
            {
                "title": goal.title,
                "goal_type": goal.goal_type,
                "source": goal.source,
                "priority": priority,
                "urgency": urgency,
                "importance": importance,
                "confidence": confidence,
            },
        )

        created_goal = self._get_goal(goal.user_id, goal_id)
        if created_goal is None:
            raise RuntimeError("failed to create goal")

        logger.info(
            "goal.create user=%s goal_id=%s type=%s title=%s",
            goal.user_id,
            goal_id,
            goal.goal_type,
            goal.title,
        )
        return created_goal

    def activate_goal(self, user_id: str, goal_id: str) -> Goal:
        return self.update_goal(
            GoalUpdate(
                user_id=user_id,
                goal_id=goal_id,
                status=GoalStatus.ACTIVE.value,
                reason="activate",
            )
        )

    def update_goal(self, update: GoalUpdate) -> Goal:
        goal = self._get_goal(update.user_id, update.goal_id)
        if goal is None:
            raise KeyError(f"goal not found: {update.goal_id}")

        current_status = goal.status
        target_status = update.status or current_status
        self._validate_transition(current_status, target_status)

        next_priority = (
            goal.priority if update.priority is None else _clamp01(update.priority)
        )
        next_urgency = (
            goal.urgency if update.urgency is None else _clamp01(update.urgency)
        )
        next_importance = (
            goal.importance
            if update.importance is None
            else _clamp01(update.importance)
        )
        next_confidence = (
            goal.confidence
            if update.confidence is None
            else _clamp01(update.confidence)
        )
        next_progress = (
            goal.progress if update.progress is None else _clamp01(update.progress)
        )

        next_meta = dict(goal.metadata)
        if update.metadata is not None:
            next_meta.update(update.metadata)

        ts = now_ts()
        exec_one(
            """
            UPDATE goals
            SET status=?, priority=?, urgency=?, importance=?, confidence=?,
                progress=?, expires_at=?, metadata=?, updated_at=?
            WHERE user_id=? AND goal_id=?
            """,
            (
                target_status,
                next_priority,
                next_urgency,
                next_importance,
                next_confidence,
                next_progress,
                (goal.expires_at if update.expires_at is None else update.expires_at),
                json_dumps(next_meta),
                ts,
                update.user_id,
                update.goal_id,
            ),
        )

        self._append_goal_event(
            update.user_id,
            update.goal_id,
            "updated",
            {
                "reason": update.reason,
                "from_status": current_status,
                "to_status": target_status,
                "progress": next_progress,
                "priority": next_priority,
                "urgency": next_urgency,
                "importance": next_importance,
                "confidence": next_confidence,
            },
        )

        updated = self._get_goal(update.user_id, update.goal_id)
        if updated is None:
            raise RuntimeError("failed to update goal")

        logger.info(
            "goal.update user=%s goal_id=%s status=%s progress=%.3f",
            update.user_id,
            update.goal_id,
            updated.status,
            updated.progress,
        )
        return updated

    def complete_goal(
        self, user_id: str, goal_id: str, reason: str = "completed"
    ) -> Goal:
        updated = self.update_goal(
            GoalUpdate(
                user_id=user_id,
                goal_id=goal_id,
                status=GoalStatus.COMPLETED.value,
                progress=1.0,
                reason=reason,
            )
        )
        logger.info(
            "goal.complete user=%s goal_id=%s reason=%s",
            user_id,
            goal_id,
            reason,
        )
        return updated

    def fail_goal(self, user_id: str, goal_id: str, reason: str = "failed") -> Goal:
        updated = self.update_goal(
            GoalUpdate(
                user_id=user_id,
                goal_id=goal_id,
                status=GoalStatus.FAILED.value,
                reason=reason,
            )
        )
        logger.info(
            "goal.fail user=%s goal_id=%s reason=%s",
            user_id,
            goal_id,
            reason,
        )
        return updated

    def expire_goals(self, user_id: str) -> int:
        rows = fetch_all(
            """
            SELECT goal_id FROM goals
            WHERE user_id=?
              AND status IN ('proposed','active','blocked','scheduled')
              AND expires_at IS NOT NULL
              AND expires_at < ?
            """,
            (user_id, now_ts()),
        )
        expired = 0
        for r in rows:
            goal_id = str(r["goal_id"])
            try:
                self.update_goal(
                    GoalUpdate(
                        user_id=user_id,
                        goal_id=goal_id,
                        status=GoalStatus.EXPIRED.value,
                        reason="expired_by_time",
                    )
                )
                expired += 1
            except (ValueError, KeyError):
                logger.debug("goal.expire skip invalid transition goal_id=%s", goal_id)

        if expired:
            logger.info("goal.expire user=%s expired=%d", user_id, expired)
        return expired

    def get_active_goals(self, user_id: str) -> list[Goal]:
        rows = fetch_all(
            """
            SELECT * FROM goals
            WHERE user_id=? AND status IN ('active','scheduled','blocked')
            ORDER BY urgency DESC, priority DESC, updated_at DESC
            """,
            (user_id,),
        )
        return [_to_goal(dict(r)) for r in rows]

    def get_actionable_goals(self, user_id: str) -> list[Goal]:
        """Active goals that should influence routing (excludes meta/small-talk junk)."""
        return [g for g in self.get_active_goals(user_id) if is_actionable_goal(g)]

    def cleanup_non_actionable_goals(
        self,
        user_id: str,
        *,
        dry_run: bool = True,
        include_proposed: bool = True,
    ) -> dict[str, Any]:
        """Cancel junk meta/small-talk goals. Idempotent; never hard-deletes."""
        statuses = ["active", "scheduled", "blocked"]
        if include_proposed:
            statuses.append("proposed")
        goals = self.list_goals(user_id, statuses=statuses, limit=2000)
        scanned = len(goals)
        matched: list[dict[str, Any]] = []
        cancelled: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for g in goals:
            should, reason = classify_goal_cleanup(g)
            entry = {
                "goal_id": g.goal_id,
                "title": (g.title or "")[:120],
                "goal_type": g.goal_type,
                "status": g.status,
                "reason": reason,
            }
            if not should:
                skipped.append(entry)
                continue
            matched.append(entry)
            if dry_run:
                continue
            meta = dict(g.metadata or {})
            meta.update(
                {
                    "cleanup_reason": GOAL_CLEANUP_REASON,
                    "cleanup_version": GOAL_CLEANUP_VERSION,
                    "cleaned_at": now_ts(),
                    "non_actionable": True,
                    "previous_status": g.status,
                }
            )
            try:
                self.update_goal(
                    GoalUpdate(
                        user_id=user_id,
                        goal_id=g.goal_id,
                        status=GoalStatus.CANCELLED.value,
                        reason=GOAL_CLEANUP_REASON,
                        metadata=meta,
                    )
                )
                cancelled.append(entry)
            except (ValueError, KeyError) as exc:
                skipped.append({**entry, "reason": f"transition_blocked:{exc}"})
        return {
            "user_id": user_id,
            "dry_run": dry_run,
            "scanned": scanned,
            "matched": len(matched),
            "cancelled": len(cancelled) if not dry_run else 0,
            "would_cancel": len(matched) if dry_run else 0,
            "skipped": len(skipped),
            "matched_goals": matched,
            "cancelled_goals": cancelled,
            "skipped_goals": skipped,
            "cleanup_version": GOAL_CLEANUP_VERSION,
        }

    def list_goals(
        self,
        user_id: str,
        statuses: list[str] | None = None,
        goal_type: str | None = None,
        limit: int = 200,
    ) -> list[Goal]:
        """List goals for a user with optional status/type filtering."""
        safe_limit = max(1, min(int(limit), 2000))
        conditions = ["user_id=?"]
        params: list[Any] = [user_id]

        if statuses:
            normalized = [str(s).strip() for s in statuses if str(s).strip()]
            if normalized:
                bind_marks = ",".join(["?"] * len(normalized))
                conditions.append(f"status IN ({bind_marks})")
                params.extend(normalized)

        if goal_type:
            conditions.append("goal_type=?")
            params.append(str(goal_type).strip())

        where_sql = " AND ".join(conditions)
        rows = fetch_all(
            f"""
            SELECT * FROM goals
            WHERE {where_sql}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            tuple(params + [safe_limit]),
        )
        return [_to_goal(dict(r)) for r in rows]

    def get_stale_goals(
        self,
        user_id: str,
        older_than_seconds: float = 24 * 3600,
    ) -> list[Goal]:
        """Return open goals that were not updated for a given age threshold."""
        cutoff = now_ts() - max(1.0, float(older_than_seconds))
        rows = fetch_all(
            """
            SELECT * FROM goals
            WHERE user_id=?
              AND status IN ('proposed','active','blocked','scheduled')
              AND updated_at < ?
            ORDER BY updated_at ASC
            """,
            (user_id, cutoff),
        )
        return [_to_goal(dict(r)) for r in rows]

    def get_goal_events(
        self,
        user_id: str,
        goal_id: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return ordered event history for one goal."""
        rows = fetch_all(
            """
            SELECT id, event_type, data, ts
            FROM goal_events
            WHERE user_id=? AND goal_id=?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (user_id, goal_id, max(1, min(int(limit), 2000))),
        )
        return [
            {
                "id": int(r["id"]),
                "event_type": str(r["event_type"]),
                "data": json_loads(r["data"]) or {},
                "ts": float(r["ts"]),
            }
            for r in rows
        ]

    def get_goal_links(
        self,
        user_id: str,
        goal_id: str,
        link_type: str | None = None,
        entity_type: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return goal link artifacts for observability/tracing."""
        conditions = ["user_id=?", "goal_id=?"]
        params: list[Any] = [user_id, goal_id]

        if link_type:
            conditions.append("link_type=?")
            params.append(str(link_type).strip())

        if entity_type:
            conditions.append("entity_type=?")
            params.append(str(entity_type).strip())

        where_sql = " AND ".join(conditions)
        rows = fetch_all(
            f"""
            SELECT id, link_type, entity_type, entity_id, payload, ts
            FROM goal_links
            WHERE {where_sql}
            ORDER BY ts DESC, id DESC
            LIMIT ?
            """,
            tuple(params + [max(1, min(int(limit), 5000))]),
        )
        return [
            {
                "id": int(r["id"]),
                "link_type": str(r["link_type"]),
                "entity_type": str(r["entity_type"]),
                "entity_id": str(r["entity_id"]),
                "payload": json_loads(r["payload"]) or {},
                "ts": float(r["ts"]),
            }
            for r in rows
        ]

    def get_goal_trace(
        self,
        user_id: str,
        goal_id: str,
        limit_events: int = 200,
        limit_links: int = 500,
    ) -> dict[str, Any]:
        """Return complete trace for one goal (goal state + events + links)."""
        goal = self._get_goal(user_id, goal_id)
        if goal is None:
            return {
                "ok": False,
                "error": "goal_not_found",
                "goal_id": goal_id,
                "events": [],
                "links": [],
            }

        return {
            "ok": True,
            "goal": {
                "goal_id": goal.goal_id,
                "title": goal.title,
                "goal_type": goal.goal_type,
                "status": goal.status,
                "progress": goal.progress,
                "priority": goal.priority,
                "urgency": goal.urgency,
                "importance": goal.importance,
                "confidence": goal.confidence,
                "updated_at": goal.updated_at,
            },
            "events": self.get_goal_events(
                user_id=user_id,
                goal_id=goal_id,
                limit=limit_events,
            ),
            "links": self.get_goal_links(
                user_id=user_id,
                goal_id=goal_id,
                limit=limit_links,
            ),
        }

    def score_goal(
        self,
        goal: Goal,
        context_signals: dict[str, Any] | None = None,
        has_similar_open: bool = False,
    ) -> GoalScore:
        signals = dict(context_signals or {})
        age_s = max(0.0, now_ts() - float(goal.updated_at))
        recency = max(0.0, 1.0 - (age_s / (7.0 * 86400.0)))

        memory_total = float(signals.get("memory_total", 0.0) or 0.0)
        knowledge_hits = float(signals.get("knowledge_hits", 0.0) or 0.0)
        urgency_signal = _clamp01(
            float(signals.get("cognitive_confidence", 0.0) or 0.0)
        )
        energy = _clamp01(float(signals.get("energy", 0.5) or 0.5))

        alignment = 0.0
        if goal.goal_type in {
            GoalType.INFORMATION_NEED.value,
            GoalType.RESEARCH_GOAL.value,
        }:
            if memory_total == 0:
                alignment += 0.12
            if knowledge_hits == 0:
                alignment += 0.06
        if goal.goal_type in {
            GoalType.LONG_TERM_GOAL.value,
            GoalType.USER_INTENT_GOAL.value,
        }:
            alignment += 0.05

        blocked_penalty = -0.28 if goal.status == GoalStatus.BLOCKED.value else 0.0
        duplicate_penalty = -0.14 if has_similar_open else 0.0
        long_term_bonus = (
            0.08 if goal.goal_type == GoalType.LONG_TERM_GOAL.value else 0.0
        )

        breakdown = {
            "priority": 0.22 * _clamp01(goal.priority),
            "urgency": 0.20 * _clamp01(goal.urgency),
            "importance": 0.20 * _clamp01(goal.importance),
            "confidence": 0.14 * _clamp01(goal.confidence),
            "recency": 0.10 * recency,
            "alignment": 0.08 * _clamp01(alignment),
            "psyche_urgency": 0.04 * _clamp01((urgency_signal + energy) / 2.0),
            "blocked_penalty": blocked_penalty,
            "duplicate_penalty": duplicate_penalty,
            "long_term_bonus": long_term_bonus,
        }

        score_value = _clamp01(sum(breakdown.values()))
        reasons = [
            f"priority={goal.priority:.2f}",
            f"urgency={goal.urgency:.2f}",
            f"importance={goal.importance:.2f}",
            f"recency={recency:.2f}",
        ]
        if blocked_penalty < 0:
            reasons.append("blocked_penalty")
        if long_term_bonus > 0:
            reasons.append("long_term_bonus")

        return GoalScore(
            goal_id=goal.goal_id,
            score=score_value,
            breakdown=breakdown,
            reasons=reasons,
        )

    def get_top_goals(
        self,
        user_id: str,
        limit: int = 5,
        context_signals: dict[str, Any] | None = None,
    ) -> list[GoalScore]:
        goals = self.get_actionable_goals(user_id)

        similarity_counts: dict[str, int] = {}
        for goal in goals:
            key = _goal_similarity_key(goal)
            similarity_counts[key] = similarity_counts.get(key, 0) + 1

        scores = [
            self.score_goal(
                g,
                context_signals=context_signals,
                has_similar_open=similarity_counts.get(_goal_similarity_key(g), 0) > 1,
            )
            for g in goals
        ]
        scores.sort(key=lambda s: s.score, reverse=True)
        top_scores = scores[: max(1, int(limit))]

        if top_scores:
            logger.info(
                "goal.score user=%s ranked=%d top=%s",
                user_id,
                len(top_scores),
                [
                    {
                        "goal_id": s.goal_id,
                        "score": round(s.score, 4),
                        "breakdown": s.breakdown,
                    }
                    for s in top_scores[:3]
                ],
            )
        return top_scores

    def select_goal_for_cycle(
        self,
        user_id: str,
        context_signals: dict[str, Any] | None = None,
    ) -> tuple[Goal | None, str, list[GoalScore]]:
        active = self.get_actionable_goals(user_id)
        if not active:
            return None, "no actionable goals", []

        by_id = {g.goal_id: g for g in active}
        scores = self.get_top_goals(
            user_id, limit=max(5, len(active)), context_signals=context_signals
        )
        if not scores:
            return None, "no scored goals", []

        top = scores[0]
        selected = by_id.get(top.goal_id)
        if selected is None:
            return None, "scored goal not found in active set", scores

        top_component = (
            max(top.breakdown, key=lambda k: top.breakdown[k])
            if top.breakdown
            else "none"
        )
        reason = (
            f"selected goal {selected.goal_id} score={top.score:.3f} "
            f"top_component={top_component}"
        )
        logger.info(
            "goal.select user=%s goal_id=%s score=%.4f reason=%s breakdown=%s",
            user_id,
            selected.goal_id,
            top.score,
            reason,
            top.breakdown,
        )
        return selected, reason, scores

    @staticmethod
    def _should_skip_goal_extraction(text: str) -> tuple[bool, str]:
        """Skip persistent goals for simple meta / greeting / small-talk turns."""
        from aihub.strategy_selector import is_assistant_meta_ask, is_simple_greeting

        raw = (text or "").strip()
        if not raw:
            return True, "GOAL_SKIPPED_EMPTY"
        lower = raw.lower().rstrip("!?., ")
        if is_assistant_meta_ask(raw):
            return True, "GOAL_SKIPPED_SIMPLE_META"
        if is_simple_greeting(raw):
            return True, "GOAL_SKIPPED_GREETING"
        n_words = len([w for w in raw.split() if w])
        if n_words <= 3 and lower in _SMALLTALK_EXACT:
            return True, "GOAL_SKIPPED_GREETING"
        if n_words <= 4 and lower in {
            "dzięki",
            "dzieki",
            "dziękuję",
            "dziekuje",
            "thanks",
            "thx",
            "ok",
            "okej",
            "spoko",
            "git",
            "super",
            "lol",
            "haha",
            "jasne",
            "rozumiem",
            "dobra",
        }:
            return True, "GOAL_SKIPPED_GREETING"
        if n_words <= 6 and any(
            p in lower
            for p in (
                "kim jesteś",
                "kim jestes",
                "jak działasz",
                "jak dzialasz",
                "powiedz krótko",
                "powiedz krotko",
            )
        ):
            return True, "GOAL_SKIPPED_SIMPLE_META"
        if re.search(r"(?i)^zapami[eę]taj\b", raw):
            return True, "GOAL_SKIPPED_MEMORY_STORE"
        if re.search(
            r"(?i)(jak nazywa|czego nie lubi|jaki jest m[oó]j|m[oó]j pies|m[oó]j kod projektu)",
            lower,
        ):
            return True, "GOAL_SKIPPED_MEMORY_RECALL"
        if lower in {"elo", "hej", "cześć", "czesc", "siema", "no i co tam u ciebie?"}:
            return True, "GOAL_SKIPPED_GREETING"
        return False, ""

    def build_goal_context(
        self,
        user_id: str,
        input_event: dict[str, Any] | None = None,
        memory_context: dict[str, Any] | None = None,
        cognitive_signal: dict[str, Any] | None = None,
        system_conditions: dict[str, Any] | None = None,
    ) -> GoalContext:
        self.expire_goals(user_id)

        event = dict(input_event or {})
        text = str(event.get("text") or event.get("message") or "").strip()
        skip, skip_reason = self._should_skip_goal_extraction(text)
        if skip:
            active_goals = self.get_actionable_goals(user_id)
            logger.info(
                "goal.context user=%s active=%d candidates=0 selected= reason=%s",
                user_id,
                len(active_goals),
                skip_reason,
            )
            return GoalContext(
                user_id=user_id,
                active_goals=active_goals,
                top_scores=[],
                selected_goal=None,
                selected_reason=skip_reason,
                execution_hint=None,
                candidates=[],
                created_goal_ids=[],
            )

        candidates = self.extract_goal_candidates(
            input_event=input_event,
            memory_context=memory_context,
            cognitive_signal=cognitive_signal,
            system_conditions=system_conditions,
            user_id=user_id,
        )

        created_goal_ids: list[str] = []
        for c in candidates:
            if is_non_actionable_meta_or_smalltalk_goal(c):
                continue
            g = self.create_goal(c)
            created_goal_ids.append(g.goal_id)
            if g.status == GoalStatus.PROPOSED.value and c.confidence >= 0.5:
                try:
                    self.activate_goal(user_id, g.goal_id)
                except ValueError:
                    logger.debug("goal.activate skipped for %s", g.goal_id)

        active_goals = self.get_actionable_goals(user_id)
        context_signals = {
            "memory_total": int((memory_context or {}).get("total", 0) or 0),
            "knowledge_hits": len((memory_context or {}).get("graph_hits", []) or []),
            "energy": float((system_conditions or {}).get("energy", 0.5) or 0.5),
            "cognitive_confidence": float(
                (cognitive_signal or {}).get("confidence", 0.0) or 0.0
            ),
        }

        selected, selected_reason, top_scores = self.select_goal_for_cycle(
            user_id,
            context_signals=context_signals,
        )

        hint: GoalExecutionHint | None = None
        if selected is not None:
            if selected.goal_type in {
                GoalType.RESEARCH_GOAL.value,
                GoalType.INFORMATION_NEED.value,
            }:
                hint = GoalExecutionHint(
                    goal_id=selected.goal_id,
                    recommended_strategy="planned_reasoning",
                    planning_bias="research_heavy",
                    research_intensity="deep"
                    if selected.urgency >= 0.65
                    else "general",
                    create_followups=True,
                )
            elif selected.goal_type in {
                GoalType.TASK.value,
                GoalType.USER_INTENT_GOAL.value,
                GoalType.LONG_TERM_GOAL.value,
            }:
                hint = GoalExecutionHint(
                    goal_id=selected.goal_id,
                    recommended_strategy="planned_reasoning",
                    planning_bias="task_focused",
                    research_intensity="general",
                    create_followups=True,
                )
            elif selected.goal_type == GoalType.MAINTENANCE_GOAL.value:
                hint = GoalExecutionHint(
                    goal_id=selected.goal_id,
                    recommended_strategy="reactive_tick",
                    planning_bias="maintenance",
                    research_intensity="light",
                    create_followups=False,
                )
            else:
                hint = GoalExecutionHint(
                    goal_id=selected.goal_id,
                    recommended_strategy="cognitive_direct",
                    planning_bias="balanced",
                    research_intensity="general",
                    create_followups=False,
                )

        logger.info(
            "goal.context user=%s active=%d candidates=%d selected=%s reason=%s",
            user_id,
            len(active_goals),
            len(candidates),
            (selected.goal_id if selected else ""),
            selected_reason,
        )

        return GoalContext(
            user_id=user_id,
            active_goals=active_goals,
            top_scores=top_scores,
            selected_goal=selected,
            selected_reason=selected_reason,
            execution_hint=hint,
            candidates=candidates,
            created_goal_ids=created_goal_ids,
        )

    def link_goal_to_result(
        self,
        user_id: str,
        goal_id: str,
        execution_result: dict[str, Any],
        execution_plan: dict[str, Any] | None = None,
        memory_links: Iterable[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Attach execution trace to goal and update progress/lifecycle."""
        goal = self._get_goal(user_id, goal_id)
        if goal is None:
            return {"updated": False, "reason": "goal_not_found"}

        plan = dict(execution_plan or {})
        result = dict(execution_result or {})

        # Link tasks from execution plan
        tasks = plan.get("tasks", [])
        if isinstance(tasks, list):
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                task_id = str(task.get("task_id") or "")
                if not task_id:
                    continue
                self._append_goal_link(
                    user_id,
                    goal_id,
                    link_type="planner_task",
                    entity_type="task",
                    entity_id=task_id,
                    payload={"task_type": task.get("task_type", "")},
                )

        payload = result.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        cycle_id = str(
            payload.get("cycle_id")
            or result.get("cycle_id")
            or plan.get("metadata", {}).get("cycle_id", "")
        ).strip()
        if cycle_id:
            self._append_goal_link(
                user_id,
                goal_id,
                link_type="execution_result",
                entity_type="cycle",
                entity_id=cycle_id,
                payload={
                    "ok": bool(result.get("ok", False)),
                    "strategy": str(result.get("strategy", "")),
                    "summary": str(result.get("action_summary", "")),
                },
            )

        executed_task_ids = payload.get("executed_task_ids", [])
        if isinstance(executed_task_ids, list):
            for task_id in executed_task_ids:
                tid = str(task_id).strip()
                if not tid:
                    continue
                self._append_goal_link(
                    user_id,
                    goal_id,
                    link_type="executed_task",
                    entity_type="task",
                    entity_id=tid,
                    payload={"source": "execution_result"},
                )

        runtime_generated_ids = payload.get("runtime_generated_task_ids", [])
        if isinstance(runtime_generated_ids, list):
            for task_id in runtime_generated_ids:
                tid = str(task_id).strip()
                if not tid:
                    continue
                self._append_goal_link(
                    user_id,
                    goal_id,
                    link_type="runtime_generated_task",
                    entity_type="task",
                    entity_id=tid,
                    payload={"source": "reasoning_followup"},
                )

        # Automatic memory/fact/episode linkage from execution context when present.
        seen_context_entities: set[tuple[str, str]] = set()
        context_obj = payload.get("context", {})
        history = (
            context_obj.get("history", []) if isinstance(context_obj, dict) else []
        )
        if isinstance(history, list):
            for item in history:
                if not isinstance(item, dict):
                    continue
                task_type = str(item.get("task_type", ""))
                result_item = item.get("result", {})
                if not isinstance(result_item, dict):
                    continue

                node_id = str(result_item.get("node_id", "")).strip()
                if node_id:
                    key = ("memory_node", node_id)
                    if key not in seen_context_entities:
                        self._append_goal_link(
                            user_id,
                            goal_id,
                            link_type="memory_fact",
                            entity_type="memory_node",
                            entity_id=node_id,
                            payload={"task_type": task_type},
                        )
                        seen_context_entities.add(key)

                context_result = result_item.get("context", {})
                if isinstance(context_result, dict):
                    for bucket in (
                        "episodic",
                        "semantic",
                        "stm",
                        "dense_hits",
                        "graph_hits",
                    ):
                        values = context_result.get(bucket, [])
                        if not isinstance(values, list):
                            continue
                        for value in values[:8]:
                            if not isinstance(value, dict):
                                continue
                            entity_id = str(
                                value.get("id") or value.get("node_id") or ""
                            ).strip()
                            if not entity_id:
                                continue
                            entity_type = (
                                "knowledge_node"
                                if bucket == "graph_hits"
                                else "memory_node"
                            )
                            key = (entity_type, entity_id)
                            if key in seen_context_entities:
                                continue
                            self._append_goal_link(
                                user_id,
                                goal_id,
                                link_type="context_entity",
                                entity_type=entity_type,
                                entity_id=entity_id,
                                payload={"bucket": bucket, "task_type": task_type},
                            )
                            seen_context_entities.add(key)

        # Optional memory / knowledge links
        for item in memory_links or []:
            entity_type, entity_id = item
            self._append_goal_link(
                user_id,
                goal_id,
                link_type="context_entity",
                entity_type=str(entity_type),
                entity_id=str(entity_id),
                payload={},
            )

        ok = bool(result.get("ok", False))
        previous_progress = float(goal.progress)

        progress_delta = 0.0
        strategy = str(result.get("strategy") or "")

        if ok:
            if strategy == "planned_reasoning":
                steps_executed = float(payload.get("steps_executed", 0) or 0)
                tasks_total = float(
                    payload.get("planner_summary", {}).get("tasks_total", 1) or 1
                )
                progress_delta = min(0.6, steps_executed / max(1.0, tasks_total) * 0.5)
            elif strategy == "reactive_tick":
                ran = float(payload.get("ran", 0) or 0)
                processed = float(payload.get("processed", 0) or 0)
                progress_delta = min(0.35, ran * 0.08 + processed * 0.02)
            elif strategy == "cognitive_direct":
                decisions = float(payload.get("decisions_made", 0) or 0)
                progress_delta = min(0.25, decisions * 0.12)
            else:
                progress_delta = 0.1

            new_progress = _clamp01(previous_progress + progress_delta)

            success_criteria_met = False
            if new_progress >= 0.999:
                success_criteria_met = True
            if (
                "steps_executed" in payload
                and int(payload.get("steps_executed", 0) or 0) > 0
            ):
                success_criteria_met = success_criteria_met or new_progress >= 0.8

            if success_criteria_met:
                completed = self.complete_goal(
                    user_id, goal_id, reason="success_criteria_met"
                )
                logger.info(
                    "goal.progress user=%s goal_id=%s before=%.4f after=%.4f status=%s strategy=%s",
                    user_id,
                    goal_id,
                    previous_progress,
                    completed.progress,
                    completed.status,
                    strategy,
                )
                return {
                    "updated": True,
                    "progress_changed": completed.progress != previous_progress,
                    "progress_before": previous_progress,
                    "progress_after": completed.progress,
                    "status": completed.status,
                }

            updated = self.update_goal(
                GoalUpdate(
                    user_id=user_id,
                    goal_id=goal_id,
                    progress=new_progress,
                    confidence=min(1.0, goal.confidence + 0.03),
                    reason="progress_update",
                    metadata={
                        "last_progress_delta": progress_delta,
                        "last_strategy": strategy,
                    },
                )
            )
            logger.info(
                "goal.progress user=%s goal_id=%s before=%.4f after=%.4f status=%s strategy=%s",
                user_id,
                goal_id,
                previous_progress,
                updated.progress,
                updated.status,
                strategy,
            )
            return {
                "updated": True,
                "progress_changed": updated.progress != previous_progress,
                "progress_before": previous_progress,
                "progress_after": updated.progress,
                "status": updated.status,
            }

        # failure path
        meta = dict(goal.metadata)
        error_count = int(meta.get("error_count", 0) or 0) + 1
        meta["error_count"] = error_count
        meta["last_error"] = str(result.get("errors", "execution_error"))

        if error_count >= 3:
            failed = self.fail_goal(user_id, goal_id, reason="error_threshold_reached")
            logger.info(
                "goal.progress_failure user=%s goal_id=%s errors=%d status=%s",
                user_id,
                goal_id,
                error_count,
                failed.status,
            )
            return {
                "updated": True,
                "progress_changed": False,
                "progress_before": previous_progress,
                "progress_after": failed.progress,
                "status": failed.status,
            }

        status = GoalStatus.BLOCKED.value if error_count >= 2 else goal.status
        updated = self.update_goal(
            GoalUpdate(
                user_id=user_id,
                goal_id=goal_id,
                status=status,
                metadata=meta,
                reason="execution_error",
            )
        )
        logger.info(
            "goal.progress_blocked user=%s goal_id=%s errors=%d status=%s",
            user_id,
            goal_id,
            error_count,
            updated.status,
        )
        return {
            "updated": True,
            "progress_changed": False,
            "progress_before": previous_progress,
            "progress_after": updated.progress,
            "status": updated.status,
        }


_goal_engine = GoalEngine()


def get_goal_engine() -> GoalEngine:
    return _goal_engine
