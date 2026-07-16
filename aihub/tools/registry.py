#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Central capability registry exposing stable adapters over AI-Hub internals."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from aihub import config
from aihub.chat_contracts import CapabilityDescriptor
from aihub.chat_image_generation import ImageGenerateIn, tool_image_generate_handler
from aihub.db import fetch_all, get_events_since, json_loads
from aihub.executive_controller import (
    build_agent_cycle_response,
    get_executive_controller,
)
from aihub.goal_engine import GoalCandidate, GoalUpdate, get_goal_engine
from aihub.memory_core import get_memory_core
from aihub.memory_engine import health
from aihub.metrics_engine import get_alert_status, get_system_health
from aihub.planner_engine import build_task_graph, plan
from aihub.psyche_core import get_psyche_core
from aihub.research_engine import research
from aihub.system_ops import create_snapshot
from aihub.tools.policies import can_view_tool
from aihub.tools.types import ToolDefinition, ToolExecutionContext, ToolMode
from aihub import web_tools as _web_tools


async def fetch_url(user_id: str, url: str) -> dict[str, Any]:
    """Runtime-resolved web fetch wrapper.

    Tests and integrations may monkeypatch either ``aihub.web_tools.fetch_url``
    or this wrapper; the default path stays dynamic instead of freezing the
    imported function at registry import time.
    """
    return await _web_tools.fetch_url(user_id, url)

logger = logging.getLogger(__name__)


class ToolEnvelopeOut(BaseModel):
    ok: bool = True
    result: Dict[str, Any] = Field(default_factory=dict)


class NoInput(BaseModel):
    """Input model for tools that do not require arguments."""


class MemorySearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=5000)
    limit: int = Field(default=10, ge=1, le=100)


class MemoryContextIn(BaseModel):
    query: str = Field(default="", max_length=5000)
    limit: int = Field(default=8, ge=1, le=100)


class MemoryAddFactIn(BaseModel):
    fact: str = Field(min_length=1, max_length=200000)
    tags: List[str] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


class MemoryAddEpisodeIn(BaseModel):
    summary: str = Field(min_length=1, max_length=200000)
    meta: Dict[str, Any] = Field(default_factory=dict)


class MemoryProcessTurnIn(BaseModel):
    user_msg: str = Field(min_length=1, max_length=200000)
    assistant_msg: str = Field(min_length=1, max_length=200000)
    intent: str = Field(default="chat", min_length=1, max_length=64)
    meta: Dict[str, Any] = Field(default_factory=dict)


class MemoryProceduresIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    extract: bool = False


class KnowledgeLookupIn(BaseModel):
    query: str = Field(min_length=1, max_length=5000)


class ConsistencyCheckIn(BaseModel):
    text: str = Field(min_length=1, max_length=200000)


class GoalCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=2000)
    goal_type: str = Field(default="task", min_length=1, max_length=64)
    source: str = Field(default="chat_runtime", min_length=1, max_length=128)
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    urgency: float = Field(default=0.5, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    tags: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)
    failure_criteria: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GoalUpdateIn(BaseModel):
    goal_id: str = Field(min_length=1, max_length=128)
    status: Optional[str] = None
    priority: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    urgency: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    progress: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    metadata: Optional[Dict[str, Any]] = None
    reason: str = "chat_update"


class GoalIdIn(BaseModel):
    goal_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="chat_action", max_length=300)


class PlannerPreviewIn(BaseModel):
    text: str = Field(min_length=1, max_length=200000)


class PlannerGraphIn(BaseModel):
    text: str = Field(min_length=1, max_length=200000)
    include_context: bool = True


class ReasoningPreviewIn(BaseModel):
    text: str = Field(min_length=1, max_length=200000)


class AgentRunCycleIn(BaseModel):
    mode: ToolMode = "agent"
    input_event: Dict[str, Any] = Field(default_factory=dict)
    include_debug: bool = False


class ResearchQueryIn(BaseModel):
    query: str = Field(min_length=1, max_length=5000)
    research_type: str = Field(default="general", max_length=64)


class ResearchUrlIn(BaseModel):
    url: str = Field(min_length=5, max_length=8000)


class WebFetchIn(BaseModel):
    url: str = Field(min_length=5, max_length=8000)


class WebIngestIn(BaseModel):
    url: str = Field(min_length=5, max_length=8000)
    importance: float = Field(default=0.6, ge=0.0, le=1.0)
    confidence: float = Field(default=0.72, ge=0.0, le=1.0)
    session_id: str | None = Field(default=None, max_length=256)


class PsycheReflectIn(BaseModel):
    query: str = Field(default="", max_length=5000)
    limit: int = Field(default=10, ge=1, le=50)


class PsycheSentimentIn(BaseModel):
    text: str = Field(min_length=1, max_length=200000)


class PsycheEvolveIn(BaseModel):
    text: str = Field(min_length=1, max_length=200000)
    role: str = Field(default="user", pattern="^(user|assistant|system)$")


class RuntimeCapabilitiesIn(BaseModel):
    mode: ToolMode = "chat"
    include_debug: bool = False


class RuntimeTraceIn(BaseModel):
    limit: int = Field(default=5, ge=1, le=100)


class FSReadIn(BaseModel):
    path: str = Field(min_length=1, max_length=5000)
    max_bytes: int = Field(default=200000, ge=1, le=5_000_000)


class FSWriteIn(BaseModel):
    path: str = Field(min_length=1, max_length=5000)
    content: str = Field(default="", max_length=5_000_000)
    overwrite: bool = True


class SnapshotCreateIn(BaseModel):
    reason: str = Field(default="chat_tool", min_length=1, max_length=200)


class DebugEventsIn(BaseModel):
    limit: int = Field(default=20, ge=1, le=200)


class ToolRegistry:
    """In-memory capability registry with centralized policy filtering."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._register_defaults()

    def register(self, tool: ToolDefinition) -> None:
        key = str(tool.name).strip()
        if not key:
            raise ValueError("tool.name is required")
        self._tools[key] = tool

    def get(self, name: str) -> ToolDefinition:
        key = str(name).strip()
        if key not in self._tools:
            raise KeyError(f"tool not found: {name}")
        return self._tools[key]

    def list_capabilities(
        self,
        *,
        mode: ToolMode,
        include_debug: bool,
        policy_overrides: Optional[Dict[str, Any]] = None,
    ) -> List[CapabilityDescriptor]:
        out: List[CapabilityDescriptor] = []
        for tool in sorted(self._tools.values(), key=lambda t: t.name):
            decision = can_view_tool(
                tool,
                mode=mode,
                include_debug=include_debug,
                policy_overrides=dict(policy_overrides or {}),
            )
            if decision.allowed:
                out.append(tool.to_descriptor())
        return out

    def _register_defaults(self) -> None:
        from aihub.fs_tools import read_file, write_file

        # ---- memory.* ----
        async def _memory_search(
            ctx: ToolExecutionContext, inp: MemorySearchIn
        ) -> Dict[str, Any]:
            get_psyche_core().ensure_user(ctx.user_id)
            data = get_memory_core().retrieve_unified(
                ctx.user_id, inp.query, limit=inp.limit
            )
            return {"ok": True, "result": data}

        async def _memory_context(
            ctx: ToolExecutionContext, inp: MemoryContextIn
        ) -> Dict[str, Any]:
            get_psyche_core().ensure_user(ctx.user_id)
            data = get_memory_core().retrieve_unified(
                ctx.user_id, inp.query, limit=inp.limit
            )
            return {"ok": True, "result": data}

        async def _memory_add_fact(
            ctx: ToolExecutionContext, inp: MemoryAddFactIn
        ) -> Dict[str, Any]:
            get_psyche_core().ensure_user(ctx.user_id)
            fact_id = get_memory_core().ingest_fact(
                ctx.user_id,
                inp.fact,
                tags=list(inp.tags or []),
                meta=dict(inp.meta or {}),
            )
            return {"ok": True, "result": {"fact_id": fact_id}}

        async def _memory_add_episode(
            ctx: ToolExecutionContext, inp: MemoryAddEpisodeIn
        ) -> Dict[str, Any]:
            get_psyche_core().ensure_user(ctx.user_id)
            episode_id = get_memory_core().ingest_episode(
                ctx.user_id,
                inp.summary,
                dict(inp.meta or {}),
            )
            return {"ok": True, "result": {"episode_id": episode_id}}

        async def _memory_process_turn(
            ctx: ToolExecutionContext, inp: MemoryProcessTurnIn
        ) -> Dict[str, Any]:
            get_psyche_core().ensure_user(ctx.user_id)
            out = get_memory_core().ingest_turn(
                ctx.user_id,
                inp.user_msg,
                inp.assistant_msg,
                inp.intent,
                inp.meta,
            )
            return {"ok": True, "result": out}

        self.register(
            ToolDefinition(
                name="memory.search",
                description="Search episodic/semantic memory and return ranked context.",
                capability_group="memory",
                input_model=MemorySearchIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=20.0,
                visibility=["chat", "agent", "readonly", "debug"],
                handler=_memory_search,
            )
        )
        self.register(
            ToolDefinition(
                name="memory.get_context",
                description="Get contextual memory package for a query.",
                capability_group="memory",
                input_model=MemoryContextIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=20.0,
                visibility=["chat", "agent", "readonly", "debug"],
                handler=_memory_context,
            )
        )
        self.register(
            ToolDefinition(
                name="memory.add_fact",
                description="Persist a semantic fact into memory layer L2.",
                capability_group="memory",
                input_model=MemoryAddFactIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=False,
                requires_confirmation=False,
                timeout_seconds=15.0,
                visibility=["chat", "agent", "debug"],
                handler=_memory_add_fact,
            )
        )
        self.register(
            ToolDefinition(
                name="memory.add_episode",
                description="Persist an episodic memory item into layer L1.",
                capability_group="memory",
                input_model=MemoryAddEpisodeIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=False,
                requires_confirmation=False,
                timeout_seconds=15.0,
                visibility=["chat", "agent", "debug"],
                handler=_memory_add_episode,
            )
        )
        self.register(
            ToolDefinition(
                name="memory.process_turn",
                description="Process user+assistant turn through memory pipeline.",
                capability_group="memory",
                input_model=MemoryProcessTurnIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=False,
                requires_confirmation=False,
                timeout_seconds=20.0,
                visibility=["chat", "agent", "debug"],
                handler=_memory_process_turn,
            )
        )

        async def _memory_list_procedures(
            ctx: ToolExecutionContext, inp: MemoryProceduresIn
        ) -> Dict[str, Any]:
            core = get_memory_core()
            extracted = 0
            if inp.extract:
                procs_new = core.v2_extract_procedures(ctx.user_id)
                extracted = len(procs_new or [])
            procs = core.v2_list_procedures(ctx.user_id, limit=inp.limit)
            items = []
            for p in procs or []:
                items.append(
                    {
                        "id": getattr(p, "id", None),
                        "name": getattr(p, "name", None) or getattr(p, "title", None),
                        "strategy": getattr(p, "recommended_strategy", None),
                        "confidence": getattr(p, "confidence_score", None),
                        "success_rate": getattr(p, "success_rate", None),
                    }
                )
            return {
                "ok": True,
                "result": {
                    "procedures": items,
                    "extracted_now": extracted,
                    "count": len(items),
                },
            }

        self.register(
            ToolDefinition(
                name="memory.list_procedures",
                description="List learned procedural workflows; optionally extract new ones from experience.",
                capability_group="memory",
                input_model=MemoryProceduresIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=False,
                requires_confirmation=False,
                timeout_seconds=25.0,
                visibility=["chat", "agent", "debug"],
                handler=_memory_list_procedures,
            )
        )

        async def _knowledge_lookup(
            ctx: ToolExecutionContext, inp: KnowledgeLookupIn
        ) -> Dict[str, Any]:
            from aihub.world_knowledge.engine import retrieve_knowledge_context

            kctx = retrieve_knowledge_context(
                user_id=ctx.user_id, message=inp.query, session_id=ctx.session_id or ""
            )
            return {
                "ok": True,
                "result": {
                    "entities": [e.canonical_name for e in (kctx.entities or [])[:8]],
                    "claims": [
                        {
                            "id": c.claim_id,
                            "statement": c.statement[:200],
                            "confidence": c.confidence,
                            "status": c.status,
                        }
                        for c in (kctx.claims or [])[:8]
                    ],
                    "relations": [r.predicate for r in (kctx.relations or [])[:8]],
                    "verification_required": bool(kctx.verification_required),
                    "disputed": list(kctx.disputed_claims or [])[:4],
                    "stale": list(kctx.stale_claims or [])[:4],
                },
            }

        self.register(
            ToolDefinition(
                name="knowledge.lookup",
                description="Lookup world-knowledge entities, claims and relation hints for a query.",
                capability_group="knowledge",
                input_model=KnowledgeLookupIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=15.0,
                visibility=["chat", "agent", "debug"],
                handler=_knowledge_lookup,
            )
        )

        async def _consistency_check(
            ctx: ToolExecutionContext, inp: ConsistencyCheckIn
        ) -> Dict[str, Any]:
            from aihub.consistency_engine import check_consistency

            verdict = check_consistency(ctx.user_id, inp.text)
            return {
                "ok": True,
                "result": {
                    "classification": verdict.classification,
                    "reasoning": getattr(verdict, "reasoning", "") or "",
                    "confidence": getattr(verdict, "confidence", None),
                },
            }

        self.register(
            ToolDefinition(
                name="consistency.check",
                description="Check whether a statement conflicts with stored memory/knowledge.",
                capability_group="memory",
                input_model=ConsistencyCheckIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=12.0,
                visibility=["chat", "agent", "debug"],
                handler=_consistency_check,
            )
        )

        # ---- goal.* ----
        async def _goal_list_active(
            ctx: ToolExecutionContext, _inp: NoInput
        ) -> Dict[str, Any]:
            engine = get_goal_engine()
            goals = [g.__dict__ for g in engine.get_active_goals(ctx.user_id)]
            return {"ok": True, "result": {"goals": goals}}

        async def _goal_create(
            ctx: ToolExecutionContext, inp: GoalCreateIn
        ) -> Dict[str, Any]:
            engine = get_goal_engine()
            candidate = GoalCandidate(
                user_id=ctx.user_id,
                title=inp.title,
                description=inp.description,
                goal_type=inp.goal_type,
                source=inp.source,
                priority=inp.priority,
                urgency=inp.urgency,
                importance=inp.importance,
                confidence=inp.confidence,
                tags=inp.tags,
                success_criteria=inp.success_criteria,
                failure_criteria=inp.failure_criteria,
                metadata=inp.metadata,
            )
            goal = engine.create_goal(candidate)
            return {
                "ok": True,
                "result": {"goal_id": goal.goal_id, "status": goal.status},
            }

        async def _goal_update(
            ctx: ToolExecutionContext, inp: GoalUpdateIn
        ) -> Dict[str, Any]:
            engine = get_goal_engine()
            updated = engine.update_goal(
                GoalUpdate(
                    user_id=ctx.user_id,
                    goal_id=inp.goal_id,
                    status=inp.status,
                    priority=inp.priority,
                    urgency=inp.urgency,
                    importance=inp.importance,
                    confidence=inp.confidence,
                    progress=inp.progress,
                    metadata=inp.metadata,
                    reason=inp.reason,
                )
            )
            return {
                "ok": True,
                "result": {
                    "goal_id": updated.goal_id,
                    "status": updated.status,
                    "progress": updated.progress,
                },
            }

        async def _goal_complete(
            ctx: ToolExecutionContext, inp: GoalIdIn
        ) -> Dict[str, Any]:
            engine = get_goal_engine()
            goal = engine.complete_goal(ctx.user_id, inp.goal_id, reason=inp.reason)
            return {
                "ok": True,
                "result": {"goal_id": goal.goal_id, "status": goal.status},
            }

        async def _goal_fail(
            ctx: ToolExecutionContext, inp: GoalIdIn
        ) -> Dict[str, Any]:
            engine = get_goal_engine()
            goal = engine.fail_goal(ctx.user_id, inp.goal_id, reason=inp.reason)
            return {
                "ok": True,
                "result": {"goal_id": goal.goal_id, "status": goal.status},
            }

        async def _goal_trace(
            ctx: ToolExecutionContext, inp: GoalIdIn
        ) -> Dict[str, Any]:
            engine = get_goal_engine()
            trace = engine.get_goal_trace(user_id=ctx.user_id, goal_id=inp.goal_id)
            return {"ok": True, "result": trace}

        for name, description, model_in, handler, read_only in [
            (
                "goal.list_active",
                "List currently active/scheduled/blocked goals.",
                NoInput,
                _goal_list_active,
                True,
            ),
            (
                "goal.create",
                "Create a new persistent goal.",
                GoalCreateIn,
                _goal_create,
                False,
            ),
            (
                "goal.update",
                "Update existing goal attributes/status/progress.",
                GoalUpdateIn,
                _goal_update,
                False,
            ),
            (
                "goal.complete",
                "Mark goal as completed.",
                GoalIdIn,
                _goal_complete,
                False,
            ),
            ("goal.fail", "Mark goal as failed.", GoalIdIn, _goal_fail, False),
            (
                "goal.trace",
                "Fetch complete trace for a goal.",
                GoalIdIn,
                _goal_trace,
                True,
            ),
        ]:
            self.register(
                ToolDefinition(
                    name=name,
                    description=description,
                    capability_group="goal",
                    input_model=model_in,
                    output_model=ToolEnvelopeOut,
                    enabled=True,
                    read_only=read_only,
                    requires_confirmation=False,
                    timeout_seconds=20.0,
                    visibility=(
                        ["chat", "agent", "readonly", "debug"]
                        if read_only
                        else ["chat", "agent", "debug"]
                    ),
                    handler=handler,
                )
            )

        # ---- planner.* ----
        async def _planner_preview(
            _ctx: ToolExecutionContext, inp: PlannerPreviewIn
        ) -> Dict[str, Any]:
            tasks = plan(inp.text)
            return {"ok": True, "result": {"tasks": tasks, "count": len(tasks)}}

        async def _planner_build_graph(
            ctx: ToolExecutionContext, inp: PlannerGraphIn
        ) -> Dict[str, Any]:
            mem_ctx = (
                get_memory_core().retrieve_unified(ctx.user_id, inp.text, limit=8)
                if inp.include_context
                else {}
            )
            result = build_task_graph(
                message=inp.text,
                memory_context=mem_ctx,
                user_id=ctx.user_id,
            )
            return {
                "ok": True,
                "result": {
                    "summary": result.summary,
                    "graph": result.graph.serialize(),
                },
            }

        self.register(
            ToolDefinition(
                name="planner.preview",
                description="Preview planner task list (legacy-compatible shape).",
                capability_group="planner",
                input_model=PlannerPreviewIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=20.0,
                visibility=["chat", "agent", "readonly", "debug"],
                handler=_planner_preview,
            )
        )
        self.register(
            ToolDefinition(
                name="planner.build_task_graph",
                description="Build full dependency graph from message and context.",
                capability_group="planner",
                input_model=PlannerGraphIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=20.0,
                visibility=["agent", "debug", "chat", "readonly"],
                handler=_planner_build_graph,
            )
        )

        # ---- reasoning.* ----
        async def _reasoning_preview(
            ctx: ToolExecutionContext, inp: ReasoningPreviewIn
        ) -> Dict[str, Any]:
            mem_ctx = get_memory_core().retrieve_unified(ctx.user_id, inp.text, limit=8)
            planned = build_task_graph(
                message=inp.text,
                memory_context=mem_ctx,
                user_id=ctx.user_id,
            )
            return {
                "ok": True,
                "result": {
                    "preview_only": True,
                    "planner_summary": planned.summary,
                    "graph": planned.graph.serialize(),
                },
            }

        self.register(
            ToolDefinition(
                name="reasoning.run_preview",
                description="Preview reasoning plan without executing tool actions.",
                capability_group="reasoning",
                input_model=ReasoningPreviewIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=20.0,
                visibility=["agent", "debug", "chat", "readonly"],
                handler=_reasoning_preview,
            )
        )

        # ---- agent.* ----
        async def _agent_run_cycle(
            ctx: ToolExecutionContext, inp: AgentRunCycleIn
        ) -> Dict[str, Any]:
            mode = "run" if inp.mode == "agent" else inp.mode
            controller = get_executive_controller()
            cycle = await controller.run_cycle(
                inp.input_event, mode=mode, user_id=ctx.user_id
            )
            return {
                "ok": True,
                "result": build_agent_cycle_response(
                    cycle, include_debug=inp.include_debug
                ),
            }

        self.register(
            ToolDefinition(
                name="agent.run_cycle",
                description="Execute one canonical executive cycle.",
                capability_group="agent",
                input_model=AgentRunCycleIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=False,
                requires_confirmation=False,
                timeout_seconds=45.0,
                visibility=["agent", "debug", "chat"],
                handler=_agent_run_cycle,
            )
        )

        # ---- research.* + web.* ----
        async def _research_query(
            ctx: ToolExecutionContext, inp: ResearchQueryIn
        ) -> Dict[str, Any]:
            out = await research(
                ctx.user_id, inp.query, research_type=inp.research_type
            )
            return {"ok": True, "result": out}

        async def _research_url(
            ctx: ToolExecutionContext, inp: ResearchUrlIn
        ) -> Dict[str, Any]:
            out = await fetch_url(ctx.user_id, inp.url)
            summary = {
                "url": out.get("url", inp.url),
                "status": out.get("status", 0),
                "bytes": out.get("bytes", 0),
                "preview": str(out.get("text", ""))[:400],
            }
            return {"ok": True, "result": summary}

        async def _web_fetch(
            ctx: ToolExecutionContext, inp: WebFetchIn
        ) -> Dict[str, Any]:
            out = await fetch_url(ctx.user_id, inp.url)
            return {"ok": True, "result": out}

        async def _web_ingest(
            ctx: ToolExecutionContext, inp: WebIngestIn
        ) -> Dict[str, Any]:
            from aihub.web_tools import ingest_url

            out = await ingest_url(
                ctx.user_id,
                inp.url,
                importance=inp.importance,
                confidence=inp.confidence,
                session_id=inp.session_id,
            )
            return {"ok": True, "result": out}

        for name, description, model_in, handler in [
            (
                "research.query",
                "Search the web (Brave + optional Wikipedia/DDG). Returns titles, URLs, snippets. "
                "For full page text after you pick a URL, call web.fetch_url with that URL.",
                ResearchQueryIn,
                _research_query,
            ),
            (
                "research.url",
                "Fetch one HTTPS URL and return a short preview (same HTTP stack as web.fetch_url).",
                ResearchUrlIn,
                _research_url,
            ),
            (
                "web.fetch_url",
                "Canonical tool to fetch one page by URL (HTML/text, size-capped, SSRF-guarded). "
                "Registered as web.fetch_url; bare fetch_url is accepted as an alias.",
                WebFetchIn,
                _web_fetch,
            ),
        ]:
            self.register(
                ToolDefinition(
                    name=name,
                    description=description,
                    capability_group=(
                        "research" if name.startswith("research") else "web"
                    ),
                    input_model=model_in,
                    output_model=ToolEnvelopeOut,
                    enabled=True,
                    read_only=True,
                    requires_confirmation=False,
                    timeout_seconds=30.0,
                    visibility=["chat", "agent", "readonly", "debug"],
                    handler=handler,
                )
            )


        self.register(
            ToolDefinition(
                name="web.ingest_url",
                description="Fetch one page and store extracted text into canonical memory with vector indexing.",
                capability_group="web",
                input_model=WebIngestIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=False,
                requires_confirmation=False,
                timeout_seconds=35.0,
                visibility=["agent", "debug"],
                handler=_web_ingest,
            )
        )

        # ---- image.* ----
        self.register(
            ToolDefinition(
                name="image.generate",
                description=(
                    "Build a ready-to-paste English prompt for DALL·E, Stable Diffusion, or Midjourney "
                    "from the user's idea. Always returns prompt_en, description_pl, negative_prompt — "
                    "use for any draw/image request; never refuse generically."
                ),
                capability_group="image",
                input_model=ImageGenerateIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=5.0,
                visibility=["chat", "agent", "readonly", "debug"],
                handler=tool_image_generate_handler,
            )
        )

        # ---- psyche.* ----
        async def _psyche_reflect(
            ctx: ToolExecutionContext, inp: PsycheReflectIn
        ) -> Dict[str, Any]:
            mem_ctx = get_memory_core().retrieve_unified(
                ctx.user_id, inp.query, limit=inp.limit
            )
            out = get_psyche_core().reflect(ctx.user_id, mem_ctx.get("stm", []))
            return {"ok": True, "result": out}

        async def _psyche_sentiment(
            _ctx: ToolExecutionContext, inp: PsycheSentimentIn
        ) -> Dict[str, Any]:
            sentiment, confidence, meta = get_psyche_core().analyze_sentiment(inp.text)
            return {
                "ok": True,
                "result": {
                    "sentiment": sentiment,
                    "confidence": confidence,
                    "meta": meta,
                },
            }

        async def _psyche_evolve(
            ctx: ToolExecutionContext, inp: PsycheEvolveIn
        ) -> Dict[str, Any]:
            out = get_psyche_core().evolve(ctx.user_id, inp.text, inp.role)
            return {"ok": True, "result": out}

        self.register(
            ToolDefinition(
                name="psyche.reflect",
                description="Generate psyche reflection from recent context.",
                capability_group="psyche",
                input_model=PsycheReflectIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=False,
                requires_confirmation=False,
                timeout_seconds=20.0,
                visibility=["chat", "agent", "debug"],
                handler=_psyche_reflect,
            )
        )
        self.register(
            ToolDefinition(
                name="psyche.analyze_sentiment",
                description="Analyze sentiment for a text payload.",
                capability_group="psyche",
                input_model=PsycheSentimentIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=10.0,
                visibility=["chat", "agent", "readonly", "debug"],
                handler=_psyche_sentiment,
            )
        )
        self.register(
            ToolDefinition(
                name="psyche.evolve_state",
                description="Apply psyche evolution based on text+role signal.",
                capability_group="psyche",
                input_model=PsycheEvolveIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=False,
                requires_confirmation=False,
                timeout_seconds=10.0,
                visibility=["chat", "agent", "debug"],
                handler=_psyche_evolve,
            )
        )

        # ---- runtime.* ----
        async def _runtime_status(
            ctx: ToolExecutionContext, _inp: NoInput
        ) -> Dict[str, Any]:
            get_psyche_core().ensure_user(ctx.user_id)
            mem = health(ctx.user_id)
            sys_health = get_system_health()
            alerts = get_alert_status()
            goals = get_goal_engine().get_active_goals(ctx.user_id)
            return {
                "ok": True,
                "result": {
                    "memory": mem,
                    "goals_active": len(goals),
                    "system_health": {
                        "latency_ms": sys_health.latency_ms,
                        "error_rate": sys_health.error_rate,
                        "rps": sys_health.requests_per_second,
                    },
                    "alerts": alerts,
                },
            }

        async def _runtime_trace_last_cycle(
            ctx: ToolExecutionContext, inp: RuntimeTraceIn
        ) -> Dict[str, Any]:
            rows = fetch_all(
                "SELECT id, type, data, ts FROM event_log WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (ctx.user_id, int(inp.limit)),
            )
            items = [
                {
                    "id": int(r["id"]),
                    "type": str(r["type"]),
                    "data": json_loads(r["data"]) or {},
                    "ts": float(r["ts"]),
                }
                for r in rows
            ]
            return {"ok": True, "result": {"events": items}}

        async def _runtime_get_caps(
            ctx: ToolExecutionContext, inp: RuntimeCapabilitiesIn
        ) -> Dict[str, Any]:
            caps = self.list_capabilities(
                mode=inp.mode,
                include_debug=bool(inp.include_debug or ctx.include_debug),
                policy_overrides=dict(ctx.policy_overrides or {}),
            )
            return {
                "ok": True,
                "result": {
                    "count": len(caps),
                    "capabilities": [c.model_dump() for c in caps],
                },
            }

        self.register(
            ToolDefinition(
                name="runtime.status",
                description="Get runtime/memory/metrics status for current user.",
                capability_group="runtime",
                input_model=NoInput,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=10.0,
                visibility=["chat", "agent", "readonly", "debug"],
                handler=_runtime_status,
            )
        )
        self.register(
            ToolDefinition(
                name="runtime.trace_last_cycle",
                description="Inspect recent event trace for current user.",
                capability_group="runtime",
                input_model=RuntimeTraceIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=10.0,
                visibility=["chat", "agent", "readonly", "debug"],
                handler=_runtime_trace_last_cycle,
            )
        )
        self.register(
            ToolDefinition(
                name="runtime.get_capabilities",
                description="List capabilities visible under selected policy mode.",
                capability_group="runtime",
                input_model=RuntimeCapabilitiesIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=10.0,
                visibility=["chat", "agent", "readonly", "debug"],
                handler=_runtime_get_caps,
            )
        )

        # ---- fs.* ----
        async def _fs_read(ctx: ToolExecutionContext, inp: FSReadIn) -> Dict[str, Any]:
            out = read_file(ctx.user_id, inp.path, max_bytes=inp.max_bytes)
            return {"ok": True, "result": out}

        async def _fs_write(
            ctx: ToolExecutionContext, inp: FSWriteIn
        ) -> Dict[str, Any]:
            out = write_file(
                ctx.user_id, inp.path, inp.content, overwrite=inp.overwrite
            )
            return {"ok": True, "result": out}

        self.register(
            ToolDefinition(
                name="fs.read_file",
                description="Read file from sandboxed FS root.",
                capability_group="fs",
                input_model=FSReadIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=10.0,
                visibility=["chat", "agent", "readonly", "debug"],
                handler=_fs_read,
            )
        )
        self.register(
            ToolDefinition(
                name="fs.write_file",
                description="Write file into sandboxed FS root.",
                capability_group="fs",
                input_model=FSWriteIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=False,
                requires_confirmation=True,
                timeout_seconds=10.0,
                visibility=["chat", "agent", "debug"],
                handler=_fs_write,
            )
        )

        # ---- system.* ----
        async def _snapshot_create(
            ctx: ToolExecutionContext, inp: SnapshotCreateIn
        ) -> Dict[str, Any]:
            out = create_snapshot(ctx.user_id, inp.reason)
            return {"ok": True, "result": out}

        async def _system_health(
            _ctx: ToolExecutionContext, _inp: NoInput
        ) -> Dict[str, Any]:
            sys_health = get_system_health()
            alerts = get_alert_status()
            return {
                "ok": True,
                "result": {
                    "latency_ms": sys_health.latency_ms,
                    "error_rate": sys_health.error_rate,
                    "requests_per_second": sys_health.requests_per_second,
                    "alerts": alerts,
                },
            }

        async def _system_debug(
            _ctx: ToolExecutionContext, _inp: NoInput
        ) -> Dict[str, Any]:
            return {
                "ok": True,
                "result": {
                    "app_name": config.APP_NAME,
                    "host": config.HOST,
                    "port": config.PORT,
                    "db_path": str(config.DB_PATH),
                    "data_dir": str(config.DATA_DIR),
                    "provider": config.LLM_PROVIDER_NAME,
                    "model": config.LLM_MODEL_NAME,
                    "tool_calling_enabled": config.LLM_TOOL_CALLING_ENABLED,
                    "streaming_enabled": config.LLM_STREAMING_ENABLED,
                },
            }

        self.register(
            ToolDefinition(
                name="snapshot.create",
                description="Create a DB snapshot for rollback safety.",
                capability_group="system",
                input_model=SnapshotCreateIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=False,
                requires_confirmation=True,
                timeout_seconds=20.0,
                visibility=["agent", "debug", "chat"],
                handler=_snapshot_create,
            )
        )
        self.register(
            ToolDefinition(
                name="system.health",
                description="Read-only system health summary.",
                capability_group="system",
                input_model=NoInput,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=10.0,
                visibility=["chat", "agent", "readonly", "debug"],
                handler=_system_health,
            )
        )
        self.register(
            ToolDefinition(
                name="system.debug_info",
                description="Restricted low-level runtime diagnostics.",
                capability_group="system",
                input_model=NoInput,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=10.0,
                visibility=["debug"],
                handler=_system_debug,
            )
        )

        # ---- debug.* ----
        async def _debug_last_events(
            ctx: ToolExecutionContext, inp: DebugEventsIn
        ) -> Dict[str, Any]:
            events = get_events_since(ctx.user_id, 0, limit=inp.limit)
            return {"ok": True, "result": {"events": events}}

        self.register(
            ToolDefinition(
                name="debug.last_events",
                description="Debug-only event log dump for current user.",
                capability_group="debug",
                input_model=DebugEventsIn,
                output_model=ToolEnvelopeOut,
                enabled=True,
                read_only=True,
                requires_confirmation=False,
                timeout_seconds=10.0,
                visibility=["debug"],
                handler=_debug_last_events,
            )
        )


_registry = ToolRegistry()


def get_tool_registry() -> ToolRegistry:
    return _registry
