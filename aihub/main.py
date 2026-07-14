#!/usr/bin/env python3
"""ACTIVE_CONFIRMED: canonical FastAPI application ``app`` for this repo.

- Entry: ``uvicorn aihub.main:app`` (see root ``start.sh``).
- **HTTP truth manifest:** ``aihub/canonical_http_surface.py`` — must match live routes
  (``tests/test_canonical_http_surface.py``).
- **Mounted routers:** ``app.include_router`` below from ``aihub/*_api.py`` plus
  ``aihub.api.security_router`` and ``aihub.api.self_heal_status_router`` only.
  Other modules under ``aihub/api/*.py`` are **not** mounted here — see ``aihub/api/_LEGACY.md``.
- **Inline** ``@app.get`` / ``@app.post`` on this module are part of the same active surface
  (e.g. ``/system/ping``, ``/psyche/{user_id}``, ``/web/fetch``).
"""

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from aihub.config import APP_NAME, HOST, PORT, gpt_openapi_spec_path
from aihub.db import append_event, init_db
from aihub.logs import setup_logging

# Configure logging before anything else
setup_logging()
logger = logging.getLogger(__name__)

# Import after logging is configured
from aihub.admin_api import router as admin_router
from aihub.agent_api import router as agent_router
from aihub.auth_api import router as auth_router
from aihub.auth_middleware import auth_middleware
from aihub.agent_http_surface import (
    stamp_cognitive_debug_decide,
    stamp_cognitive_observability_health,
)
from aihub.agent_worker import start_worker_once
from aihub.api.security_router import router as security_router
from aihub.api.self_heal_status_router import router as self_heal_status_router
from aihub.auth_patch import (
    collect_hub_auth_secrets,
    hub_proxy_token_expected,
    safe_check_api_key,
)
from aihub.chat_api import router as chat_router
from aihub.chat_sessions_api import router as chat_sessions_router
from aihub.cockpit_api import router as cockpit_router
from aihub.conflict_detector import ConflictDetector
from aihub.core.security import NO_AUTH_PATHS, starts_with_any
from aihub.fs_tools import list_dir, read_file, write_file
from aihub.knowledge_graph import KnowledgeGraph, load_from_db
from aihub.memory_core import get_memory_core
from aihub.memory_errors import MemoryUserIdRequiredError, MemoryVectorWriteError
from aihub.memory_engine import health
from aihub.memory_v2_api import router as memory_v2_router
from aihub.metrics_engine import (
    get_alert_status,
    get_system_health,
    record_error,
    record_latency,
)
from aihub.models import (
    FSListIn,
    FSReadIn,
    FSWriteIn,
    MemoryAddIn,
    MemoryItem,
    MemorySearchIn,
    MemorySearchOut,
    PsycheGetOut,
    PsycheReflectIn,
    PsycheUpdateIn,
    SnapshotCreateIn,
    SnapshotRestoreIn,
    TurnIn,
    TurnOut,
    WebFetchIn,
    WebIngestIn,
    WebResearchIn,
)
from aihub.psyche_core import get_psyche_core
from aihub.psyche_v2_api import router as psyche_v2_router
from aihub.sse_engine import event_stream
from aihub.system_ops import create_snapshot, list_snapshots, restore_snapshot
from aihub.web_tools import fetch_url, ingest_url as ingest_web_url, web_health

COGNITIVE_DEBUG_ENDPOINT_ENABLED = (
    os.environ.get("AIHUB_ENABLE_COGNITIVE_DEBUG_ENDPOINT", "0") == "1"
)


def _legacy_stm_turn_disabled() -> bool:
    v = os.environ.get("AIHUB_DISABLE_LEGACY_STM_TURN", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _legacy_memory_v1_http_disabled() -> bool:
    v = os.environ.get("AIHUB_DISABLE_LEGACY_MEMORY_V1_HTTP", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _guard_legacy_memory_v1_http() -> None:
    if _legacy_memory_v1_http_disabled():
        raise HTTPException(
            status_code=410,
            detail={
                "message": "POST /memory/add and POST /memory/search (legacy v1 HTTP) are disabled.",
                "env": "AIHUB_DISABLE_LEGACY_MEMORY_V1_HTTP",
                "canonical_memory_surface": "memory-v2",
                "related_paths": {
                    "structured_write": "/memory/v2/item",
                    "structured_search": "/memory/v2/search",
                },
                "note": "Use MemoryCanonicalCore via POST /memory/v2/item or unified retrieval.",
            },
        )


def _stamp_legacy_memory_v1_http_headers(
    response: Response, *, link_target: str
) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Wed, 31 Dec 2026 23:59:59 GMT"
    response.headers["X-AIHub-Endpoint-Role"] = "legacy-memory-v1-http"
    response.headers["X-AIHub-Legacy-Memory-V1"] = "true"
    response.headers["X-AIHub-Canonical-Memory-Surface"] = "memory-v2"
    response.headers["Link"] = f'<{link_target}>; rel="related"'


# Create FastAPI app with lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app startup and shutdown."""
    # Startup
    try:
        logger.info("Starting %s...", APP_NAME)
        init_db()
        from aihub.local_auth import ensure_auth_schema

        ensure_auth_schema()
        logger.info("Database initialized")
        load_from_db()
        logger.info("Knowledge graph loaded from DB")
        start_worker_once()
        logger.info("Agent worker started")
        try:
            from aihub.core.background import start_background

            start_background()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Background workers: %s", e, exc_info=True)
        # Wire memory + psyche into chat runtime
        _wire_memory_psyche_into_runtime()
        # Log actual bind address (re-read env to catch start.sh port override)
        _host = os.environ.get("HOST", HOST)
        _port = os.environ.get("PORT", str(PORT))
        logger.info("%s is running on %s:%s", APP_NAME, _host, _port)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Startup error: %s", e, exc_info=True)
        raise

    yield

    # Shutdown
    try:
        logger.info("Shutting down %s...", APP_NAME)
        try:
            from aihub.agent_worker import stop_worker

            stop_worker()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Agent worker shutdown: %s", exc)
        try:
            from aihub.core.background import stop_background

            stop_background()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Background worker shutdown: %s", exc)
        logger.info("Shutdown complete")
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Shutdown error: %s", e, exc_info=True)


app = FastAPI(title=APP_NAME, version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def _auth_middleware(request, call_next):
    """Session, signed principal, and ownership enforcement."""
    return await auth_middleware(request, call_next)


app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(agent_router)
app.include_router(chat_router)
app.include_router(chat_sessions_router)
app.include_router(cockpit_router)
app.include_router(memory_v2_router)
app.include_router(psyche_v2_router)
app.include_router(security_router)
app.include_router(self_heal_status_router)


def _wire_memory_psyche_into_runtime():
    """Wire :meth:`MemoryCanonicalCore.ingest_turn` into chat_runtime so /chat/turn learns."""
    try:
        from aihub.chat_runtime import get_chat_runtime

        # Get runtime instance and verify it exists
        runtime_instance = get_chat_runtime()
        if runtime_instance:
            runtime_instance._memory_process_fn = get_memory_core().ingest_turn
            runtime_instance._psyche_evolve_fn = get_psyche_core().evolve
            logger.debug("Memory processing wired into chat runtime")
        else:
            logger.warning("Could not get runtime instance")
    except Exception as e:
        logger.warning("Could not wire memory: %s", e)


# Initialize cognitive system (shared managed instance)
def _resolve_cognitive_controller():
    from .cognitive_controller import get_cognitive_controller

    return get_cognitive_controller()


cognitive_controller = _resolve_cognitive_controller()
knowledge_graph = KnowledgeGraph()
conflict_detector = ConflictDetector()

logger.info("Cognitive system modules initialized")


# ---------------------------
# System endpoints
# ---------------------------
@app.get("/system/ping")
def ping() -> dict[str, Any]:
    """Health check endpoint."""
    return {"ok": True, "ts": time.time(), "app": APP_NAME}


@app.get("/ops/health")
def ops_health() -> dict[str, Any]:
    """Operator/LB: warstwowy status (app, DB, memory, embeddings, vector, streaming, STT/vision)."""
    from aihub.ops_platform import get_platform_health

    return get_platform_health()


@app.get("/ops/ready")
def ops_ready(response: Response) -> dict[str, Any]:
    """Hard readiness probe: HTTP 503 when a mandatory runtime layer is broken."""
    from aihub.ops_platform import get_platform_health, readiness_from_health

    health_report = get_platform_health()
    ready = readiness_from_health(health_report)
    if not ready.get("ready"):
        response.status_code = 503
    return ready


@app.get("/ops/capabilities")
def ops_capabilities() -> dict[str, Any]:
    """Compact capability matrix for frontend/operator dashboards; no secrets exposed."""
    from aihub.ops_platform import capability_matrix

    return capability_matrix()


@app.get("/system/health/{user_id}")
def system_health(user_id: str) -> dict[str, Any]:
    """Get system health for user."""
    get_psyche_core().ensure_user(user_id)
    result = health(user_id)
    logger.debug("Health check for user %s: %s", user_id, result)
    return result


# ---------------------------
# STM turns (raw)
# ---------------------------
@app.post("/turn", response_model=TurnOut)
def add_turn(turn: TurnIn, response: Response) -> TurnOut:
    """Legacy raw STM write only (add_stm). Not LLM chat — use POST /chat/turn for that."""
    if _legacy_stm_turn_disabled():
        raise HTTPException(
            status_code=410,
            detail={
                "message": "POST /turn (legacy STM append) is disabled by AIHUB_DISABLE_LEGACY_STM_TURN.",
                "canonical_chat_path": "/chat/turn",
                "note": "/chat/turn runs the full chat runtime; /turn only appended raw STM rows.",
            },
        )
    try:
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "Wed, 31 Dec 2026 23:59:59 GMT"
        response.headers["Link"] = '</chat/turn>; rel="successor-version"'
        response.headers["X-AIHub-Endpoint-Role"] = "legacy-stm-write"
        response.headers["X-AIHub-Legacy-Stm-Write"] = "true"
        response.headers["X-AIHub-Canonical-Chat-Path"] = "/chat/turn"
        get_psyche_core().ensure_user(turn.user_id)
        msg_id = get_memory_core().ingest_stm_message(
            turn.user_id, turn.role, turn.content, turn.meta or {}
        )
        append_event(turn.user_id, "turn.add", {"id": msg_id, "role": turn.role})
        logger.debug("Added turn %s for user %s: %s", msg_id, turn.user_id, turn.role)
        return TurnOut(id=msg_id, ts=time.time())
    except Exception as e:
        logger.error("Error in add_turn: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---------------------------
# Psyche
# ---------------------------
@app.get("/psyche/{user_id}", response_model=PsycheGetOut)
def psyche_get(user_id: str) -> PsycheGetOut:
    """Get psyche state for user."""
    try:
        st = get_psyche_core().ensure_user(user_id)
        logger.debug("Retrieved psyche for %s", user_id)
        return PsycheGetOut(**st)
    except Exception as e:
        logger.error("Error in psyche_get: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/psyche/update", response_model=PsycheGetOut)
def psyche_update(inp: PsycheUpdateIn) -> PsycheGetOut:
    """Update psyche state."""
    try:
        st = get_psyche_core().evolve(inp.user_id, inp.text, inp.role)
        logger.debug("Updated psyche for %s: %s", inp.user_id, inp.role)
        return PsycheGetOut(**st)
    except Exception as e:
        logger.error("Error in psyche_update: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/psyche/reflect")
def psyche_reflect(inp: PsycheReflectIn) -> dict[str, Any]:
    """Reflect on recent conversation."""
    try:
        get_psyche_core().ensure_user(inp.user_id)
        ctx = get_memory_core().retrieve_unified(
            inp.user_id, inp.query or "", limit=min(inp.limit, 20)
        )
        out = get_psyche_core().reflect(inp.user_id, ctx.get("stm") or [])
        logger.debug(
            "Reflection for %s: %d insights", inp.user_id, len(out.get("insights", []))
        )
        return out
    except Exception as e:
        logger.error("Error in psyche_reflect: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/psyche/runtime/{user_id}")
def psyche_runtime(user_id: str) -> dict[str, Any]:
    """Combined v1/v2 psyche runtime context used by chat, agent and cockpit."""
    try:
        from aihub.runtime_psyche_bridge import (
            build_psyche_v2_behavior_context,
            build_psyche_v2_runtime_snapshot,
            summarize_psyche_v2_for_agent,
            summarize_psyche_v2_for_chat,
        )

        v1 = get_psyche_core().ensure_user(user_id)
        behavior = build_psyche_v2_behavior_context(user_id)
        return {
            "ok": True,
            "user_id": user_id,
            "psyche_v1": v1,
            "psyche_v2_snapshot": build_psyche_v2_runtime_snapshot(user_id),
            "behavior_context": behavior.__dict__,
            "chat_summary": summarize_psyche_v2_for_chat(user_id),
            "agent_summary": summarize_psyche_v2_for_agent(user_id),
        }
    except Exception as e:
        logger.error("Error in psyche_runtime: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


# ---------------------------
# Memory
# ---------------------------
@app.post("/memory/add")
def memory_add(inp: MemoryAddIn, response: Response) -> dict[str, Any]:
    """Compatibility HTTP write; body routed through :class:`aihub.memory_core.MemoryCanonicalCore`."""
    _guard_legacy_memory_v1_http()
    try:
        _stamp_legacy_memory_v1_http_headers(response, link_target="/memory/v2/item")
        get_psyche_core().ensure_user(inp.user_id)
        res = get_memory_core().ingest_turn(
            inp.user_id,
            inp.user_msg,
            inp.assistant_msg,
            inp.intent,
            inp.meta or {},
        )
        logger.debug("Added memory turn for %s", inp.user_id)
        return {"ok": True, **res}
    except MemoryVectorWriteError as e:
        logger.error("memory_add vector failure: %s", e, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={"message": str(e), "layer": "L2_vector"},
        ) from e
    except MemoryUserIdRequiredError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error("Error in memory_add: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/memory/search", response_model=MemorySearchOut)
def memory_search(inp: MemorySearchIn, response: Response) -> MemorySearchOut:
    """Compatibility HTTP search; uses unified retrieval from :func:`aihub.memory_core.get_memory_core`."""
    _guard_legacy_memory_v1_http()
    try:
        _stamp_legacy_memory_v1_http_headers(response, link_target="/memory/v2/search")
        get_psyche_core().ensure_user(inp.user_id)
        ctx = get_memory_core().retrieve_unified(
            inp.user_id, inp.query, limit=inp.limit
        )
        st = get_psyche_core().ensure_user(inp.user_id)
        logger.debug("Memory search for %s: %s", inp.user_id, inp.query)
        return MemorySearchOut(
            user_id=inp.user_id,
            query=inp.query,
            stm=ctx["stm"],
            episodic=[
                MemoryItem(
                    id=x["id"],
                    layer=x["layer"],
                    content=x["content"],
                    tags=x["tags"],
                    score=x["score"],
                    ts=x["ts"],
                    meta=x["meta"],
                )
                for x in ctx["episodic"]
            ],
            semantic=[
                MemoryItem(
                    id=x["id"],
                    layer=x["layer"],
                    content=x["content"],
                    tags=x["tags"],
                    score=x["score"],
                    ts=x["ts"],
                    meta=x["meta"],
                )
                for x in ctx["semantic"]
            ],
            psyche=st,
            total=int(ctx["total"]),
            dense_hits=list(ctx.get("dense_hits") or []),
            graph_hits=list(ctx.get("graph_hits") or []),
            memory_v2_items=list(ctx.get("memory_v2_items") or []),
            memory_v2_total=int(ctx.get("memory_v2_total") or 0),
            memory_v2_contradictions=list(ctx.get("memory_v2_contradictions") or []),
            memory_v2_related_procedures=list(
                ctx.get("memory_v2_related_procedures") or []
            ),
        )
    except MemoryUserIdRequiredError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error("Error in memory_search: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/memory/health")
def memory_health(user_id: str = Query(min_length=1, max_length=128)) -> dict[str, Any]:
    """Operational health and inventory of the active memory stack for one user."""
    try:
        get_psyche_core().ensure_user(user_id)
        return get_memory_core().build_health_report(user_id)
    except Exception as e:
        logger.error("Error in memory_health: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


# ---------------------------
# SSE
# ---------------------------
@app.get("/sse/{user_id}")
def sse(user_id: str, last_id: int = 0):
    """SSE stream for real-time events."""
    try:
        get_psyche_core().ensure_user(user_id)
        logger.debug("SSE stream started for %s", user_id)
        return StreamingResponse(
            event_stream(user_id, last_id=last_id), media_type="text/event-stream"
        )
    except Exception as e:
        logger.error("Error in sse: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---------------------------
# FS tools (admin-only; see auth_middleware._ADMIN_PREFIXES)
# ---------------------------
def _principal_user_id(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    uid = str(getattr(principal, "user_id", "") or "").strip()
    if not uid:
        raise HTTPException(status_code=401, detail="authentication required")
    return uid


@app.post("/fs/write")
def fs_write(request: Request, inp: FSWriteIn) -> dict[str, Any]:
    """Write file (admin)."""
    user_id = _principal_user_id(request)
    try:
        get_psyche_core().ensure_user(user_id)
        result = write_file(user_id, inp.path, inp.content, overwrite=inp.overwrite)
        logger.info("File written for %s: %s", user_id, inp.path)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in fs_write: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/fs/read")
def fs_read(request: Request, inp: FSReadIn) -> dict[str, Any]:
    """Read file (admin)."""
    user_id = _principal_user_id(request)
    try:
        get_psyche_core().ensure_user(user_id)
        result = read_file(user_id, inp.path, max_bytes=inp.max_bytes)
        logger.debug("File read for %s: %s", user_id, inp.path)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in fs_read: %s", e, exc_info=True)
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/fs/list")
def fs_list(request: Request, inp: FSListIn) -> dict[str, Any]:
    """List directory (admin)."""
    user_id = _principal_user_id(request)
    try:
        get_psyche_core().ensure_user(user_id)
        result = list_dir(
            user_id, inp.path, recursive=inp.recursive, max_items=inp.max_items
        )
        logger.debug("Directory listed for %s: %s", user_id, inp.path)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in fs_list: %s", e, exc_info=True)
        raise HTTPException(status_code=404, detail=str(e)) from e


# ---------------------------
# Web tools
# ---------------------------
@app.get("/web/health")
def web_health_endpoint() -> dict[str, Any]:
    """Web subsystem capability status, without secrets."""
    try:
        return web_health()
    except Exception as e:
        logger.error("Error in web_health: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/web/fetch")
async def web_fetch(inp: WebFetchIn, user_id: str = "default") -> dict[str, Any]:
    """Fetch URL through the canonical SSRF-guarded web stack."""
    try:
        get_psyche_core().ensure_user(user_id)
        try:
            get_psyche_core().v2_service.apply_event(
                user_id=user_id,
                event_type="web_research_triggered",
                reason_text=f"web.fetch_url {inp.url}",
                source_ref=inp.url,
                signal_strength=0.45,
                metadata={"operation": "web.fetch"},
            )
        except Exception:
            logger.debug("psyche v2 web.fetch event skipped", exc_info=True)
        result = await fetch_url(user_id, inp.url)
        try:
            get_psyche_core().v2_service.apply_event(
                user_id=user_id,
                event_type="tool_success",
                reason_text="web.fetch_url succeeded",
                source_ref=str(result.get("url") or inp.url),
                signal_strength=0.45,
                metadata={"operation": "web.fetch", "status": result.get("status")},
            )
        except Exception:
            logger.debug("psyche v2 web.fetch success event skipped", exc_info=True)
        logger.debug("URL fetched for %s: %s", user_id, inp.url)
        return result
    except Exception as e:
        try:
            get_psyche_core().v2_service.apply_event(
                user_id=user_id,
                event_type="tool_failure",
                reason_text=f"web.fetch_url failed: {str(e)[:240]}",
                source_ref=inp.url,
                signal_strength=0.55,
                metadata={"operation": "web.fetch"},
            )
        except Exception:
            logger.debug("psyche v2 web.fetch failure event skipped", exc_info=True)
        logger.error("Error in web_fetch: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/web/research")
async def web_research(inp: WebResearchIn, user_id: str = "default") -> dict[str, Any]:
    """Run configured web research and store extracted facts through memory."""
    try:
        get_psyche_core().ensure_user(user_id)
        try:
            get_psyche_core().v2_service.apply_event(
                user_id=user_id,
                event_type="web_research_triggered",
                reason_text=f"web.research {inp.query}",
                source_ref=inp.query,
                signal_strength=0.55,
                metadata={"operation": "web.research", "research_type": inp.research_type},
            )
        except Exception:
            logger.debug("psyche v2 web.research event skipped", exc_info=True)
        from aihub.research_engine import research

        result = await research(user_id, inp.query, research_type=inp.research_type)
        if result.get("ok"):
            try:
                get_psyche_core().v2_service.apply_event(
                    user_id=user_id,
                    event_type="tool_success",
                    reason_text="web.research succeeded",
                    source_ref=inp.query,
                    signal_strength=0.5,
                    metadata={
                        "operation": "web.research",
                        "results": result.get("total_results", 0),
                        "facts": result.get("total_facts", 0),
                    },
                )
            except Exception:
                logger.debug("psyche v2 web.research success event skipped", exc_info=True)
        return result
    except Exception as e:
        try:
            get_psyche_core().v2_service.apply_event(
                user_id=user_id,
                event_type="tool_failure",
                reason_text=f"web.research failed: {str(e)[:240]}",
                source_ref=inp.query,
                signal_strength=0.55,
                metadata={"operation": "web.research"},
            )
        except Exception:
            logger.debug("psyche v2 web.research failure event skipped", exc_info=True)
        logger.error("Error in web_research: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/web/ingest")
async def web_ingest(inp: WebIngestIn, user_id: str = "default") -> dict[str, Any]:
    """Fetch URL and store extracted page text into canonical memory."""
    try:
        get_psyche_core().ensure_user(user_id)
        result = await ingest_web_url(
            user_id,
            inp.url,
            importance=inp.importance,
            confidence=inp.confidence,
            session_id=inp.session_id,
        )
        try:
            get_psyche_core().v2_service.apply_event(
                user_id=user_id,
                event_type="tool_success",
                reason_text="web.ingest succeeded",
                source_ref=str(result.get("fetch", {}).get("url") or inp.url),
                signal_strength=0.6,
                metadata={"operation": "web.ingest", "memory_ids": result.get("memory_ids", {})},
            )
        except Exception:
            logger.debug("psyche v2 web.ingest success event skipped", exc_info=True)
        return result
    except Exception as e:
        try:
            get_psyche_core().v2_service.apply_event(
                user_id=user_id,
                event_type="tool_failure",
                reason_text=f"web.ingest failed: {str(e)[:240]}",
                source_ref=inp.url,
                signal_strength=0.6,
                metadata={"operation": "web.ingest"},
            )
        except Exception:
            logger.debug("psyche v2 web.ingest failure event skipped", exc_info=True)
        logger.error("Error in web_ingest: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---------------------------
# Snapshots (admin-only)
# ---------------------------
@app.post("/system/snapshot/create")
def snapshot_create(request: Request, inp: SnapshotCreateIn) -> dict[str, Any]:
    """Create snapshot (admin)."""
    user_id = _principal_user_id(request)
    try:
        get_psyche_core().ensure_user(user_id)
        result = create_snapshot(user_id, inp.reason)
        logger.info("Snapshot created for %s: %s", user_id, inp.reason)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in snapshot_create: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/system/snapshot/list")
def snapshot_list(request: Request) -> dict[str, Any]:
    """List snapshots (admin)."""
    _principal_user_id(request)
    try:
        snapshots = list_snapshots()
        logger.debug("Listed %d snapshots", len(snapshots))
        return {"snapshots": snapshots}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in snapshot_list: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/system/snapshot/restore")
def snapshot_restore(request: Request, inp: SnapshotRestoreIn) -> dict[str, Any]:
    """Restore from snapshot (admin)."""
    user_id = _principal_user_id(request)
    try:
        get_psyche_core().ensure_user(user_id)
        result = restore_snapshot(user_id, inp.snapshot_id)
        logger.info("Snapshot restored for %s: %s", user_id, inp.snapshot_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in snapshot_restore: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---------------------------
# Cognitive System
# ---------------------------
# DEBUG_ONLY: ``POST /cognitive/decide`` — disabled unless ``AIHUB_ENABLE_COGNITIVE_DEBUG_ENDPOINT=1``.
# Observability: ``GET /cognitive/health`` is always available (see ``stamp_cognitive_observability_health``).
@app.post("/cognitive/decide", include_in_schema=COGNITIVE_DEBUG_ENDPOINT_ENABLED)
async def cognitive_decide(
    inp: dict[str, Any],
    response: Response,
    user_id: str = "default",
) -> dict[str, Any]:
    """Debug/internal cognitive-only endpoint (non-canonical).

    Canonical production orchestration flows through `/agent/*` via ExecutiveController.
    This endpoint intentionally bypasses execution/planning and is disabled by default.
    """
    try:
        if not COGNITIVE_DEBUG_ENDPOINT_ENABLED:
            raise HTTPException(
                status_code=404,
                detail=(
                    "cognitive debug endpoint disabled; set "
                    "AIHUB_ENABLE_COGNITIVE_DEBUG_ENDPOINT=1 to enable"
                ),
            )

        get_psyche_core().ensure_user(user_id)

        message = inp.get("message", "")
        context = inp.get("context", {})

        from .cognitive_controller import DecisionRequest

        decision_request = DecisionRequest(
            user_id=user_id,
            message=message,
            context=context,
            available_tools=["web_search", "memory_store", "file_write"],
            constraints={"max_time_seconds": 30},
        )

        start_time = time.time()
        decision = await cognitive_controller.decide(decision_request)
        duration_ms = (time.time() - start_time) * 1000

        record_latency("cognitive_decision", duration_ms)

        append_event(
            user_id,
            "cognitive.api_call",
            {
                "message": message[:100],
                "action": decision.action_type,
                "confidence": decision.confidence,
                "duration_ms": duration_ms,
            },
        )

        stamp_cognitive_debug_decide(response)
        return {
            "debug_endpoint": True,
            "debug_only": True,
            "bypass": True,
            "canonical_runtime": False,
            "action_type": decision.action_type,
            "parameters": decision.parameters,
            "confidence": decision.confidence,
            "reasoning": decision.reasoning,
            "duration_ms": duration_ms,
        }
    except HTTPException:
        # Preserve FastAPI-native semantics (e.g. intentional 404 when debug endpoint is disabled).
        raise
    except Exception as e:
        logger.error("Error in cognitive_decide: %s", e, exc_info=True)
        record_error("cognitive_decide_error", user_id)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/cognitive/health")
def cognitive_health(response: Response) -> dict[str, Any]:
    """Observability: cognitive/memory subsystem health (not an execution endpoint)."""
    try:
        from aihub.db import cognitive_memory_schema_status, fetch_one

        sys_health = get_system_health()
        alerts = get_alert_status()

        schema_ok, db_schema_alerts = cognitive_memory_schema_status()

        # GC stats — count active vs archived vs deleted nodes
        gc_row = fetch_one(
            """
            SELECT
                SUM(CASE WHEN deleted=0 AND layer NOT IN ('L3','L3_archive') THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN layer IN ('L3','L3_archive') THEN 1 ELSE 0 END) AS archived,
                SUM(CASE WHEN deleted=1 THEN 1 ELSE 0 END) AS deleted
            FROM memory_nodes
        """
        )
        if gc_row:
            gc_stats = {
                "active": gc_row["active"] or 0,
                "archived": gc_row["archived"] or 0,
                "deleted": gc_row["deleted"] or 0,
            }
        else:
            gc_stats = {"active": 0, "archived": 0, "deleted": 0}

        # Metrics counts (already in alerts above)

        stamp_cognitive_observability_health(response)
        return {
            "status": (
                "ok"
                if alerts["alert_count"] == 0 and not db_schema_alerts
                else "warning"
            ),
            "health": {
                "latency_ms": sys_health.latency_ms,
                "error_rate": sys_health.error_rate,
                "requests_per_second": sys_health.requests_per_second,
            },
            "alerts": alerts.get("alerts", []),
            "db_schema": {
                "ok": len(db_schema_alerts) == 0,
                "alerts": db_schema_alerts,
                "tables": schema_ok,
            },
            "gc_stats": gc_stats,
            "graph_stats": knowledge_graph.stats(),
        }
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error in cognitive_health: %s", e, exc_info=True)
        stamp_cognitive_observability_health(response)
        return {
            "status": "error",
            "error": str(e),
        }


# ---------------------------
# OpenAPI
# ---------------------------
@app.get("/gpt-openapi.json")
def gpt_openapi():
    """Serve GPT-oriented OpenAPI JSON.

    Prefers the static override at :func:`aihub.config.gpt_openapi_spec_path` when it exists AND
    has a non-empty ``paths`` object (a deliberately curated document). Otherwise falls back to
    the live schema generated from this running app (``app.openapi()``) — the same data already
    served at ``GET /openapi.json`` (also in :data:`aihub.core.security.NO_AUTH_PATHS`, so this
    adds no new information disclosure).

    06.07 repair sprint (P1): previously this endpoint could serve a static stub with
    ``"paths": {}``, which is a dead-weight response for any client actually depending on it.
    This endpoint must never do that again — see ``tests/test_config_truth.py``.
    """
    try:
        openapi_path = gpt_openapi_spec_path()
        if openapi_path.is_file():
            try:
                data = json.loads(openapi_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning(
                    "GPT OpenAPI override at %s unreadable, falling back to live schema: %s",
                    openapi_path,
                    exc,
                )
                data = None
            if isinstance(data, dict) and data.get("paths"):
                logger.debug("Served static GPT OpenAPI override (%d paths)", len(data["paths"]))
                return JSONResponse(data)
        logger.debug("No usable static GPT OpenAPI override; serving live app schema")
        return JSONResponse(app.openapi())
    except Exception as e:
        logger.error("Error serving OpenAPI: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


# ---------------------------
# Run locally
# ---------------------------
if __name__ == "__main__":
    logger.info("Starting %s server...", APP_NAME)
    uvicorn.run(
        "aihub.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
        access_log=True,
    )
