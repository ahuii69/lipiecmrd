#!/usr/bin/env python3

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from aihub.agent_db import (
    enqueue_task,
    ensure_schema,
    get_agent_state,
    list_tasks,
    set_enabled,
)
from aihub.agent_http_surface import (
    FLOW_LOOP,
    FLOW_NONE,
    FLOW_RUN,
    FLOW_TICK,
    ROLE_AGENT_CANONICAL_LOOP,
    ROLE_AGENT_CANONICAL_RUN,
    ROLE_AGENT_DEBUG_GOAL_EVENTS,
    ROLE_AGENT_DEBUG_GOAL_LINKS,
    ROLE_AGENT_OBSERVABILITY_GOAL_TRACE,
    ROLE_AGENT_OBSERVABILITY_STATUS,
    ROLE_AGENT_SECONDARY_GOALS_LIST,
    ROLE_AGENT_SECONDARY_TICK,
    ROLE_AGENT_SECONDARY_WORKER_ENABLE,
    ROLE_AGENT_SECONDARY_WORKER_ENQUEUE,
    ROLE_AGENT_SECONDARY_WORKER_TASKS,
    agent_goal_artifact_http_enabled,
    agent_tick_http_enabled,
    stamp_agent_endpoint,
)
from aihub.executive_controller import (
    build_agent_cycle_response,
    build_agent_loop_response,
    get_executive_controller,
)
from aihub.goal_engine import get_goal_engine
from aihub.psyche_core import get_psyche_core

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentEnableIn(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=128)
    enabled: bool = True


class AgentEnqueueIn(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=50, ge=1, le=1000)


class AgentCycleResponse(BaseModel):
    """Canonical runtime response for all /agent/* execution endpoints.

    Notes:
    - planning_used/reasoning_used are execution facts, not strategy intent.
      Specifically: planning_used = True means FULL planning strategy (planned_reasoning)
      was executed. Note: PlannerEngine lightweight runs for ALL strategies but does
      not set these flags - they indicate FULL planning execution, not planner usage.
    - debug payload is optional and only populated on explicit request.
    - Extended telemetry fields (experience_*, policy_*, simulation_*, etc.) provide
      detailed decision signals and execution metadata.
    """

    ok: bool
    mode: str
    strategy_authority_external: bool = False
    strategy_source: str = "fallback_local"
    strategy: str
    strategy_reason: str
    planning_used: bool
    reasoning_used: bool
    context_signals: dict[str, Any] = Field(default_factory=dict)
    active_goals_summary: list[dict[str, Any]] = Field(default_factory=list)
    selected_goal: dict[str, Any] | None = None
    selected_goal_reason: str = ""
    goal_affected_planning: bool = False
    goal_progress_changed: bool
    execution_summary: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    reflection: dict[str, Any] = Field(default_factory=dict)
    debug: dict[str, Any] | None = None
    response_text: str = ""

    # Extended telemetry (from build_agent_cycle_response)
    experience_lookup_happened: bool = False
    experience_matches_count: int = 0
    experience_influenced_strategy: bool = False
    experience_confidence_adjustment: float | None = None
    experience_blocker_reason: str | None = None
    experience_signal_summary: str = ""
    policy_hints_loaded: bool = False
    policy_profile_name: str = ""
    policy_feedback_loaded: bool = False
    policy_feedback_applied: bool = False
    policy_feedback_summary: str = ""
    policy_confidence_delta: float = 0.0
    policy_blocker_sensitivity: float = 0.0
    policy_simulation_risk_cal: float = 0.0
    policy_strategy_adjustments: dict[str, Any] = Field(default_factory=dict)
    simulation_ran: bool = False
    simulation_variants_count: int = 0
    simulation_best_action: str | None = None
    simulation_risk_summary: str = ""
    reflection_ran: bool = False
    reflection_summary: str = ""
    strategy_fit: str = ""
    confidence_hindsight: float | None = None
    risk_hindsight: float | None = None
    decision_signals_reason_codes: list[str] = Field(default_factory=list)

    # V2 Bridge Telemetry (foundation)
    memory_v2_loaded: bool = False
    memory_v2_procedures_count: int = 0
    memory_v2_avg_procedure_confidence: float = 0.0
    memory_v2_contradictions_count: int = 0
    memory_v2_reinforced_count: int = 0
    memory_v2_suppressed_count: int = 0
    memory_v2_retrieval_reason_codes: list[str] = Field(default_factory=list)
    psyche_v2_loaded: bool = False
    psyche_v2_mode: str = "neutral"
    psyche_v2_habit_biases: list[dict[str, Any]] = Field(default_factory=list)
    psyche_v2_relation_friction: float = 0.0
    psyche_v2_pressure: float = 0.0
    identity_bridge_loaded: bool = False

    # V2 Real Influence (decision impact)
    memory_influenced_strategy: bool = False
    psyche_influenced_strategy: bool = False
    memory_influence_reason_codes: list[str] = Field(default_factory=list)
    psyche_influence_reason_codes: list[str] = Field(default_factory=list)

    # V2 Write-back (outcome capture)
    memory_v2_writeback_attempted: bool = False
    memory_v2_writeback_succeeded: bool = False
    memory_v2_writeback_kind: str | None = None
    memory_v2_new_lessons_count: int = 0
    memory_v2_new_procedures_count: int = 0
    psyche_v2_writeback_attempted: bool = False
    psyche_v2_writeback_succeeded: bool = False
    psyche_v2_event_applied: str | None = None

    # V2 Behavior Injection (real runtime influence)
    memory_v2_context_injected: bool = False
    memory_v2_context_item_count: int = 0
    memory_v2_procedure_bias_applied: bool = False
    memory_v2_contradiction_guard_applied: bool = False
    psyche_v2_behavior_applied: bool = False
    psyche_v2_style_mode: str = "neutral"
    psyche_v2_pressure_applied: bool = False
    psyche_v2_relation_tone_applied: bool = False
    final_behavior_profile: dict[str, Any] = Field(default_factory=dict)


@router.get("/status/{user_id}")
def status(user_id: str, response: Response) -> dict[str, Any]:
    ensure_schema()
    get_psyche_core().ensure_user(user_id)
    stamp_agent_endpoint(
        response, role=ROLE_AGENT_OBSERVABILITY_STATUS, canonical_flow=FLOW_NONE
    )
    return {"state": get_agent_state(user_id)}


@router.post("/enable")
def enable(inp: AgentEnableIn, response: Response) -> dict[str, Any]:
    ensure_schema()
    get_psyche_core().ensure_user(inp.user_id)
    set_enabled(inp.user_id, bool(inp.enabled))
    stamp_agent_endpoint(
        response, role=ROLE_AGENT_SECONDARY_WORKER_ENABLE, canonical_flow=FLOW_NONE
    )
    return {"ok": True, "state": get_agent_state(inp.user_id)}


@router.post("/enqueue")
def enqueue(inp: AgentEnqueueIn, response: Response) -> dict[str, Any]:
    ensure_schema()
    get_psyche_core().ensure_user(inp.user_id)
    tid = enqueue_task(inp.user_id, inp.type, inp.payload, priority=int(inp.priority))
    stamp_agent_endpoint(
        response, role=ROLE_AGENT_SECONDARY_WORKER_ENQUEUE, canonical_flow=FLOW_NONE
    )
    return {"ok": True, "task_id": tid}


@router.get("/tasks/{user_id}")
def tasks(
    user_id: str,
    response: Response,
    task_status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    ensure_schema()
    get_psyche_core().ensure_user(user_id)
    stamp_agent_endpoint(
        response, role=ROLE_AGENT_SECONDARY_WORKER_TASKS, canonical_flow=FLOW_NONE
    )
    return {
        "tasks": list_tasks(user_id, status=task_status, limit=min(int(limit), 200))
    }


@router.post("/tick/{user_id}", response_model=AgentCycleResponse)
async def tick(
    user_id: str,
    response: Response,
    max_stm: int = 200,
    max_tasks: int = 8,
    include_debug: bool = False,
    force_strategy: str | None = Query(
        default=None,
        description="Optional pin; when set, executive uses external authority (no local _select_strategy as source of truth).",
    ),
) -> AgentCycleResponse:
    if not agent_tick_http_enabled():
        raise HTTPException(
            status_code=404,
            detail=(
                "agent tick HTTP disabled; set AIHUB_ENABLE_AGENT_TICK_HTTP=1 to enable"
            ),
        )
    ensure_schema()
    get_psyche_core().ensure_user(user_id)
    try:
        controller = get_executive_controller()
        ev: dict[str, Any] = {"max_stm": int(max_stm), "max_tasks": int(max_tasks)}
        fs = str(force_strategy or "").strip()
        if fs:
            ev["force_strategy"] = fs
        cycle = await controller.run_cycle(
            ev,
            mode="tick",
            user_id=user_id,
        )
        stamp_agent_endpoint(
            response, role=ROLE_AGENT_SECONDARY_TICK, canonical_flow=FLOW_TICK
        )
        return AgentCycleResponse(
            **build_agent_cycle_response(cycle, include_debug=bool(include_debug))
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/run", response_model=AgentCycleResponse)
async def agent_run(data: dict, response: Response) -> AgentCycleResponse:
    """Canonical agent run — shares ExecutiveController via ``agent_runner`` when possible."""
    text = data.get("text", "")
    user_id = data.get("user_id", "default")
    include_debug = bool(data.get("include_debug", False))
    fs = str(data.get("force_strategy", "") or "").strip()
    dry_run = bool(data.get("dry_run", False))
    stamp_agent_endpoint(
        response, role=ROLE_AGENT_CANONICAL_RUN, canonical_flow=FLOW_RUN
    )
    # Sync/async adapter shares one controller path (value previously unused outside this module).
    if not fs and not dry_run:
        from aihub.agent_runner import run_agent_async

        wrapped = await run_agent_async(
            text=text,
            user_id=user_id,
            max_steps=int(data.get("max_steps", 8)),
            timeout_seconds=float(data.get("timeout_seconds", 20.0)),
        )
        payload = wrapped.get("result") if isinstance(wrapped.get("result"), dict) else {}
        if include_debug and isinstance(payload, dict):
            payload = {
                **payload,
                "debug": {
                    **(payload.get("debug") or {}),
                    "runner": "agent_runner",
                    "vector_memory": wrapped.get("vector_memory"),
                },
            }
        try:
            return AgentCycleResponse(**payload)
        except Exception as shape_exc:
            import logging

            logging.getLogger(__name__).debug(
                "agent_runner payload shape fallback: %s", shape_exc
            )
    ev: dict[str, Any] = {
        "text": text,
        "max_steps": int(data.get("max_steps", 8)),
        "timeout_seconds": float(data.get("timeout_seconds", 20.0)),
        "dry_run": dry_run,
    }
    if fs:
        ev["force_strategy"] = fs
    controller = get_executive_controller()
    cycle = await controller.run_cycle(
        ev,
        mode="run",
        user_id=user_id,
    )
    return AgentCycleResponse(
        **build_agent_cycle_response(cycle, include_debug=include_debug)
    )


@router.post("/loop", response_model=AgentCycleResponse)
async def agent_loop(data: dict, response: Response) -> AgentCycleResponse:
    text = data.get("text", "")
    user_id = data.get("user_id", "default")
    max_iters = int(data.get("max_iters", 5))
    dry_run = bool(data.get("dry_run", False))
    include_debug = bool(data.get("include_debug", False))

    controller = get_executive_controller()
    runs: list[dict[str, Any]] = []

    fs = str(data.get("force_strategy", "") or "").strip()
    for iteration in range(max_iters):
        ev: dict[str, Any] = {
            "text": text,
            "dry_run": dry_run,
            "iteration": iteration + 1,
            "max_iters": max_iters,
        }
        if fs:
            ev["force_strategy"] = fs
        cycle = await controller.run_cycle(
            ev,
            mode="loop",
            user_id=user_id,
        )
        runs.append(cycle)
        if not bool(cycle.get("ok", False)):
            break

    stamp_agent_endpoint(
        response, role=ROLE_AGENT_CANONICAL_LOOP, canonical_flow=FLOW_LOOP
    )
    return AgentCycleResponse(
        **build_agent_loop_response(runs, include_debug=include_debug)
    )


@router.get("/goals/{user_id}")
def goals(user_id: str, response: Response, limit: int = 200) -> dict[str, Any]:
    """Secondary listing surface (not the same contract as POST /agent/run)."""
    ensure_schema()
    get_psyche_core().ensure_user(user_id)
    stamp_agent_endpoint(
        response, role=ROLE_AGENT_SECONDARY_GOALS_LIST, canonical_flow=FLOW_NONE
    )
    engine = get_goal_engine()
    rows = [
        g.__dict__
        for g in engine.get_active_goals(user_id)[: max(1, min(int(limit), 2000))]
    ]
    return {
        "ok": True,
        "user_id": user_id,
        "count": len(rows),
        "goals": rows,
    }


@router.get("/goals/{user_id}/{goal_id}/trace")
def goal_trace(user_id: str, goal_id: str, response: Response) -> dict[str, Any]:
    """Observability: aggregated goal trace (cockpit may call via BFF)."""
    ensure_schema()
    get_psyche_core().ensure_user(user_id)
    trace = get_goal_engine().get_goal_trace(user_id=user_id, goal_id=goal_id)
    if not bool(trace.get("ok", False)):
        raise HTTPException(status_code=404, detail="goal not found")
    stamp_agent_endpoint(
        response, role=ROLE_AGENT_OBSERVABILITY_GOAL_TRACE, canonical_flow=FLOW_NONE
    )
    return trace


@router.get("/goals/{user_id}/{goal_id}/links")
def goal_links(
    user_id: str,
    goal_id: str,
    response: Response,
    link_type: str | None = None,
    entity_type: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Debug-oriented goal link rows; disable with AIHUB_ENABLE_AGENT_GOAL_ARTIFACT_HTTP=0."""
    if not agent_goal_artifact_http_enabled():
        raise HTTPException(
            status_code=404,
            detail=(
                "goal artifact HTTP disabled; set "
                "AIHUB_ENABLE_AGENT_GOAL_ARTIFACT_HTTP=1 for /links and /events"
            ),
        )
    ensure_schema()
    get_psyche_core().ensure_user(user_id)
    engine = get_goal_engine()
    trace = engine.get_goal_trace(user_id=user_id, goal_id=goal_id)
    if not bool(trace.get("ok", False)):
        raise HTTPException(status_code=404, detail="goal not found")

    rows = engine.get_goal_links(
        user_id=user_id,
        goal_id=goal_id,
        link_type=link_type,
        entity_type=entity_type,
        limit=max(1, min(int(limit), 5000)),
    )
    stamp_agent_endpoint(
        response, role=ROLE_AGENT_DEBUG_GOAL_LINKS, canonical_flow=FLOW_NONE
    )
    return {
        "ok": True,
        "goal_id": goal_id,
        "count": len(rows),
        "links": rows,
    }


@router.get("/goals/{user_id}/{goal_id}/events")
def goal_events(
    user_id: str,
    goal_id: str,
    response: Response,
    limit: int = 200,
) -> dict[str, Any]:
    """Debug-oriented goal event rows; disable with AIHUB_ENABLE_AGENT_GOAL_ARTIFACT_HTTP=0."""
    if not agent_goal_artifact_http_enabled():
        raise HTTPException(
            status_code=404,
            detail=(
                "goal artifact HTTP disabled; set "
                "AIHUB_ENABLE_AGENT_GOAL_ARTIFACT_HTTP=1 for /links and /events"
            ),
        )
    ensure_schema()
    get_psyche_core().ensure_user(user_id)
    engine = get_goal_engine()
    trace = engine.get_goal_trace(user_id=user_id, goal_id=goal_id)
    if not bool(trace.get("ok", False)):
        raise HTTPException(status_code=404, detail="goal not found")

    rows = engine.get_goal_events(
        user_id=user_id,
        goal_id=goal_id,
        limit=max(1, min(int(limit), 2000)),
    )
    stamp_agent_endpoint(
        response, role=ROLE_AGENT_DEBUG_GOAL_EVENTS, canonical_flow=FLOW_NONE
    )
    return {
        "ok": True,
        "goal_id": goal_id,
        "count": len(rows),
        "events": rows,
    }
