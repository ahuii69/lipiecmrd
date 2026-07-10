#!/usr/bin/env python3

"""Unified runtime orchestrator for AI-Hub.

Why this module exists
----------------------
Historically AI-Hub executed work through three partially separate orchestration
paths:
- planned DAG runtime (`/agent/run`)
- reactive autonomous tick runtime (`agent_tick`)
- cognitive direct runtime (`/agent/loop`)

This controller introduces a single canonical decision cycle so all entrypoints
use one shared orchestration contract while still reusing existing engines.

Canonical cycle
---------------
1) perception/input normalization
2) memory retrieval
3) cognitive evaluation
4) strategy selection
5) execution plan creation
6) execution
7) reflection/result packaging
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from aihub.cognitive_controller import DecisionRequest
from aihub.db import get_experiences_by_user, get_stm, now_ts, write_experience
from aihub.goal_engine import GoalContext, get_goal_engine
from aihub.knowledge_graph import query_nodes
from aihub.memory_core import get_memory_core
from aihub.planner_engine import PlannerEngine
from aihub.psyche_core import get_psyche_core
from aihub.reasoning_engine import run_reasoning_loop
from aihub.strategy_selector import select_strategy
from aihub.task_graph import TaskGraph

logger = logging.getLogger(__name__)


def retrieve_context(user_id: str, query: str, limit: int = 8) -> dict[str, Any]:
    """Planner/tests entry: unified memory read via :func:`aihub.memory_core.get_memory_core`."""

    return get_memory_core().retrieve_unified(user_id, query, limit)


def add_fact(
    user_id: str,
    fact: str,
    tags: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Planner/tests entry: graph L2 write via canonical core."""

    return get_memory_core().ingest_fact(
        user_id, fact, tags=list(tags or []), meta=dict(meta or {})
    )


STRATEGY_PLANNED = "planned_reasoning"
STRATEGY_REACTIVE = "reactive_tick"
STRATEGY_COGNITIVE = "cognitive_direct"
_VALID_STRATEGIES = {STRATEGY_PLANNED, STRATEGY_REACTIVE, STRATEGY_COGNITIVE}

# Who decided the execution strategy (observability; product canon = external from ChatRuntime/client).
STRATEGY_SOURCE_EXTERNAL = "external"
STRATEGY_SOURCE_FALLBACK_LOCAL = "fallback_local"
STRATEGY_SOURCE_WORKER_DEFAULT = "worker_default"

# raw_event["execution_intent_source"] when background worker runs tick (subordinate maintenance path).
EXECUTION_INTENT_WORKER_MAINTENANCE = "worker_maintenance"


def map_chat_execution_mode_to_force_strategy(
    decision_core: dict[str, Any],
) -> tuple[str, str]:
    """Map ChatRuntime decision_core (post-_finalize_escalation) to executive force_strategy.

    Used for handoff and provider-fallback so the executor does not re-decide the path.
    """
    mode = (
        str(
            decision_core.get("execution_mode")
            or decision_core.get("escalation_final_mode")
            or "direct"
        )
        .strip()
        .lower()
    )
    if mode in ("planner", "research"):
        return STRATEGY_PLANNED, f"chat_runtime:execution_mode={mode}"
    if mode == "memory_augmented":
        return STRATEGY_COGNITIVE, f"chat_runtime:execution_mode={mode}"
    return STRATEGY_COGNITIVE, f"chat_runtime:execution_mode={mode}"


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


@dataclass
class PerceptionInput:
    """Normalized input payload for one executive cycle."""

    mode: str
    user_id: str
    raw_event: dict[str, Any]
    text: str = ""
    max_steps: int = 8
    timeout_seconds: float = 20.0
    max_stm: int = 200
    max_tasks: int = 8
    dry_run: bool = False

    @classmethod
    def from_event(
        cls, input_event: dict[str, Any], mode: str, user_id: str | None = None
    ) -> PerceptionInput:
        event = dict(input_event or {})
        normalized_user = str(user_id or event.get("user_id") or "default")
        text = str(event.get("text") or event.get("message") or "").strip()

        return cls(
            mode=str(mode or "run").strip().lower(),
            user_id=normalized_user,
            raw_event=event,
            text=text,
            max_steps=max(1, int(event.get("max_steps", 8))),
            timeout_seconds=max(1.0, float(event.get("timeout_seconds", 20.0))),
            max_stm=max(1, int(event.get("max_stm", 200))),
            max_tasks=max(1, int(event.get("max_tasks", 8))),
            dry_run=bool(event.get("dry_run", False)),
        )

    def strategy_authority_external(self) -> bool:
        """True when subscriber (e.g. ChatRuntime) pinned strategy via force_strategy."""
        forced = str(self.raw_event.get("force_strategy") or "").strip()
        return forced in _VALID_STRATEGIES


def resolve_strategy_source(perception: PerceptionInput) -> str:
    """Classify strategy provenance for trace/API (not a second routing decision)."""
    if perception.strategy_authority_external():
        return STRATEGY_SOURCE_EXTERNAL
    if str(perception.raw_event.get("execution_intent_source") or "").strip() == (
        EXECUTION_INTENT_WORKER_MAINTENANCE
    ):
        return STRATEGY_SOURCE_WORKER_DEFAULT
    return STRATEGY_SOURCE_FALLBACK_LOCAL


@dataclass
class DecisionContext:
    """Canonical decision context passed through strategies."""

    user_id: str
    mode: str
    perception: PerceptionInput
    memory_context: dict[str, Any]
    knowledge_context: dict[str, Any]
    psyche_state: dict[str, Any]
    goal_context: GoalContext | None = None
    cognitive_signal: dict[str, Any] = field(default_factory=dict)
    context_signals: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlannedTask:
    """Plan task in normalized schema."""

    task_id: str
    task_type: str
    payload: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)
    priority: int = 50


@dataclass
class ExecutionPlan:
    """Unified execution plan description."""

    strategy: str
    strategy_reason: str
    # Intention-level flags (selected branch), not execution facts.
    planning_used: bool
    reasoning_used: bool
    tasks: list[PlannedTask] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """Normalized execution output for all strategies."""

    ok: bool
    strategy: str
    action_summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReflectionRecord:
    """Per-cycle trace for debugging and observability."""

    mode: str
    strategy: str
    strategy_reason: str
    planning_used: bool
    reasoning_used: bool
    memory_hits: dict[str, int]
    context_signals: dict[str, Any]
    duration_ms: float
    action_summary: str
    errors: list[dict[str, Any]] = field(default_factory=list)
    ts: float = field(default_factory=now_ts)


def _pl(n: int, one: str, few: str, many: str) -> str:
    """Polish noun inflection: _pl(1,'krok','kroki','kroków') → '1 krok'."""
    last2 = abs(n) % 100
    last1 = abs(n) % 10
    if n == 1:
        return f"{n} {one}"
    if 10 <= last2 <= 20:
        return f"{n} {many}"
    if 2 <= last1 <= 4:
        return f"{n} {few}"
    return f"{n} {many}"


def _build_agent_response_text(cycle: dict[str, Any]) -> str:
    """User-facing cycle summary. V2: richer signals, short and product-grade."""
    mode = str(cycle.get("mode") or "")
    ok = bool(cycle.get("ok", False))
    strategy = str(cycle.get("strategy") or "")
    selected_goal = cycle.get("selected_goal")
    active_goals = _safe_list(cycle.get("active_goals_summary"))
    execution_result = _safe_dict(cycle.get("execution_result"))
    exec_payload = _safe_dict(execution_result.get("payload"))
    errors = _safe_list(execution_result.get("errors"))
    reflection = _safe_dict(cycle.get("reflection"))
    memory_hits = _safe_dict(reflection.get("memory_hits"))
    memory_total = int(memory_hits.get("total", 0) or 0)
    goal_progress_changed = bool(cycle.get("goal_progress_changed", False))

    goal_title = ""
    goal_progress = 0.0
    if isinstance(selected_goal, dict):
        goal_title = str(selected_goal.get("title") or "").strip()
        goal_progress = float(selected_goal.get("progress", 0.0) or 0.0)

    active_count = len(active_goals)

    if not ok:
        if errors:
            err_msg = str((_safe_dict(errors[0])).get("error") or "")
            if err_msg:
                return f"Cykl zakończony niepowodzeniem: {err_msg[:80]}"
        return "Cykl agenta zakończony niepowodzeniem."

    # ── TICK ────────────────────────────────────────────────────────────────
    if mode == "tick":
        processed = int(exec_payload.get("processed", 0) or 0)
        enqueued = int(exec_payload.get("enqueued", 0) or 0)
        ran = int(exec_payload.get("ran", 0) or 0)
        parts: list[str] = []
        if processed:
            parts.append(
                f"Przetworzono {_pl(processed, 'sygnał', 'sygnały', 'sygnałów')} STM."
            )
        else:
            parts.append("Brak nowych sygnałów STM.")
        if goal_title:
            pct = int(goal_progress * 100)
            parts.append(f'Aktywny cel: "{goal_title[:60]}" ({pct}%).')
        elif active_count:
            parts.append(f"Aktywnych celów: {active_count}.")
        else:
            parts.append("Brak aktywnych celów.")
        if ran:
            parts.append(f"Wykonano {_pl(ran, 'zadanie', 'zadania', 'zadań')}.")
        elif enqueued:
            parts.append(f"W kolejce: {_pl(enqueued, 'zadanie', 'zadania', 'zadań')}.")
        return " ".join(parts)

    # ── RUN / LOOP ───────────────────────────────────────────────────────────
    steps = int(exec_payload.get("steps_executed", 0) or 0)
    timed_out = bool(exec_payload.get("timed_out", False))
    num_errors = len(errors)

    if num_errors:
        err_part = f' Cel: "{goal_title[:60]}".' if goal_title else ""
        return f"Cykl zakończony z {_pl(num_errors, 'błędem', 'błędami', 'błędami')}.{err_part}"

    if timed_out:
        time_part = f' Cel: "{goal_title[:60]}".' if goal_title else ""
        return f"Cykl rozumowania przekroczył limit czasu ({_pl(steps, 'krok', 'kroki', 'kroków')}).{time_part}"

    parts = []
    if strategy in {"planned_reasoning", "planned"}:
        if steps:
            if goal_title:
                pct = int(goal_progress * 100)
                verb = "Postęp zaktualizowany" if goal_progress_changed else "Postęp"
                parts.append(
                    f"Zrealizowałem {_pl(steps, 'krok', 'kroki', 'kroków')} planu."
                )
                parts.append(f'Cel: "{goal_title[:60]}". {verb}: {pct}%.')
            else:
                parts.append(
                    f"Zrealizowałem {_pl(steps, 'krok', 'kroki', 'kroków')} rozumowania."
                )
                if memory_total:
                    parts.append(f"Kontekst pamięci: {memory_total} el.")
        else:
            if goal_title:
                parts.append(f'Plan gotowy dla celu "{goal_title[:60]}".')
                parts.append("Kroki oczekują na realizację.")
            else:
                parts.append("Plan gotowy. Kroki oczekują na realizację.")
    else:
        # reactive / cognitive
        if goal_title:
            pct = int(goal_progress * 100)
            parts.append(f'Cykl zakończony. Cel: "{goal_title[:60]}" ({pct}%).')
        elif active_count:
            parts.append(f"Cykl zakończony. Aktywnych celów: {active_count}.")
        else:
            parts.append("Cykl agenta zakończony.")
        if memory_total:
            parts.append(f"Kontekst: {memory_total} elementów pamięci.")

    return " ".join(parts) if parts else "Cykl agenta zakończony."


def build_agent_cycle_response(
    cycle: dict[str, Any], *, include_debug: bool = False
) -> dict[str, Any]:
    """Canonical, truthful response contract for one cycle (/agent/run|tick).

    Semantics:
    - strategy/strategy_reason describe selected runtime intent.
    - planning_used/reasoning_used describe executed behavior (facts).
    - execution_summary.execution_flags captures attempted vs executed details.
    """
    execution_result = _safe_dict(cycle.get("execution_result"))
    execution_payload = _safe_dict(execution_result.get("payload"))
    reflection = _safe_dict(cycle.get("reflection"))
    execution_plan = _safe_dict(cycle.get("execution_plan"))
    context_signals = _safe_dict(cycle.get("context_signals"))

    planning_attempted = bool(cycle.get("planning_attempted", False))
    planning_executed = bool(
        cycle.get("planning_executed", cycle.get("planning_used", False))
    )
    reasoning_attempted = bool(cycle.get("reasoning_attempted", False))
    reasoning_executed = bool(
        cycle.get("reasoning_executed", cycle.get("reasoning_used", False))
    )

    lw_records = _safe_list(execution_payload.get("planner_task_records"))
    plan_src = str(execution_payload.get("plan_source") or "")
    is_lw = plan_src == "planner_lightweight"
    lw_planned_ids = _safe_list(execution_payload.get("planned_task_ids"))
    lw_executed_ids = _safe_list(execution_payload.get("executed_task_ids"))
    runtime_generated_ids = _safe_list(
        execution_payload.get("runtime_generated_task_ids")
    )

    trace = {
        "cycle_id": str(cycle.get("cycle_id") or ""),
        "plan_task_ids": [
            str(t.get("task_id"))
            for t in _safe_list(execution_plan.get("tasks"))
            if isinstance(t, dict) and t.get("task_id")
        ],
        "executed_task_ids": [
            str(tid)
            for tid in _safe_list(execution_payload.get("executed_task_ids"))
            if str(tid).strip()
        ],
        "runtime_generated_task_ids": [
            str(tid)
            for tid in _safe_list(execution_payload.get("runtime_generated_task_ids"))
            if str(tid).strip()
        ],
        "status_counts": _safe_dict(execution_payload.get("status_counts")),
        "plan_source": str(execution_payload.get("plan_source") or ""),
        "goal": {
            "selected_goal_reason": str(cycle.get("selected_goal_reason") or ""),
            "affected_planning": bool(cycle.get("goal_affected_planning", False)),
            "progress_update": _safe_dict(cycle.get("goal_progress_update")),
        },
        "strategy_authority_external": bool(
            cycle.get("strategy_authority_external", False)
        ),
        "strategy_source": str(
            cycle.get("strategy_source") or STRATEGY_SOURCE_FALLBACK_LOCAL
        ),
    }

    response = {
        "ok": bool(cycle.get("ok", False)),
        "mode": str(cycle.get("mode") or ""),
        "strategy_authority_external": bool(
            cycle.get("strategy_authority_external", False)
        ),
        "strategy_source": str(
            cycle.get("strategy_source") or STRATEGY_SOURCE_FALLBACK_LOCAL
        ),
        "strategy": str(cycle.get("strategy") or ""),
        "strategy_reason": str(cycle.get("strategy_reason") or ""),
        "planning_used": planning_executed,
        "reasoning_used": reasoning_executed,
        "context_signals": context_signals,
        "active_goals_summary": _safe_list(cycle.get("active_goals_summary")),
        "selected_goal": cycle.get("selected_goal"),
        "selected_goal_reason": str(cycle.get("selected_goal_reason") or ""),
        "goal_affected_planning": bool(cycle.get("goal_affected_planning", False)),
        "goal_progress_changed": bool(cycle.get("goal_progress_changed", False)),
        "execution_summary": {
            "action_summary": str(execution_result.get("action_summary") or ""),
            "duration_ms": float(reflection.get("duration_ms", 0.0) or 0.0),
            "steps_executed": int(execution_payload.get("steps_executed", 0) or 0),
            "steps_generated": int(execution_payload.get("steps_generated", 0) or 0),
            "timed_out": bool(execution_payload.get("timed_out", False)),
            "dry_run": bool(execution_payload.get("dry_run", False)),
            "execution_flags": {
                "planning_attempted": planning_attempted,
                "planning_executed": planning_executed,
                "reasoning_attempted": reasoning_attempted,
                "reasoning_executed": reasoning_executed,
            },
            "planner_runtime_summary": {
                "strategy_effective": str(cycle.get("strategy") or ""),
                "planning_used": planning_executed,
                "reasoning_used": reasoning_executed,
                "plan_source": plan_src,
                "tasks_planned": (
                    len(lw_planned_ids) if lw_planned_ids else len(lw_records)
                ),
                "tasks_executed": len(lw_executed_ids),
                "tasks_lightweight_executed": len(lw_records) if is_lw else 0,
                "tasks_runtime_generated": len(runtime_generated_ids),
            },
            "task_trace": (
                [
                    {
                        "task_id": r["task_id"],
                        "task_type": r["task_type"],
                        "source": "planner_lightweight",
                        "executed_lightweight": True,
                        "runtime_generated": False,
                        "status": r.get("status"),
                        "duration_ms": r.get("duration_ms"),
                    }
                    for r in lw_records
                    if isinstance(r, dict) and r.get("task_id")
                ]
                if is_lw
                else [
                    {
                        "task_id": n.get("task_id"),
                        "task_type": n.get("task_type"),
                        "source": "planner",
                        "executed_lightweight": False,
                        "runtime_generated": False,
                    }
                    for n in execution_payload.get("graph", {}).get("nodes", [])
                    if isinstance(n, dict)
                ]
            ),
        },
        "trace": trace,
        "errors": _safe_list(execution_result.get("errors")),
        "reflection": reflection,
    }

    response["response_text"] = _build_agent_response_text(cycle)

    # Parity pass-through fields — experience/policy/simulation/reflection/decision signals
    response["experience_lookup_happened"] = bool(
        cycle.get("experience_lookup_happened", False)
    )
    response["experience_matches_count"] = int(cycle.get("experience_matches_count", 0))
    response["experience_influenced_strategy"] = bool(
        cycle.get("experience_influenced_strategy", False)
    )
    response["experience_confidence_adjustment"] = cycle.get(
        "experience_confidence_adjustment"
    )
    response["experience_blocker_reason"] = cycle.get("experience_blocker_reason")
    response["experience_signal_summary"] = str(
        cycle.get("experience_signal_summary") or ""
    )
    response["policy_hints_loaded"] = bool(cycle.get("policy_hints_loaded", False))
    response["policy_profile_name"] = str(cycle.get("policy_profile_name") or "")
    response["policy_feedback_loaded"] = bool(
        cycle.get("policy_feedback_loaded", False)
    )
    response["policy_feedback_applied"] = bool(
        cycle.get("policy_feedback_applied", False)
    )
    response["policy_feedback_summary"] = str(
        cycle.get("policy_feedback_summary") or ""
    )
    response["policy_confidence_delta"] = float(
        cycle.get("policy_confidence_delta") or 0.0
    )
    response["policy_blocker_sensitivity"] = float(
        cycle.get("policy_blocker_sensitivity") or 0.0
    )
    response["policy_simulation_risk_cal"] = float(
        cycle.get("policy_simulation_risk_cal") or 0.0
    )
    response["policy_strategy_adjustments"] = _safe_dict(
        cycle.get("policy_strategy_adjustments")
    )
    response["simulation_ran"] = bool(cycle.get("simulation_ran", False))
    response["simulation_variants_count"] = int(
        cycle.get("simulation_variants_count", 0)
    )
    response["simulation_best_action"] = cycle.get("simulation_best_action")
    response["simulation_risk_summary"] = str(
        cycle.get("simulation_risk_summary") or ""
    )
    response["reflection_ran"] = bool(cycle.get("reflection_ran", False))
    response["reflection_summary"] = str(cycle.get("reflection_summary") or "")
    response["strategy_fit"] = str(cycle.get("strategy_fit") or "")
    response["confidence_hindsight"] = cycle.get("confidence_hindsight")
    response["risk_hindsight"] = cycle.get("risk_hindsight")
    response["decision_signals_reason_codes"] = list(
        cycle.get("decision_signals_reason_codes") or []
    )

    # V2 Bridge Fields (foundation)
    response["memory_v2_loaded"] = bool(cycle.get("memory_v2_loaded", False))
    response["memory_v2_procedures_count"] = int(
        cycle.get("memory_v2_procedures_count", 0)
    )
    response["memory_v2_contradictions_count"] = int(
        cycle.get("memory_v2_contradictions_count", 0)
    )
    response["memory_v2_reinforced_count"] = int(
        cycle.get("memory_v2_reinforced_count", 0)
    )
    response["memory_v2_suppressed_count"] = int(
        cycle.get("memory_v2_suppressed_count", 0)
    )
    response["memory_v2_retrieval_reason_codes"] = cycle.get(
        "memory_v2_retrieval_reason_codes", []
    )
    response["psyche_v2_loaded"] = bool(cycle.get("psyche_v2_loaded", False))
    response["psyche_v2_mode"] = str(cycle.get("psyche_v2_mode", "neutral"))
    response["psyche_v2_habit_biases"] = cycle.get("psyche_v2_habit_biases", [])
    response["psyche_v2_relation_friction"] = float(
        cycle.get("psyche_v2_relation_friction", 0.0)
    )
    response["psyche_v2_pressure"] = float(cycle.get("psyche_v2_pressure", 0.0))
    response["identity_bridge_loaded"] = bool(
        cycle.get("identity_bridge_loaded", False)
    )

    # V2 Real Influence (decision impact)
    response["memory_influenced_strategy"] = bool(
        cycle.get("memory_influenced_strategy", False)
    )
    response["psyche_influenced_strategy"] = bool(
        cycle.get("psyche_influenced_strategy", False)
    )

    # V2 Write-back (outcome capture)
    response["memory_v2_writeback_attempted"] = bool(
        cycle.get("memory_v2_writeback_attempted", False)
    )
    response["memory_v2_writeback_succeeded"] = bool(
        cycle.get("memory_v2_writeback_succeeded", False)
    )
    response["memory_v2_writeback_kind"] = cycle.get("memory_v2_writeback_kind")
    response["memory_v2_new_lessons_count"] = int(
        cycle.get("memory_v2_new_lessons_count", 0)
    )
    response["memory_v2_new_procedures_count"] = int(
        cycle.get("memory_v2_new_procedures_count", 0)
    )
    response["psyche_v2_writeback_attempted"] = bool(
        cycle.get("psyche_v2_writeback_attempted", False)
    )
    response["psyche_v2_writeback_succeeded"] = bool(
        cycle.get("psyche_v2_writeback_succeeded", False)
    )
    response["psyche_v2_event_applied"] = cycle.get("psyche_v2_event_applied")

    # V2 Behavior Injection (real runtime influence)
    response["memory_v2_context_injected"] = bool(
        cycle.get("memory_v2_context_injected", False)
    )
    response["memory_v2_context_item_count"] = int(
        cycle.get("memory_v2_context_item_count", 0)
    )
    response["memory_v2_procedure_bias_applied"] = bool(
        cycle.get("memory_v2_procedure_bias_applied", False)
    )
    response["memory_v2_contradiction_guard_applied"] = bool(
        cycle.get("memory_v2_contradiction_guard_applied", False)
    )
    response["psyche_v2_behavior_applied"] = bool(
        cycle.get("psyche_v2_behavior_applied", False)
    )
    response["psyche_v2_style_mode"] = str(cycle.get("psyche_v2_style_mode", "neutral"))
    response["psyche_v2_pressure_applied"] = bool(
        cycle.get("psyche_v2_pressure_applied", False)
    )
    response["psyche_v2_relation_tone_applied"] = bool(
        cycle.get("psyche_v2_relation_tone_applied", False)
    )
    response["final_behavior_profile"] = _safe_dict(cycle.get("final_behavior_profile"))

    if include_debug:
        response["debug"] = {
            "legacy_response": cycle.get("legacy_response"),
            "cycle_id": str(cycle.get("cycle_id") or ""),
        }

    return response


def build_agent_loop_response(
    cycles: list[dict[str, Any]], *, include_debug: bool = False
) -> dict[str, Any]:
    """Canonical response contract for /agent/loop aggregate runs."""
    normalized = [
        build_agent_cycle_response(c, include_debug=include_debug) for c in cycles
    ]
    if not normalized:
        return {
            "ok": False,
            "mode": "loop",
            "strategy_authority_external": False,
            "strategy_source": STRATEGY_SOURCE_FALLBACK_LOCAL,
            "strategy": "",
            "strategy_reason": "no cycles executed",
            "planning_used": False,
            "reasoning_used": False,
            "context_signals": {},
            "active_goals_summary": [],
            "selected_goal": None,
            "selected_goal_reason": "",
            "goal_affected_planning": False,
            "goal_progress_changed": False,
            "execution_summary": {
                "iterations": 0,
                "completed_iterations": 0,
                "dry_run": False,
            },
            "trace": {"cycles": [], "strategy_counts": {}},
            "errors": [{"error": "no cycles executed"}],
            "reflection": {},
        }

    strategies = [str(n.get("strategy") or "") for n in normalized]
    unique_strategies = sorted({s for s in strategies if s})
    if len(unique_strategies) == 1:
        aggregate_strategy = unique_strategies[0]
    else:
        aggregate_strategy = "mixed"

    strategy_counts: dict[str, int] = {}
    for strategy in strategies:
        if strategy:
            strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1

    all_errors: list[dict[str, Any]] = []
    for idx, n in enumerate(normalized, start=1):
        for e in _safe_list(n.get("errors")):
            err: dict[str, Any]
            if isinstance(e, dict):
                err = dict(e)
            else:
                err = {"error": str(e)}
            err["iteration"] = idx
            all_errors.append(err)

    last_cycle = normalized[-1]
    completed_iterations = sum(1 for n in normalized if bool(n.get("ok", False)))

    planning_attempted = any(
        bool(
            _safe_dict(
                _safe_dict(n.get("execution_summary")).get("execution_flags")
            ).get("planning_attempted", False)
        )
        for n in normalized
    )
    planning_executed = any(bool(n.get("planning_used", False)) for n in normalized)
    reasoning_attempted = any(
        bool(
            _safe_dict(
                _safe_dict(n.get("execution_summary")).get("execution_flags")
            ).get("reasoning_attempted", False)
        )
        for n in normalized
    )
    reasoning_executed = any(bool(n.get("reasoning_used", False)) for n in normalized)

    response = {
        "ok": all(bool(n.get("ok", False)) for n in normalized),
        "mode": "loop",
        "strategy_authority_external": bool(
            last_cycle.get("strategy_authority_external", False)
        ),
        "strategy_source": str(
            last_cycle.get("strategy_source") or STRATEGY_SOURCE_FALLBACK_LOCAL
        ),
        "strategy": aggregate_strategy,
        "strategy_reason": "aggregated from per-cycle runtime results",
        "planning_used": planning_executed,
        "reasoning_used": reasoning_executed,
        "context_signals": _safe_dict(last_cycle.get("context_signals")),
        "active_goals_summary": _safe_list(last_cycle.get("active_goals_summary")),
        "selected_goal": last_cycle.get("selected_goal"),
        "selected_goal_reason": str(last_cycle.get("selected_goal_reason") or ""),
        "goal_affected_planning": any(
            bool(n.get("goal_affected_planning", False)) for n in normalized
        ),
        "goal_progress_changed": any(
            bool(n.get("goal_progress_changed", False)) for n in normalized
        ),
        "execution_summary": {
            "iterations": len(normalized),
            "completed_iterations": completed_iterations,
            "dry_run": any(
                bool(_safe_dict(n.get("execution_summary")).get("dry_run", False))
                for n in normalized
            ),
            "action_summaries": [
                _safe_dict(n.get("execution_summary")).get("action_summary", "")
                for n in normalized
            ],
            "execution_flags": {
                "planning_attempted": planning_attempted,
                "planning_executed": planning_executed,
                "reasoning_attempted": reasoning_attempted,
                "reasoning_executed": reasoning_executed,
            },
        },
        "trace": {
            "cycles": normalized,
            "strategy_counts": strategy_counts,
            "context_signals_history": [
                _safe_dict(n.get("context_signals")) for n in normalized
            ],
        },
        "errors": all_errors,
        "reflection": _safe_dict(last_cycle.get("reflection")),
    }

    if include_debug:
        response["debug"] = {
            "legacy_count": sum(
                1 for c in cycles if c.get("legacy_response") is not None
            ),
            "iterations": len(cycles),
        }

    return response


class ExecutiveController:
    """Single orchestration controller for all runtime entrypoints."""

    _EXEC_STRATEGY_TO_ACTION: dict[str, str] = {
        "planned_reasoning": "run",
        "reactive_tick": "tick",
        "cognitive_direct": "loop",
    }
    _EXEC_ACTION_TO_STRATEGY: dict[str, str] = {
        "run": "planned_reasoning",
        "tick": "reactive_tick",
        "loop": "cognitive_direct",
    }

    def __init__(self) -> None:
        self._planner = PlannerEngine()
        from aihub import cognitive_controller as cognitive_module

        self._cognitive = cognitive_module.get_cognitive_controller()
        self._goals = get_goal_engine()

    def _derive_query_text(self, perception: PerceptionInput) -> str:
        if perception.text:
            return perception.text
        stm = get_stm(perception.user_id, limit=1)
        if stm:
            return str(stm[0].get("content") or "").strip()
        return ""

    async def _build_decision_context(
        self, perception: PerceptionInput
    ) -> DecisionContext:
        query_text = self._derive_query_text(perception)

        if query_text:
            memory_context = retrieve_context(perception.user_id, query_text, limit=8)
            kg_hits = query_nodes(
                query_text, limit=8, user_id=perception.user_id
            )
            knowledge_context = {
                "hits": [
                    {
                        "node_id": n.node_id,
                        "type": n.node_type,
                        "confidence": float(n.confidence),
                        "content": n.content,
                    }
                    for n in kg_hits
                ]
            }
        else:
            memory_context = {
                "user_id": perception.user_id,
                "query": "",
                "stm": get_stm(perception.user_id, limit=20),
                "episodic": [],
                "semantic": [],
                "dense_hits": [],
                "graph_hits": [],
                "total": 0,
            }
            knowledge_context = {"hits": []}

        psyche_state = get_psyche_core().ensure_user(perception.user_id)

        cognitive_signal: dict[str, Any] = {}
        if query_text:
            try:
                req = DecisionRequest(
                    user_id=perception.user_id,
                    message=query_text,
                    context={
                        "memory_total": int(memory_context.get("total", 0) or 0),
                        "knowledge_hits": len(knowledge_context.get("hits", [])),
                        "mode": perception.mode,
                    },
                    available_tools=["web_fetch", "memory", "fs_write", "snapshot"],
                    constraints={"executive_mode": perception.mode},
                )
                dec = await self._cognitive.decide(req)
                cognitive_signal = {
                    "action_type": dec.action_type,
                    "confidence": float(dec.confidence),
                    "reasoning": dec.reasoning,
                }
            except (RuntimeError, ValueError, TypeError, KeyError, OSError) as e:
                logger.warning(
                    "Executive cognitive evaluation failed: user=%s mode=%s err=%s",
                    perception.user_id,
                    perception.mode,
                    e,
                )

        context_signals = {
            "mode": perception.mode,
            "has_text": bool(query_text),
            "memory_total": int(memory_context.get("total", 0) or 0),
            "knowledge_hits": len(knowledge_context.get("hits", [])),
            "pending_stm": len(memory_context.get("stm", [])),
            "energy": float(psyche_state.get("energy", 0.5)),
            "focus": float(psyche_state.get("focus", 0.5)),
            "cognitive_action": cognitive_signal.get("action_type", ""),
            "cognitive_confidence": float(
                cognitive_signal.get("confidence", 0.0) or 0.0
            ),
        }

        system_conditions = {
            "energy": float(psyche_state.get("energy", 0.5)),
            "focus": float(psyche_state.get("focus", 0.5)),
            "memory_pressure": min(
                1.0,
                float(int(memory_context.get("total", 0) or 0)) / 5000.0,
            ),
            "mode": perception.mode,
        }

        # When ChatRuntime (or any subscriber) pins force_strategy, cognitive.decide must
        # not steer goal extraction / hints — keep full cognitive_signal on ctx for telemetry.
        _goal_cognitive = (
            {} if perception.strategy_authority_external() else cognitive_signal
        )
        goal_context = self._goals.build_goal_context(
            user_id=perception.user_id,
            input_event=perception.raw_event,
            memory_context=memory_context,
            cognitive_signal=_goal_cognitive,
            system_conditions=system_conditions,
        )
        context_signals["active_goals"] = len(goal_context.active_goals)
        context_signals["has_selected_goal"] = bool(goal_context.selected_goal)
        context_signals["selected_goal_type"] = (
            goal_context.selected_goal.goal_type if goal_context.selected_goal else ""
        )

        return DecisionContext(
            user_id=perception.user_id,
            mode=perception.mode,
            perception=perception,
            memory_context=memory_context,
            knowledge_context=knowledge_context,
            psyche_state=psyche_state,
            goal_context=goal_context,
            cognitive_signal=cognitive_signal,
            context_signals=context_signals,
        )

    def _select_strategy(self, ctx: DecisionContext) -> tuple[str, str]:
        """Local compatibility fallback when no external force_strategy (not product canon)."""
        forced = str(ctx.perception.raw_event.get("force_strategy") or "").strip()
        if forced in _VALID_STRATEGIES:
            extra = str(
                ctx.perception.raw_event.get("force_strategy_reason") or ""
            ).strip()
            base = "forced by input_event.force_strategy (external authority)"
            return forced, f"{base}; {extra}" if extra else base

        selected_goal = ctx.goal_context.selected_goal if ctx.goal_context else None
        goal_hint = ctx.goal_context.execution_hint if ctx.goal_context else None

        mode = ctx.mode
        if mode == "run":
            if selected_goal is not None:
                return (
                    STRATEGY_PLANNED,
                    f"mode=run with selected goal {selected_goal.goal_id}",
                )
            return STRATEGY_PLANNED, "mode=run requires planner+reasoning runtime"

        if mode == "tick":
            if (
                selected_goal is not None
                and goal_hint is not None
                and goal_hint.recommended_strategy == STRATEGY_PLANNED
                and not bool(ctx.context_signals.get("has_text", False))
            ):
                return (
                    STRATEGY_PLANNED,
                    "mode=tick switched to planned_reasoning due active persistent goal",
                )
            return (
                STRATEGY_REACTIVE,
                "mode=tick requires STM polling/reactive execution",
            )

        if mode == "loop":
            if (
                selected_goal is not None
                and goal_hint is not None
                and goal_hint.recommended_strategy == STRATEGY_PLANNED
                and selected_goal.urgency >= 0.8
            ):
                return (
                    STRATEGY_PLANNED,
                    "mode=loop escalated to planned_reasoning for high-urgency goal",
                )
            return STRATEGY_COGNITIVE, "mode=loop requires direct cognitive cycle"

        if selected_goal is not None and goal_hint is not None:
            if goal_hint.recommended_strategy in _VALID_STRATEGIES:
                return (
                    goal_hint.recommended_strategy,
                    f"fallback uses goal hint from {selected_goal.goal_id}",
                )

        if ctx.context_signals.get("memory_total", 0) == 0:
            return STRATEGY_PLANNED, "fallback: no memory hits, use planned reasoning"
        return STRATEGY_COGNITIVE, "fallback: use cognitive direct strategy"

    def _build_execution_plan(
        self,
        strategy: str,
        strategy_reason: str,
        ctx: DecisionContext,
    ) -> tuple[ExecutionPlan, TaskGraph | None, dict[str, Any]]:
        planning_used = strategy == STRATEGY_PLANNED
        reasoning_used = strategy == STRATEGY_PLANNED
        tasks: list[PlannedTask] = []
        planned_graph: TaskGraph | None = None
        planner_summary: dict[str, Any] = {}
        metadata: dict[str, Any] = {
            "mode": ctx.mode,
            "memory_total": ctx.context_signals.get("memory_total", 0),
            "knowledge_hits": ctx.context_signals.get("knowledge_hits", 0),
        }

        selected_goal = ctx.goal_context.selected_goal if ctx.goal_context else None
        if selected_goal is not None:
            metadata["selected_goal_id"] = selected_goal.goal_id
            metadata["selected_goal_type"] = selected_goal.goal_type
            metadata["selected_goal_progress"] = selected_goal.progress

        if strategy == STRATEGY_PLANNED:
            goal_context_payload = (
                asdict(ctx.goal_context) if ctx.goal_context is not None else None
            )
            plan_message = self._derive_query_text(ctx.perception)
            if not plan_message and selected_goal is not None:
                plan_message = selected_goal.description or selected_goal.title

            planner_memory_context = dict(ctx.memory_context)
            if goal_context_payload is not None:
                planner_memory_context["_goal_context"] = goal_context_payload

            plan_result = self._planner.build_task_graph(
                message=plan_message
                or "Brak jawnego zapytania — wykonaj diagnozę kontekstu",
                memory_context=planner_memory_context,
                knowledge_context=ctx.knowledge_context,
                user_id=ctx.user_id,
            )
            planned_graph = plan_result.graph
            planner_summary = dict(plan_result.summary)
            metadata["planner_summary"] = planner_summary
            tasks = [
                PlannedTask(
                    task_id=n.task_id,
                    task_type=n.task_type,
                    payload=dict(n.payload),
                    depends_on=list(n.depends_on),
                    priority=int(n.priority),
                )
                for n in plan_result.graph.nodes.values()
            ]
        elif strategy == STRATEGY_COGNITIVE:
            plan_message = self._derive_query_text(ctx.perception)
            plan_result = self._planner.build_task_graph(
                message=plan_message or "cognitive context scan",
                memory_context=dict(ctx.memory_context),
                knowledge_context=ctx.knowledge_context,
                user_id=ctx.user_id,
                max_tasks_override=4,
            )
            planned_graph = plan_result.graph
            planner_summary = dict(plan_result.summary)
            metadata["planner_summary"] = planner_summary
            tasks = [
                PlannedTask(
                    task_id=n.task_id,
                    task_type=n.task_type,
                    payload=dict(n.payload),
                    depends_on=list(n.depends_on),
                    priority=int(n.priority),
                )
                for n in plan_result.graph.nodes.values()
            ]
        elif strategy == STRATEGY_REACTIVE:
            plan_message = self._derive_query_text(ctx.perception)
            plan_result = self._planner.build_task_graph(
                message=plan_message or "reactive context scan",
                memory_context=dict(ctx.memory_context),
                knowledge_context=ctx.knowledge_context,
                user_id=ctx.user_id,
                max_tasks_override=3,
            )
            planned_graph = plan_result.graph
            planner_summary = dict(plan_result.summary)
            metadata["planner_summary"] = planner_summary
            tasks = [
                PlannedTask(
                    task_id=n.task_id,
                    task_type=n.task_type,
                    payload=dict(n.payload),
                    depends_on=list(n.depends_on),
                    priority=int(n.priority),
                )
                for n in plan_result.graph.nodes.values()
            ]

        return (
            ExecutionPlan(
                strategy=strategy,
                strategy_reason=strategy_reason,
                planning_used=planning_used,
                reasoning_used=reasoning_used,
                tasks=tasks,
                metadata=metadata,
            ),
            planned_graph,
            planner_summary,
        )

    async def _execute_planner_tasks_lightweight(
        self,
        graph: TaskGraph,
        ctx: "DecisionContext",
    ) -> list[dict[str, Any]]:
        """Execute planner tasks in a minimal lightweight pass (no reasoning loop).
        Returns a list of task records: {task_id, task_type, status, error, duration_ms}.
        """
        import time as _time

        if not graph or not graph.nodes:
            return []

        records: list[dict[str, Any]] = []
        for node in graph.nodes.values():
            t0 = _time.monotonic()
            try:
                if node.task_type == "memory_query":
                    query = node.payload.get("query", "")
                    retrieve_context(ctx.user_id, query, limit=8)
                elif node.task_type == "learn":
                    fact = node.payload.get("fact", "")
                    tags = node.payload.get("tags")
                    add_fact(ctx.user_id, fact, tags=list(tags or []), meta={})
                elif node.task_type == "reason":
                    records.append({
                        "task_id": node.task_id,
                        "task_type": node.task_type,
                        "status": "completed",
                        "duration_ms": 0.0,
                        "details": "reasoning signal already computed in decision context",
                    })
                    continue
                # all other task types: no-op in lightweight mode
                records.append(
                    {
                        "task_id": node.task_id,
                        "task_type": node.task_type,
                        "status": "completed",
                        "error": None,
                        "duration_ms": (_time.monotonic() - t0) * 1000.0,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                records.append(
                    {
                        "task_id": node.task_id,
                        "task_type": node.task_type,
                        "status": "failed",
                        "error": str(exc),
                        "duration_ms": (_time.monotonic() - t0) * 1000.0,
                    }
                )
        return records

    async def _execute_plan(
        self,
        plan: ExecutionPlan,
        ctx: DecisionContext,
        *,
        planned_graph: TaskGraph | None,
        planner_summary: dict[str, Any],
        cycle_id: str,
    ) -> tuple[ExecutionResult, dict[str, Any]]:
        strategy = plan.strategy

        if strategy == STRATEGY_PLANNED:
            selected_goal = ctx.goal_context.selected_goal if ctx.goal_context else None
            message = self._derive_query_text(ctx.perception)
            if not message and selected_goal is not None:
                message = selected_goal.description or selected_goal.title
            if not message:
                message = "diagnose context"

            runtime_memory_context = dict(ctx.memory_context)
            if ctx.goal_context is not None:
                runtime_memory_context["_goal_context"] = asdict(ctx.goal_context)

            if planned_graph is None:
                raise RuntimeError("planned_reasoning requires a prebuilt task graph")

            if ctx.perception.dry_run:
                serialized_graph = planned_graph.serialize()
                dry_payload = {
                    "ok": True,
                    "dry_run": True,
                    "cycle_id": cycle_id,
                    "steps_executed": 0,
                    "steps_generated": 0,
                    "timed_out": False,
                    "duration_ms": 0.0,
                    "errors": [],
                    "status_counts": {
                        "pending": len(serialized_graph.get("nodes", [])),
                        "running": 0,
                        "completed": 0,
                        "failed": 0,
                        "skipped": 0,
                    },
                    "planner_summary": dict(planner_summary),
                    "plan_source": "prebuilt_graph",
                    "planned_task_ids": [
                        str(t.get("task_id"))
                        for t in serialized_graph.get("nodes", [])
                        if isinstance(t, dict) and t.get("task_id")
                    ],
                    "executed_task_ids": [],
                    "runtime_generated_task_ids": [],
                    "context": {
                        "memory_context": runtime_memory_context,
                        "knowledge_context": dict(ctx.knowledge_context),
                        "goal_context": (
                            asdict(ctx.goal_context)
                            if ctx.goal_context is not None
                            else {}
                        ),
                        "history": [],
                    },
                    "graph": serialized_graph,
                }
                result = ExecutionResult(
                    ok=True,
                    strategy=strategy,
                    action_summary="dry_run planned graph generated (no execution)",
                    payload=dry_payload,
                    errors=[],
                )
                return result, dry_payload

            payload = await run_reasoning_loop(
                user_id=ctx.user_id,
                message=message,
                memory_context=runtime_memory_context,
                knowledge_context=ctx.knowledge_context,
                goal_context=(asdict(ctx.goal_context) if ctx.goal_context else None),
                prebuilt_graph=planned_graph,
                planner_summary=planner_summary,
                max_steps=ctx.perception.max_steps,
                timeout_seconds=ctx.perception.timeout_seconds,
            )
            payload["cycle_id"] = cycle_id
            errors = [
                {
                    "task_id": e.get("task_id", ""),
                    "task_type": e.get("task_type", ""),
                    "error": e.get("error", ""),
                }
                for e in payload.get("errors", [])
            ]
            summary = (
                f"reasoning steps={int(payload.get('steps_executed', 0))}, "
                f"generated={int(payload.get('steps_generated', 0))}, "
                f"timed_out={bool(payload.get('timed_out', False))}"
            )
            result = ExecutionResult(
                ok=bool(payload.get("ok", False)),
                strategy=strategy,
                action_summary=summary,
                payload=payload,
                errors=errors,
            )
            return result, payload

        if strategy == STRATEGY_REACTIVE:
            if ctx.perception.dry_run:
                payload = {
                    "ok": True,
                    "cycle_id": cycle_id,
                    "dry_run": True,
                    "processed": 0,
                    "enqueued": 0,
                    "ran": 0,
                    "planner_task_records": [],
                    "plan_source": "planner_lightweight",
                    "planned_task_ids": [],
                    "executed_task_ids": [],
                }
                result = ExecutionResult(
                    ok=True,
                    strategy=strategy,
                    action_summary="dry_run reactive tick skipped",
                    payload=payload,
                    errors=[],
                )
                return result, payload

            from aihub.agent_engine import run_reactive_tick_cycle

            lw_records = (
                await self._execute_planner_tasks_lightweight(planned_graph, ctx)
                if planned_graph
                else []
            )
            planned_ids = [r["task_id"] for r in lw_records]
            executed_ids = [
                r["task_id"] for r in lw_records if r["status"] == "completed"
            ]

            payload = await run_reactive_tick_cycle(
                ctx.user_id,
                max_stm=ctx.perception.max_stm,
                max_tasks=ctx.perception.max_tasks,
            )
            payload["cycle_id"] = cycle_id
            payload["planner_task_records"] = lw_records
            payload["plan_source"] = "planner_lightweight"
            payload["planned_task_ids"] = planned_ids
            payload["executed_task_ids"] = executed_ids
            errors: list[dict[str, Any]] = []
            if payload.get("error"):
                errors.append({"error": str(payload.get("error"))})
            errors.extend(
                {
                    "task_id": r["task_id"],
                    "task_type": r["task_type"],
                    "error": str(r.get("error", "task_failed")),
                }
                for r in lw_records
                if r.get("status") == "failed"
            )
            summary = (
                f"planner_tasks={len(executed_ids)}/{len(lw_records)}, "
                f"tick processed={int(payload.get('processed', 0))}, "
                f"enqueued={int(payload.get('enqueued', 0))}, "
                f"ran={int(payload.get('ran', 0))}"
            )
            result = ExecutionResult(
                ok=bool(payload.get("ok", False)),
                strategy=strategy,
                action_summary=summary,
                payload=payload,
                errors=errors,
            )
            return result, payload

        if ctx.perception.dry_run:
            payload = {
                "ok": True,
                "cycle": "completed",
                "cycle_id": cycle_id,
                "dry_run": True,
                "messages_processed": 0,
                "decisions_made": 0,
                "planner_task_records": [],
                "plan_source": "planner_lightweight",
                "planned_task_ids": [],
                "executed_task_ids": [],
            }
            result = ExecutionResult(
                ok=True,
                strategy=strategy,
                action_summary="dry_run cognitive cycle skipped",
                payload=payload,
                errors=[],
            )
            return result, payload

        from aihub.agent_loop import run_cognitive_direct_cycle

        lw_records = (
            await self._execute_planner_tasks_lightweight(planned_graph, ctx)
            if planned_graph
            else []
        )
        planned_ids = [r["task_id"] for r in lw_records]
        executed_ids = [r["task_id"] for r in lw_records if r["status"] == "completed"]

        payload = await run_cognitive_direct_cycle(ctx.user_id)
        payload["cycle_id"] = cycle_id
        payload["planner_task_records"] = lw_records
        payload["plan_source"] = "planner_lightweight"
        payload["planned_task_ids"] = planned_ids
        payload["executed_task_ids"] = executed_ids
        ok = payload.get("cycle") != "error"
        errors: list[dict[str, Any]] = (
            [{"error": str(payload.get("error"))}] if payload.get("error") else []
        )
        errors.extend(
            {
                "task_id": r["task_id"],
                "task_type": r["task_type"],
                "error": str(r.get("error", "task_failed")),
            }
            for r in lw_records
            if r.get("status") == "failed"
        )
        summary = (
            f"planner_tasks={len(executed_ids)}/{len(lw_records)}, "
            f"loop messages={int(payload.get('messages_processed', 0))}, "
            f"decisions={int(payload.get('decisions_made', 0))}"
        )
        result = ExecutionResult(
            ok=bool(ok),
            strategy=strategy,
            action_summary=summary,
            payload=payload,
            errors=errors,
        )
        return result, payload

    def _compute_experience_signal(
        self,
        *,
        user_id: str,
        query_text: str,
        selected_strategy: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Compute experience-driven bias signal for the current query."""
        _FAIL: dict[str, Any] = {
            "lookup_happened": False,
            "experience_signal_summary": "lookup_failed",
            "matches_count": 0,
            "recommended_strategy": None,
            "confidence_adjustment": None,
            "recurring_failure_detected": False,
            "blocker_reason": None,
            "action_bias": None,
        }
        try:
            experiences = get_experiences_by_user(user_id, limit=20)
        except Exception:  # noqa: BLE001
            return _FAIL

        if not experiences:
            return {
                "lookup_happened": True,
                "experience_signal_summary": "no_history",
                "matches_count": 0,
                "recommended_strategy": None,
                "confidence_adjustment": None,
                "recurring_failure_detected": False,
                "blocker_reason": None,
                "action_bias": None,
            }

        # Word-overlap matching
        query_words = set(query_text.lower().split())
        matched: list[dict[str, Any]] = []
        for exp in experiences:
            exp_summary = str(exp.get("user_input_summary") or "")
            exp_words = set(exp_summary.lower().split())
            if not query_words or not exp_words:
                continue
            overlap_ratio = len(query_words & exp_words) / max(len(query_words), 1)
            if overlap_ratio >= 0.3:
                matched.append(exp)

        matches_count = len(matched)
        if not matched:
            return {
                "lookup_happened": True,
                "experience_signal_summary": "no_matches",
                "matches_count": 0,
                "recommended_strategy": None,
                "confidence_adjustment": None,
                "recurring_failure_detected": False,
                "blocker_reason": None,
                "action_bias": None,
            }

        failures = [e for e in matched if not e.get("success", True)]
        failure_rate = len(failures) / len(matched)
        success_rate = 1.0 - failure_rate
        confidence_adjustment = (
            round(0.05 + success_rate * 0.05, 4)
            if success_rate >= 0.7
            else round(-0.05 - failure_rate * 0.05, 4)
        )

        # Detect recurring failures of same type in same strategy
        from collections import Counter as _Counter

        recurring_failure_detected = False
        blocker_reason: str | None = None
        qualifying = [
            str(e.get("failure_type") or "")
            for e in failures
            if e.get("failure_type") and e.get("selected_strategy") == selected_strategy
        ]
        if qualifying:
            most_common_ft, count = _Counter(qualifying).most_common(1)[0]
            if count >= 2 and most_common_ft:
                recurring_failure_detected = True
                blocker_reason = f"recurring {most_common_ft} in {selected_strategy} strategy ({count} times)"

        # Compute action_bias: if current strategy fails often, suggest alternative
        action_bias: dict[str, Any] | None = None
        strategy_stats: dict[str, dict[str, int]] = {}
        for exp in matched:
            s = str(exp.get("selected_strategy") or "")
            if s not in strategy_stats:
                strategy_stats[s] = {"successes": 0, "failures": 0}
            if exp.get("success", True):
                strategy_stats[s]["successes"] += 1
            else:
                strategy_stats[s]["failures"] += 1
        current_stats = strategy_stats.get(selected_strategy, {})
        if current_stats.get("failures", 0) >= 2:
            best_alt: str | None = None
            best_alt_rate = 0.0
            for s, stats in strategy_stats.items():
                if s == selected_strategy:
                    continue
                total = stats["successes"] + stats["failures"]
                if total > 0:
                    rate = stats["successes"] / total
                    if rate > best_alt_rate:
                        best_alt_rate = rate
                        best_alt = s
            if best_alt:
                action_bias = {
                    "recommended_strategy": best_alt,
                    "alt_success_rate": round(best_alt_rate, 4),
                    "current_failure_count": current_stats["failures"],
                }

        adj_sign = "+" if confidence_adjustment >= 0 else ""
        summary = (
            f"matches={matches_count} "
            f"succ={round(success_rate, 2)} "
            f"fail={round(failure_rate, 2)} "
            f"conf_adj={adj_sign}{round(confidence_adjustment, 2)}"
        )
        return {
            "lookup_happened": True,
            "experience_signal_summary": summary,
            "matches_count": matches_count,
            "recommended_strategy": (
                action_bias["recommended_strategy"] if action_bias else None
            ),
            "confidence_adjustment": confidence_adjustment,
            "recurring_failure_detected": recurring_failure_detected,
            "blocker_reason": blocker_reason,
            "action_bias": action_bias,
        }

    def _compute_decision_signals(
        self,
        *,
        user_id: str,
        query_text: str,
        selected_strategy: str,
        strategy_confidence: float,
        psyche_state: dict[str, Any],
        mode: str,
        memory_v2_contradictions_count: int = 0,
        memory_v2_procedures_count: int = 0,
        psyche_v2_mode: str = "neutral",
        psyche_v2_relation_trust: float = 0.5,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Compute layered decision signals (experience + policy + simulation)."""
        exp = self._compute_experience_signal(
            user_id=user_id,
            query_text=query_text,
            selected_strategy=selected_strategy,
        )

        reason_codes: list[str] = []
        adjusted_confidence = strategy_confidence
        conf_adj = exp.get("confidence_adjustment")
        if exp.get("lookup_happened"):
            reason_codes.append("EXPERIENCE_LOOKUP")
        if conf_adj is not None:
            adjusted_confidence = max(
                0.0, min(1.0, strategy_confidence + float(conf_adj))
            )
            reason_codes.append("EXPERIENCE_CONFIDENCE")

        # ── V2 REAL INFLUENCE: Memory + Psyche affect decision signals ──
        risk_adjustment = 0.0
        confidence_adjustment = 0.0

        if memory_v2_contradictions_count > 0:
            risk_adjustment += 0.3
            reason_codes.append("MEMORY_V2_CONTRADICTIONS")

        if memory_v2_procedures_count > 0:
            confidence_adjustment += 0.2
            reason_codes.append("MEMORY_V2_PROCEDURES")

        if psyche_v2_mode == "cautious":
            risk_adjustment += 0.2
            reason_codes.append("PSYCHE_V2_CAUTIOUS")

        if psyche_v2_mode == "focused":
            confidence_adjustment += 0.1
            reason_codes.append("PSYCHE_V2_FOCUSED")

        if psyche_v2_relation_trust < 0.3:
            confidence_adjustment -= 0.1
            reason_codes.append("PSYCHE_V2_LOW_TRUST")

        adjusted_confidence = max(
            0.0, min(1.0, adjusted_confidence + confidence_adjustment)
        )

        return {
            "selected_strategy": selected_strategy,
            "strategy_confidence": adjusted_confidence,
            "reason_codes": reason_codes,
            "experience_lookup_happened": bool(exp.get("lookup_happened", False)),
            "experience_matches_count": int(exp.get("matches_count", 0)),
            "experience_signal_summary": str(
                exp.get("experience_signal_summary") or ""
            ),
            "experience_blocker_reason": exp.get("blocker_reason"),
            "experience_confidence_adjustment": conf_adj,
            "experience_influenced_strategy": bool(exp.get("recommended_strategy")),
            "policy_hints_loaded": False,
            "policy_profile_name": f"user:{user_id}",
            "policy_feedback_loaded": False,
            "policy_feedback_applied": False,
            "policy_feedback_summary": "",
            "policy_confidence_delta": confidence_adjustment,
            "policy_blocker_sensitivity": risk_adjustment,
            "policy_simulation_risk_cal": risk_adjustment,
            "policy_strategy_adjustments": {},
            "simulation_ran": False,
            "simulation_variants_count": 0,
            "simulation_best_action": None,
            "simulation_risk_summary": "",
        }

    def _compute_post_exec_reflection(
        self,
        *,
        user_id: str,
        strategy: str,
        execution_result: "ExecutionResult",
        decision_signals: dict[str, Any],
        duration_ms: float,
        query_text: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Compute post-execution reflection / hindsight."""
        ok = bool(getattr(execution_result, "ok", True))
        errors = list(getattr(execution_result, "errors", []))

        if ok and not errors:
            strategy_fit = "optimal"
        elif ok and errors:
            strategy_fit = "partial"
        else:
            strategy_fit = "poor"

        initial_conf = float(decision_signals.get("strategy_confidence") or 0.7)
        confidence_hindsight = round(
            initial_conf * 0.1 if ok else -initial_conf * 0.1, 4
        )
        risk_hindsight: float | None = (
            -0.05 if (ok and decision_signals.get("simulation_ran")) else None
        )

        summary = (
            f"Strategy {strategy} {'succeeded' if ok else 'failed'}. "
            f"Fit: {strategy_fit}. Duration: {round(duration_ms)}ms."
        )
        return {
            "reflection_ran": True,
            "reflection_summary": summary,
            "strategy_fit": strategy_fit,
            "confidence_hindsight": confidence_hindsight,
            "risk_hindsight": risk_hindsight,
        }

    async def run_cycle(
        self,
        input_event: dict,
        mode: str,
        user_id: str | None = None,
    ) -> dict:
        """Run one canonical executive cycle."""
        started = time.monotonic()
        perception = PerceptionInput.from_event(input_event, mode, user_id=user_id)
        get_psyche_core().ensure_user(perception.user_id)
        cycle_id = f"{perception.user_id}:{perception.mode}:{int(now_ts() * 1000)}"

        # V2 Bridge Snapshots (read-only foundation)
        memory_v2_snapshot: dict[str, Any] = {}
        psyche_v2_snapshot: dict[str, Any] = {}
        identity_bridge_snapshot = None
        memory_v2_runtime_ctx = None
        psyche_v2_behavior_ctx = None
        try:
            from aihub.runtime_identity_bridge import build_identity_bridge_snapshot
            from aihub.runtime_memory_bridge import (
                build_memory_v2_runtime_context,
                summarize_memory_v2_for_agent,
            )
            from aihub.runtime_psyche_bridge import (
                build_psyche_v2_behavior_context,
                summarize_psyche_v2_for_agent,
            )

            query_text = self._derive_query_text(perception)
            memory_v2_snapshot = summarize_memory_v2_for_agent(
                perception.user_id, query_text
            )
            psyche_v2_snapshot = summarize_psyche_v2_for_agent(perception.user_id)
            identity_bridge_snapshot = build_identity_bridge_snapshot(
                perception.user_id, query_text
            )

            # Production runtime contexts for behavior injection
            memory_v2_runtime_ctx = build_memory_v2_runtime_context(
                perception.user_id, query_text
            )
            psyche_v2_behavior_ctx = build_psyche_v2_behavior_context(
                perception.user_id
            )
        except Exception as bridge_error:
            logger.warning(f"Failed to load V2 bridges: {bridge_error}")

        try:
            if memory_v2_runtime_ctx is not None or psyche_v2_behavior_ctx is not None:
                from aihub.psyche_v2_repository import ensure_psyche_profile
                from aihub.runtime_psyche_bridge import apply_consistency_to_contexts

                _prof = ensure_psyche_profile(perception.user_id)
                memory_v2_runtime_ctx, psyche_v2_behavior_ctx, _ = (
                    apply_consistency_to_contexts(
                        memory_v2_runtime_ctx,
                        psyche_v2_behavior_ctx,
                        _prof.core_caution,
                    )
                )
        except Exception as consistency_error:
            logger.debug(
                "Executive self-consistency pass skipped: %s",
                consistency_error,
                exc_info=True,
            )

        ctx = await self._build_decision_context(perception)
        strategy, strategy_reason = self._select_strategy(ctx)
        strategy_authority_external = perception.strategy_authority_external()
        strategy_source = resolve_strategy_source(perception)
        ctx.context_signals["strategy_source"] = strategy_source
        ctx.context_signals["strategy_authority_external"] = strategy_authority_external

        # ── V2 REAL INFLUENCE: Memory + Psyche affect strategy ──
        # Skipped when force_strategy is set — subscriber owns routing; V2 remains in payload.
        memory_v2_contradictions_count = memory_v2_snapshot.get(
            "contradictions_count", 0
        )
        memory_v2_actionable_contradictions = memory_v2_snapshot.get(
            "actionable_contradictions_count", memory_v2_contradictions_count
        )
        if memory_v2_runtime_ctx is not None and getattr(
            memory_v2_runtime_ctx, "loaded", False
        ):
            memory_v2_actionable_contradictions = len(
                memory_v2_runtime_ctx.contradiction_alerts
            )
            memory_v2_contradictions_count = memory_v2_actionable_contradictions + len(
                memory_v2_runtime_ctx.transient_contradiction_hints
            )
        memory_v2_procedures_count = memory_v2_snapshot.get("procedures_count", 0)
        psyche_v2_mode = psyche_v2_snapshot.get("mode", "neutral")
        psyche_v2_relation_trust = float(psyche_v2_snapshot.get("relation_trust", 0.5))
        if psyche_v2_behavior_ctx is not None and getattr(
            psyche_v2_behavior_ctx, "loaded", False
        ):
            psyche_v2_mode = psyche_v2_behavior_ctx.mode
            psyche_v2_relation_trust = float(psyche_v2_behavior_ctx.trust)

        memory_influenced_strategy = False
        psyche_influenced_strategy = False

        psyche_reason_codes: list[str] = []
        memory_reason_codes: list[str] = []

        if not strategy_authority_external:
            if memory_v2_actionable_contradictions > 0 and strategy != STRATEGY_PLANNED:
                strategy = STRATEGY_PLANNED
                strategy_reason = f"Memory V2: {memory_v2_contradictions_count} contradictions require planning"
                memory_influenced_strategy = True
                memory_reason_codes.append("contradictions_present")
                logger.info(
                    f"V2 override: contradictions → planned_reasoning (user={perception.user_id})"
                )

            if memory_v2_procedures_count > 0 and strategy == STRATEGY_COGNITIVE:
                strategy = STRATEGY_REACTIVE
                strategy_reason = f"Memory V2: {memory_v2_procedures_count} procedures suggest reactive execution"
                memory_influenced_strategy = True
                memory_reason_codes.append("procedures_available")
                logger.info(
                    f"V2 override: procedures → reactive_tick (user={perception.user_id})"
                )

            if psyche_v2_mode == "cautious" and strategy == STRATEGY_REACTIVE:
                strategy = STRATEGY_PLANNED
                strategy_reason = f"Psyche V2: cautious mode requires careful planning"
                psyche_influenced_strategy = True
                psyche_reason_codes.append("cautious_mode_active")
                logger.info(
                    f"V2 override: cautious mode → planned_reasoning (user={perception.user_id})"
                )

            if psyche_v2_mode == "focused" and (
                strategy == STRATEGY_COGNITIVE or perception.mode == "loop"
            ):
                strategy = STRATEGY_REACTIVE
                strategy_reason = f"Psyche V2: focused mode prefers direct execution"
                psyche_influenced_strategy = True
                psyche_reason_codes.append("focused_mode_active")
                logger.info(
                    f"V2 override: focused mode → reactive_tick (user={perception.user_id})"
                )

            if (
                psyche_v2_behavior_ctx is not None
                and getattr(psyche_v2_behavior_ctx, "loaded", False)
                and getattr(psyche_v2_behavior_ctx, "consistency_decision", "allow")
                == "suppress"
                and memory_v2_runtime_ctx is not None
                and getattr(memory_v2_runtime_ctx, "loaded", False)
                and memory_v2_actionable_contradictions == 0
                and strategy == STRATEGY_PLANNED
                and "cautious_mode_active" in psyche_reason_codes
            ):
                strategy = STRATEGY_REACTIVE
                strategy_reason = (
                    "Self-consistency: ostrożny tryb bez twardych sprzeczności pamięci "
                    "— planowanie nie jest wymuszane"
                )
                psyche_reason_codes.append("SELF_CONSISTENCY_RELAX_PLANNED")

        # ETAP 9A: Pre-routing strategy analysis (informational layer)
        active_goals_summary = None
        if ctx.goal_context and ctx.goal_context.active_goals:
            max_urgency = (
                max(g.urgency for g in ctx.goal_context.active_goals)
                if ctx.goal_context.active_goals
                else 0.0
            )
            active_goals_summary = {
                "active_count": len(ctx.goal_context.active_goals),
                "max_urgency": max_urgency,
            }

        try:
            strategy_selection = select_strategy(
                user_id=perception.user_id,
                user_text=self._derive_query_text(perception),
                mode=perception.mode,
                active_goals_summary=active_goals_summary,
            )
            # Advisory only — never mutates execution strategy (see strategy_authority_external).
            advisory_payload = dict(strategy_selection.trace_payload or {})
            advisory_selected = str(advisory_payload.get("selected_strategy") or "")
            advisory_payload["selected_strategy_advisory"] = advisory_selected
            advisory_payload["selected_strategy_effective"] = strategy
            advisory_payload["advisory_applied"] = False
            advisory_payload["strategy_authority_external"] = (
                strategy_authority_external
            )
            advisory_payload["strategy_source"] = strategy_source
            advisory_payload["advisory_matches_effective"] = (
                advisory_selected == strategy
            )
            ctx.context_signals["strategy_selection"] = advisory_payload
        except Exception as e:  # noqa: BLE001
            logger.warning("Strategy selection failed: %s", e)
            ctx.context_signals["strategy_selection"] = {
                "error": str(e),
                "degraded": True,
                "selected_strategy_effective": strategy,
                "strategy_authority_external": strategy_authority_external,
                "strategy_source": strategy_source,
            }

        plan, planned_graph, planner_summary = self._build_execution_plan(
            strategy, strategy_reason, ctx
        )
        plan.metadata["cycle_id"] = cycle_id
        execution_result, raw_payload = await self._execute_plan(
            plan,
            ctx,
            planned_graph=planned_graph,
            planner_summary=planner_summary,
            cycle_id=cycle_id,
        )

        # Execution truth flags (facts) used by canonical response serializers.
        # planning_attempted/executed = czy FULL planning (strategy=PLANNED) został próbowany/wykonany
        # lightweight planner (REACTIVE/COGNITIVE) NIE liczy się jako "planning"
        planning_attempted = plan.planning_used
        planning_executed = bool(
            plan.planning_used
            and planned_graph is not None
            and bool(getattr(planned_graph, "nodes", {}))
        )
        reasoning_attempted = bool(
            strategy == STRATEGY_PLANNED and not perception.dry_run
        )
        reasoning_executed = bool(
            reasoning_attempted
            and isinstance(raw_payload, dict)
            and (
                "steps_executed" in raw_payload
                or "status_counts" in raw_payload
                or "errors" in raw_payload
            )
        )

        goal_progress_update: dict[str, Any] = {
            "updated": False,
            "progress_changed": False,
            "progress_before": None,
            "progress_after": None,
            "status": "",
        }
        selected_goal = ctx.goal_context.selected_goal if ctx.goal_context else None
        if selected_goal is not None and not perception.dry_run:
            try:
                goal_progress_update = self._goals.link_goal_to_result(
                    user_id=ctx.user_id,
                    goal_id=selected_goal.goal_id,
                    execution_result=asdict(execution_result),
                    execution_plan=asdict(plan),
                )
            except (RuntimeError, ValueError, TypeError, KeyError, OSError) as e:
                logger.warning(
                    "executive.goal_progress_update failed user=%s goal=%s err=%s",
                    ctx.user_id,
                    selected_goal.goal_id,
                    e,
                )

        duration_ms = (time.monotonic() - started) * 1000.0
        reflection = ReflectionRecord(
            mode=ctx.mode,
            strategy=strategy,
            strategy_reason=strategy_reason,
            planning_used=planning_executed,
            reasoning_used=reasoning_executed,
            memory_hits={
                "total": int(ctx.context_signals.get("memory_total", 0)),
                "episodic": len(ctx.memory_context.get("episodic", [])),
                "semantic": len(ctx.memory_context.get("semantic", [])),
            },
            context_signals=dict(ctx.context_signals),
            duration_ms=duration_ms,
            action_summary=execution_result.action_summary,
            errors=list(execution_result.errors),
        )

        logger.info(
            "executive.cycle mode=%s user=%s strategy=%s reason=%s memory_total=%d planner=%s reasoning=%s summary=%s errors=%d",
            ctx.mode,
            ctx.user_id,
            strategy,
            strategy_reason,
            int(ctx.context_signals.get("memory_total", 0)),
            planning_executed,
            reasoning_executed,
            execution_result.action_summary,
            len(execution_result.errors),
        )

        payload = {
            "cycle_id": cycle_id,
            "ok": bool(execution_result.ok),
            "mode": ctx.mode,
            "user_id": ctx.user_id,
            "strategy_authority_external": strategy_authority_external,
            "strategy_source": strategy_source,
            "strategy": strategy,
            "strategy_reason": strategy_reason,
            "planning_used": planning_executed,
            "reasoning_used": reasoning_executed,
            "planning_attempted": planning_attempted,
            "planning_executed": planning_executed,
            "reasoning_attempted": reasoning_attempted,
            "reasoning_executed": reasoning_executed,
            "context_signals": dict(ctx.context_signals),
            "perception": asdict(perception),
            "decision_context": {
                "psyche_state": dict(ctx.psyche_state),
                "cognitive_signal": dict(ctx.cognitive_signal),
                "memory_total": int(ctx.context_signals.get("memory_total", 0)),
                "knowledge_hits": int(ctx.context_signals.get("knowledge_hits", 0)),
                "goal_selected": (
                    ctx.goal_context.selected_goal.goal_id
                    if ctx.goal_context and ctx.goal_context.selected_goal
                    else ""
                ),
            },
            "active_goals_summary": [
                {
                    "goal_id": g.goal_id,
                    "title": g.title,
                    "goal_type": g.goal_type,
                    "status": g.status,
                    "priority": g.priority,
                    "urgency": g.urgency,
                    "progress": g.progress,
                }
                for g in (
                    (ctx.goal_context.active_goals[:8]) if ctx.goal_context else []
                )
            ],
            "selected_goal": (
                asdict(ctx.goal_context.selected_goal)
                if ctx.goal_context and ctx.goal_context.selected_goal
                else None
            ),
            "selected_goal_reason": (
                ctx.goal_context.selected_reason if ctx.goal_context else ""
            ),
            "goal_context_trace": {
                "created_goal_ids": (
                    list(ctx.goal_context.created_goal_ids) if ctx.goal_context else []
                ),
                "candidates": (
                    [asdict(c) for c in ctx.goal_context.candidates]
                    if ctx.goal_context
                    else []
                ),
                "top_scores": (
                    [asdict(s) for s in ctx.goal_context.top_scores]
                    if ctx.goal_context
                    else []
                ),
                "execution_hint": (
                    asdict(ctx.goal_context.execution_hint)
                    if ctx.goal_context and ctx.goal_context.execution_hint is not None
                    else None
                ),
            },
            "goal_affected_planning": bool(
                selected_goal is not None and planning_executed
            ),
            "goal_progress_changed": bool(
                goal_progress_update.get("progress_changed", False)
            ),
            "goal_progress_update": goal_progress_update,
            "execution_plan": asdict(plan),
            "execution_result": asdict(execution_result),
            "reflection": asdict(reflection),
            "legacy_response": raw_payload,
        }

        # ETAP 9A: Post-turn ExperienceMemory write-back
        if not perception.dry_run:
            try:
                experience_id = str(uuid.uuid4())
                user_input_summary = (
                    self._derive_query_text(perception) or perception.mode
                )
                content_hash = hashlib.sha256(
                    user_input_summary.encode("utf-8", errors="ignore")
                ).hexdigest()

                # Determine execution facts
                tools_needed = bool(execution_result.payload.get("tools_needed", False))
                tools_executed = bool(
                    execution_result.payload.get("tools_executed", False)
                )
                research_needed = bool(
                    ctx.context_signals.get("strategy_selection", {}).get(
                        "research_needed", False
                    )
                )
                research_executed = bool(
                    execution_result.payload.get("research_executed", False)
                )

                # Determine outcome
                outcome_type = "success" if execution_result.ok else "failure"
                failure_type = None
                if execution_result.errors:
                    failure_type = execution_result.errors[0].get("error", "unknown")[
                        :100
                    ]

                # Lesson and seed for reflection
                short_lesson = f"Strategy: {strategy}. Summary: {execution_result.action_summary[:100]}"
                reflection_seed = (
                    f"Need to revisit: {strategy} routing decision"
                    if not execution_result.ok
                    else f"Confirm pattern: {strategy} worked well"
                )

                strategy_selection_trace = dict(
                    ctx.context_signals.get("strategy_selection", {})
                )

                write_success = write_experience(
                    experience_id=experience_id,
                    user_id=perception.user_id,
                    user_input_summary=user_input_summary,
                    selected_strategy=strategy,
                    reason_codes=strategy_selection_trace.get("reason_codes", []),
                    tools_needed=tools_needed,
                    tools_executed=tools_executed,
                    research_needed=research_needed,
                    research_executed=research_executed,
                    planner_recommended=planning_attempted,
                    planner_executed=planning_executed,
                    agentic_recommended=bool(
                        strategy_selection_trace.get("agentic_recommended", False)
                    ),
                    agentic_executed=bool(
                        strategy in {STRATEGY_PLANNED, STRATEGY_COGNITIVE}
                    ),
                    outcome_type=outcome_type,
                    success=execution_result.ok,
                    failure_type=failure_type,
                    fallback_flag=(
                        bool(raw_payload.get("fallback_flag", False))
                        if isinstance(raw_payload, dict)
                        else False
                    ),
                    degraded_flag=bool(strategy_selection_trace.get("degraded", False)),
                    latency_ms=reflection.duration_ms,
                    content_hash=content_hash,
                    embedding_provider="voyage",
                    embedding_model="voyage-4-large",
                    embedding_dimension=1024,
                    embedding_input_type="document",
                    semantic_embedding=None,
                    short_lesson_learned=short_lesson,
                    reflection_seed=reflection_seed,
                    session_id=None,
                    trace_id=cycle_id,
                    goal_id=selected_goal.goal_id if selected_goal else None,
                    metadata={
                        "mode": perception.mode,
                        "memory_total": ctx.context_signals.get("memory_total", 0),
                        "knowledge_hits": ctx.context_signals.get("knowledge_hits", 0),
                    },
                )

                if write_success:
                    payload["experience_write_back_attempted"] = True
                    payload["experience_write_back_succeeded"] = True
                    payload["experience_id"] = experience_id
                else:
                    payload["experience_write_back_attempted"] = True
                    payload["experience_write_back_succeeded"] = False

            except Exception as e:  # noqa: BLE001
                logger.warning("ExperienceMemory write-back failed: %s", e)
                payload["experience_write_back_attempted"] = True
                payload["experience_write_back_succeeded"] = False
                payload["experience_write_back_error"] = str(e)

        # ── V2 POST-EXECUTION WRITE-BACK: outcome → Memory V2 + Psyche V2 ──
        payload["memory_v2_writeback_attempted"] = False
        payload["memory_v2_writeback_succeeded"] = False
        payload["memory_v2_writeback_kind"] = None
        payload["memory_v2_new_lessons_count"] = 0
        payload["memory_v2_new_procedures_count"] = 0
        payload["psyche_v2_writeback_attempted"] = False
        payload["psyche_v2_writeback_succeeded"] = False
        payload["psyche_v2_event_applied"] = None

        if not perception.dry_run:
            try:
                _psy_svc = get_psyche_core().v2_service

                # Memory V2 write-back (canonical core → single v2_service instance)
                memory_wb = get_memory_core().record_agent_outcome(
                    user_id=perception.user_id,
                    cycle_id=cycle_id,
                    strategy=strategy,
                    ok=execution_result.ok,
                    action_summary=execution_result.action_summary,
                    errors=list(execution_result.errors),
                    goal_progress_changed=bool(
                        goal_progress_update.get("progress_changed", False)
                    ),
                    contradictions_present=memory_v2_snapshot.get(
                        "contradictions_count", 0
                    ),
                    procedures_active=memory_v2_snapshot.get("procedures_count", 0),
                    duration_ms=duration_ms,
                )
                payload["memory_v2_writeback_attempted"] = memory_wb.get(
                    "attempted", False
                )
                payload["memory_v2_writeback_succeeded"] = memory_wb.get(
                    "succeeded", False
                )
                payload["memory_v2_writeback_kind"] = memory_wb.get("writeback_kind")
                payload["memory_v2_new_lessons_count"] = memory_wb.get(
                    "new_lessons_count", 0
                )
                payload["memory_v2_new_procedures_count"] = memory_wb.get(
                    "new_procedures_count", 0
                )

                # Psyche V2 write-back
                outcome_kind = "success" if execution_result.ok else "failure"
                if not execution_result.ok and len(execution_result.errors) > 0:
                    # Check for timeout-like failures
                    error_msgs = " ".join(
                        [e.get("message", "") for e in execution_result.errors[:3]]
                    )
                    if (
                        "timeout" in error_msgs.lower()
                        or "timed out" in error_msgs.lower()
                    ):
                        outcome_kind = "timeout"
                if goal_progress_update.get("progress_changed", False):
                    outcome_kind = "progress"

                psyche_wb = _psy_svc.apply_outcome_event(
                    user_id=perception.user_id,
                    outcome_kind=outcome_kind,
                    source_ref=cycle_id,
                    context={
                        "contradictions_present": memory_v2_snapshot.get(
                            "contradictions_count", 0
                        ),
                        "goal_progress_changed": bool(
                            goal_progress_update.get("progress_changed", False)
                        ),
                        "strategy": strategy,
                        "duration_ms": duration_ms,
                    },
                )
                payload["psyche_v2_writeback_attempted"] = psyche_wb.get(
                    "attempted", False
                )
                payload["psyche_v2_writeback_succeeded"] = psyche_wb.get(
                    "succeeded", False
                )
                payload["psyche_v2_event_applied"] = psyche_wb.get("event_applied")

                logger.info(
                    f"V2 write-back: memory={memory_wb.get('succeeded')} psyche={psyche_wb.get('succeeded')} user={perception.user_id}"
                )

            except Exception as v2_wb_error:
                logger.warning(f"V2 write-back failed: {v2_wb_error}", exc_info=True)
                payload["memory_v2_writeback_attempted"] = True
                payload["psyche_v2_writeback_attempted"] = True

        # V2 Bridge Fields (foundation)
        payload["memory_v2_loaded"] = memory_v2_snapshot.get("available", False)
        payload["memory_v2_procedures_count"] = memory_v2_snapshot.get(
            "procedures_count", 0
        )
        payload["memory_v2_avg_procedure_confidence"] = memory_v2_snapshot.get(
            "avg_procedure_confidence", 0.0
        )
        payload["memory_v2_avg_procedure_confidence"] = memory_v2_snapshot.get(
            "avg_procedure_confidence", 0.0
        )
        payload["memory_v2_contradictions_count"] = memory_v2_snapshot.get(
            "contradictions_count", 0
        )
        payload["memory_v2_reinforced_count"] = memory_v2_snapshot.get(
            "reinforced_count", 0
        )
        payload["memory_v2_suppressed_count"] = memory_v2_snapshot.get(
            "suppressed_count", 0
        )
        payload["memory_v2_retrieval_reason_codes"] = memory_v2_snapshot.get(
            "top_reason_codes", []
        )
        payload["psyche_v2_loaded"] = psyche_v2_snapshot.get("available", False)
        payload["psyche_v2_mode"] = psyche_v2_snapshot.get("mode", "neutral")
        payload["psyche_v2_habit_biases"] = psyche_v2_snapshot.get("habit_biases", [])
        payload["psyche_v2_relation_friction"] = psyche_v2_snapshot.get(
            "relation_friction", 0.0
        )
        payload["psyche_v2_pressure"] = psyche_v2_snapshot.get("pressure", 0.0)
        payload["identity_bridge_loaded"] = identity_bridge_snapshot is not None

        # V2 Real Influence Flags
        payload["memory_influenced_strategy"] = memory_influenced_strategy
        payload["psyche_influenced_strategy"] = psyche_influenced_strategy
        payload["memory_influence_reason_codes"] = memory_reason_codes
        payload["psyche_influence_reason_codes"] = psyche_reason_codes

        # ── V2 Behavior Injection (real runtime influence) ──
        memory_v2_context_injected = bool(
            memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded
        )
        memory_v2_context_item_count = (
            len(memory_v2_runtime_ctx.top_facts)
            + len(memory_v2_runtime_ctx.top_preferences)
            if memory_v2_runtime_ctx
            else 0
        )
        memory_v2_procedure_bias_applied = bool(
            memory_v2_runtime_ctx
            and memory_v2_runtime_ctx.loaded
            and memory_v2_runtime_ctx.confidence_modifier > 0.6
        )
        memory_v2_contradiction_guard_applied = bool(
            memory_v2_runtime_ctx
            and memory_v2_runtime_ctx.loaded
            and memory_v2_runtime_ctx.contradiction_alerts
        )

        psyche_v2_behavior_applied = bool(
            psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded
        )
        psyche_v2_style_mode = (
            psyche_v2_behavior_ctx.mode if psyche_v2_behavior_ctx else "neutral"
        )
        psyche_v2_pressure_applied = bool(
            psyche_v2_behavior_ctx
            and psyche_v2_behavior_ctx.loaded
            and psyche_v2_behavior_ctx.pressure > 0.5
        )
        psyche_v2_relation_tone_applied = bool(
            psyche_v2_behavior_ctx
            and psyche_v2_behavior_ctx.loaded
            and (
                psyche_v2_behavior_ctx.friction > 0.5
                or psyche_v2_behavior_ctx.warmth > 0.7
            )
        )

        final_behavior_profile = {}
        if psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded:
            final_behavior_profile = {
                "mode": psyche_v2_style_mode,
                "directness": psyche_v2_behavior_ctx.directness_bias,
                "verbosity": psyche_v2_behavior_ctx.verbosity_bias,
                "caution": psyche_v2_behavior_ctx.caution_bias,
                "pressure": psyche_v2_behavior_ctx.pressure,
                "trust": psyche_v2_behavior_ctx.trust,
                "friction": psyche_v2_behavior_ctx.friction,
                "warmth": psyche_v2_behavior_ctx.warmth,
                "autonomy": psyche_v2_behavior_ctx.autonomy_bias,
                "structuredness": psyche_v2_behavior_ctx.structuredness_bias,
                "tool_bias": psyche_v2_behavior_ctx.tool_bias,
                "web_bias": psyche_v2_behavior_ctx.web_bias,
            }

        payload["memory_v2_context_injected"] = memory_v2_context_injected
        payload["memory_v2_context_item_count"] = memory_v2_context_item_count
        payload["memory_v2_procedure_bias_applied"] = memory_v2_procedure_bias_applied
        payload["memory_v2_contradiction_guard_applied"] = (
            memory_v2_contradiction_guard_applied
        )
        payload["psyche_v2_behavior_applied"] = psyche_v2_behavior_applied
        payload["psyche_v2_style_mode"] = psyche_v2_style_mode
        payload["psyche_v2_pressure_applied"] = psyche_v2_pressure_applied
        payload["psyche_v2_relation_tone_applied"] = psyche_v2_relation_tone_applied
        payload["final_behavior_profile"] = final_behavior_profile

        return payload


_EXECUTIVE_HOLDER: list[ExecutiveController] = []


def get_executive_controller() -> ExecutiveController:
    """Return singleton ExecutiveController instance."""
    if not _EXECUTIVE_HOLDER:
        _EXECUTIVE_HOLDER.append(ExecutiveController())
    return _EXECUTIVE_HOLDER[0]
