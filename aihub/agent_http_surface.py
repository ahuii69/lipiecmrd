#!/usr/bin/env python3
"""HTTP surface semantics for agent + cognitive routes (headers, env guards).

Canonical user-driven execution lives on ``POST /agent/run``. Multi-iteration
cognitive aggregate is ``POST /agent/loop``. Worker-style cycles use
``POST /agent/tick/{user_id}``. ``GET /cognitive/decide`` is debug-only and
off unless :envvar:`AIHUB_ENABLE_COGNITIVE_DEBUG_ENDPOINT` is set.

Clients should treat :header:`X-AIHub-Endpoint-Role` and
:header:`X-AIHub-Canonical-Agent-Flow` as the runtime truth for what a response
represents — not all JSON shapes are interchangeable.
"""

from __future__ import annotations

import os
from typing import Final

from starlette.responses import Response

HEADER_ENDPOINT_ROLE: Final = "X-AIHub-Endpoint-Role"
HEADER_CANONICAL_AGENT_FLOW: Final = "X-AIHub-Canonical-Agent-Flow"
HEADER_COGNITIVE_SURFACE: Final = "X-AIHub-Cognitive-Surface"

# --- Agent /agent/* roles (single string per response; stable contract) ---
ROLE_AGENT_CANONICAL_RUN: Final = "agent-canonical-run"
ROLE_AGENT_CANONICAL_LOOP: Final = "agent-canonical-loop"
ROLE_AGENT_SECONDARY_TICK: Final = "agent-secondary-tick"
ROLE_AGENT_OBSERVABILITY_STATUS: Final = "agent-observability-status"
ROLE_AGENT_SECONDARY_WORKER_ENABLE: Final = "agent-secondary-worker-enable"
ROLE_AGENT_SECONDARY_WORKER_ENQUEUE: Final = "agent-secondary-worker-enqueue"
ROLE_AGENT_SECONDARY_WORKER_TASKS: Final = "agent-secondary-worker-tasks"
ROLE_AGENT_SECONDARY_GOALS_LIST: Final = "agent-secondary-goals-list"
ROLE_AGENT_OBSERVABILITY_GOAL_TRACE: Final = "agent-observability-goal-trace"
ROLE_AGENT_DEBUG_GOAL_LINKS: Final = "agent-debug-goal-links"
ROLE_AGENT_DEBUG_GOAL_EVENTS: Final = "agent-debug-goal-events"

FLOW_RUN: Final = "run"
FLOW_LOOP: Final = "loop"
FLOW_TICK: Final = "tick"
FLOW_NONE: Final = "none"

COGNITIVE_OBSERVABILITY_HEALTH: Final = "cognitive-observability-health"
COGNITIVE_DEBUG_DECIDE: Final = "cognitive-debug-decide"


def _env_flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip() == "1"


def agent_tick_http_enabled() -> bool:
    """When false, ``POST /agent/tick/{user_id}`` returns 404 (worker tick disabled)."""

    return _env_flag("AIHUB_ENABLE_AGENT_TICK_HTTP", "1")


def agent_goal_artifact_http_enabled() -> bool:
    """When false, goal links/events GET endpoints return 404."""

    return _env_flag("AIHUB_ENABLE_AGENT_GOAL_ARTIFACT_HTTP", "1")


def stamp_agent_endpoint(
    response: Response,
    *,
    role: str,
    canonical_flow: str = FLOW_NONE,
) -> None:
    response.headers[HEADER_ENDPOINT_ROLE] = role
    response.headers[HEADER_CANONICAL_AGENT_FLOW] = canonical_flow


def stamp_cognitive_observability_health(response: Response) -> None:
    response.headers[HEADER_COGNITIVE_SURFACE] = COGNITIVE_OBSERVABILITY_HEALTH
    response.headers[HEADER_CANONICAL_AGENT_FLOW] = FLOW_NONE


def stamp_cognitive_debug_decide(response: Response) -> None:
    response.headers[HEADER_COGNITIVE_SURFACE] = COGNITIVE_DEBUG_DECIDE
    response.headers[HEADER_ENDPOINT_ROLE] = "cognitive-debug-decide"
    response.headers[HEADER_CANONICAL_AGENT_FLOW] = FLOW_NONE
