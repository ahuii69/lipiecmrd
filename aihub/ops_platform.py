#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Operator health/capability status for backend and cockpit.

This endpoint is intentionally honest: disabled features are ``inactive``, optional
fallbacks are ``degraded`` with backend details, and missing mandatory runtime
parts are ``error``. It never returns raw secret values.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import socket
import time
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _secret_status(name: str) -> str:
    return "set" if (os.getenv(name) or "").strip() else "empty"


def _tcp_probe(url: str, timeout: float = 2.5) -> dict[str, Any]:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return {"ok": False, "error": "invalid_url"}
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "host": host, "port": port, "scheme": parsed.scheme}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "host": host, "port": port, "scheme": parsed.scheme, "error": str(exc)[:300]}


def _rollup_status(layers: dict[str, Any]) -> str:
    statuses = [str(v.get("status") or "") for v in layers.values() if isinstance(v, dict)]
    if "error" in statuses:
        return "error"
    if "degraded" in statuses or "unknown" in statuses:
        return "degraded"
    return "ok"


def get_platform_health() -> dict[str, Any]:
    """Return layered platform health used by /ops/health and cockpit dock."""
    ts = time.time()
    layers: dict[str, Any] = {}

    def put(name: str, status: str, **extra: Any) -> None:
        layers[name] = {"status": status, **extra}

    try:
        from aihub.config import APP_NAME

        put("app", "ok", app_name=APP_NAME, env=os.getenv("ENV", "development"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("ops_health: app config failed")
        put("app", "error", detail=str(exc)[:300])

    try:
        from aihub.db import _db_backend, fetch_one

        fetch_one("SELECT 1")
        backend = _db_backend()
        put(
            "database",
            "ok",
            backend=backend,
            postgres_dsn=_secret_status("POSTGRES_DSN") if backend == "postgres" else "not_used",
            psycopg2_available=_has_module("psycopg2") if backend == "postgres" else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("ops_health: database check failed: %s", exc, exc_info=True)
        put("database", "error", detail=str(exc)[:500], backend=os.getenv("DB_BACKEND", "sqlite"))

    try:
        from aihub.memory_engine import health as mem_health

        h = mem_health("_ops_probe")
        put(
            "memory_v1",
            "ok",
            stm_messages=h.get("stm_messages"),
            episodic_nodes=h.get("episodic_nodes"),
            semantic_nodes=h.get("semantic_nodes"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("ops_health: memory health failed: %s", exc, exc_info=True)
        put("memory_v1", "error", detail=str(exc)[:500])

    try:
        from typing import get_args

        from aihub.memory_v2_repository import count_memory_items
        from aihub.memory_psyche_contracts import MemoryType

        counts = {str(mt): count_memory_items("_ops_probe", str(mt)) for mt in get_args(MemoryType)}
        counts["total"] = count_memory_items("_ops_probe")
        put("memory_v2", "ok", counts=counts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ops_health: Memory V2 summary failed: %s", exc, exc_info=True)
        put("memory_v2", "degraded", detail=str(exc)[:500])

    try:
        from aihub.memory_v2_index_jobs import index_job_summary

        summary = index_job_summary()
        counts = summary.get("counts", {})
        failed = int(counts.get("failed", 0) or 0)
        pending = int(counts.get("pending", 0) or 0) + int(counts.get("stale", 0) or 0)
        status = "error" if failed else ("degraded" if pending else "ok")
        put("memory_v2_index_jobs", status, **summary)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ops_health: Memory V2 index job summary failed: %s", exc, exc_info=True)
        put("memory_v2_index_jobs", "degraded", detail=str(exc)[:500])

    try:
        from aihub import embedding_engine as ee

        eh = ee.healthcheck()
        probe_error = eh.get("embedding_healthcheck_probe_error")
        fallback_used = bool(eh.get("embedding_runtime_probe_fallback_used"))
        status = "error" if probe_error else ("degraded" if fallback_used else "ok")
        put(
            "embeddings",
            status,
            provider=eh.get("provider"),
            model=eh.get("model"),
            runtime_provider=eh.get("embedding_runtime_probe_provider"),
            output_dimension=eh.get("output_dimension"),
            voyage_api_key_present=bool(eh.get("voyage_api_key_present")),
            fallback_used=fallback_used,
            detail=str(probe_error)[:500] if probe_error else "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("ops_health: embeddings probe failed: %s", exc, exc_info=True)
        put("embeddings", "error", detail=str(exc)[:500])

    try:
        from aihub import vector_engine as ve

        vh = ve.health()
        backend = vh.get("backend") or vh.get("vector_backend") or "unknown"
        status = "ok" if backend == "faiss" else "degraded"
        put("vector", status, **vh)
    except Exception as exc:  # noqa: BLE001
        logger.error("ops_health: vector health failed: %s", exc, exc_info=True)
        put("vector", "error", detail=str(exc)[:500])

    try:
        from aihub.config import (
            LLM_API_KEY,
            LLM_BASE_URL,
            LLM_MODEL_NAME,
            LLM_PROVIDER_NAME,
            LLM_STREAMING_ENABLED,
        )

        configured = bool((LLM_API_KEY or "").strip() and (LLM_BASE_URL or "").strip())
        live_probe = _env_bool("AIHUB_HEALTH_LIVE_PROVIDER_PROBE", "0")
        layer: dict[str, Any] = {
            "configured": configured,
            "provider": LLM_PROVIDER_NAME,
            "model": LLM_MODEL_NAME,
            "base_url": LLM_BASE_URL,
            "api_key": "set" if (LLM_API_KEY or "").strip() else "empty",
            "llm_streaming_enabled": bool(LLM_STREAMING_ENABLED),
            "live_probe_enabled": live_probe,
        }
        if live_probe and LLM_BASE_URL:
            layer["tcp_probe"] = _tcp_probe(LLM_BASE_URL)
            status = "ok" if configured and layer["tcp_probe"].get("ok") else "error"
        else:
            status = "ok" if configured else "error"
        put("llm", status, **layer)
    except Exception as exc:  # noqa: BLE001
        logger.exception("ops_health: LLM config read failed")
        put("llm", "error", detail=str(exc)[:300])

    try:
        from aihub.config import (
            CHAT_VISION_API_KEY,
            CHAT_VISION_API_URL,
            CHAT_VISION_BACKEND,
            CHAT_VISION_ENABLED,
            CHAT_VISION_MODEL,
            CHAT_VISION_OLLAMA_URL,
            LLM_API_KEY,
            LLM_BASE_URL,
        )

        if not CHAT_VISION_ENABLED:
            put("vision", "inactive", enabled=False, backend=CHAT_VISION_BACKEND or "")
        else:
            # Capability truth: "vision" is reported as usable ONLY when the configured pipeline is
            # actually reachable/configured — never on the strength of the enable flag alone. This
            # prevents /ops/capabilities from lying (vision:true while the provider is dead).
            backend = (CHAT_VISION_BACKEND or "ollama").strip().lower()
            model_set = bool((CHAT_VISION_MODEL or "").strip())
            if backend in ("ollama", "local_ollama"):
                probe = _tcp_probe(CHAT_VISION_OLLAMA_URL)
                reachable = bool(probe.get("ok")) and model_set
                reason = (
                    None
                    if reachable
                    else ("vision_backend_unreachable" if not probe.get("ok") else "vision_model_missing")
                )
                put(
                    "vision",
                    "ok" if reachable else "degraded",
                    enabled=True,
                    backend=backend,
                    endpoint=CHAT_VISION_OLLAMA_URL,
                    tcp_probe=probe,
                    reason=reason,
                )
            elif backend in ("openai_compatible", "openai", "remote"):
                # Runtime (chat_attachment_vision._describe_openai_compatible) falls back to the
                # shared LLM endpoint/key when the vision-specific ones are blank. Mirror that
                # resolution here so capability truth matches what the pipeline can actually reach.
                resolved_url = (CHAT_VISION_API_URL or LLM_BASE_URL or "").strip()
                resolved_key = (CHAT_VISION_API_KEY or LLM_API_KEY or "").strip()
                configured = bool(resolved_url) and bool(resolved_key) and model_set
                put(
                    "vision",
                    "ok" if configured else "degraded",
                    enabled=True,
                    backend=backend,
                    endpoint=resolved_url,
                    reason=None if configured else "vision_remote_not_configured",
                )
            else:
                put("vision", "degraded", enabled=True, backend=backend, reason=f"vision_bad_backend:{backend}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("ops_health: vision config read failed")
        put("vision", "error", detail=str(exc)[:200])

    try:
        from aihub.config import CHAT_STT_API_URL, CHAT_STT_BACKEND, CHAT_STT_ENABLED

        if CHAT_STT_ENABLED:
            probe = _tcp_probe(CHAT_STT_API_URL) if _env_bool("AIHUB_HEALTH_LIVE_PROVIDER_PROBE", "0") else None
            status = "ok" if probe is None or probe.get("ok") else "degraded"
            put("stt", status, enabled=True, backend=CHAT_STT_BACKEND or "", endpoint=CHAT_STT_API_URL, tcp_probe=probe)
        else:
            put("stt", "inactive", enabled=False, backend=CHAT_STT_BACKEND or "")
    except Exception as exc:  # noqa: BLE001
        logger.exception("ops_health: stt config read failed")
        put("stt", "error", detail=str(exc)[:200])


    try:
        from aihub.psyche_core import get_psyche_core
        from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context

        core = get_psyche_core()
        core.ensure_user("_ops_probe")
        behavior = build_psyche_v2_behavior_context("_ops_probe")
        put(
            "psyche",
            "ok" if behavior.loaded else "degraded",
            v1=True,
            v2_loaded=bool(behavior.loaded),
            mode=getattr(behavior, "mode", "neutral"),
            trust=getattr(behavior, "trust", 0.5),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("ops_health: psyche check failed: %s", exc, exc_info=True)
        put("psyche", "error", detail=str(exc)[:500])

    try:
        from aihub.web_tools import web_health

        wh = web_health()
        research_cfg = wh.get("research", {}) if isinstance(wh, dict) else {}
        # URL fetch always works; research is a feature that degrades (never hard-errors)
        # when no usable search backend is available (missing/invalid Brave token and no
        # keyless public backends). Degraded keeps deploy/readiness gates open while telling
        # the truth: web_research capability is off until a working backend exists.
        status = "ok" if wh.get("ok") else "degraded"
        if not bool(research_cfg.get("brave_configured")) and not bool(
            research_cfg.get("optional_public_backends")
        ):
            status = "degraded"
        put("web", status, **wh)
    except Exception as exc:  # noqa: BLE001
        logger.error("ops_health: web check failed: %s", exc, exc_info=True)
        put("web", "error", detail=str(exc)[:500])

    try:
        import shutil

        put(
            "frontend_toolchain",
            "ok" if shutil.which("node") and shutil.which("npm") else "inactive",
            node=bool(shutil.which("node")),
            npm=bool(shutil.which("npm")),
        )
    except Exception as exc:  # noqa: BLE001
        put("frontend_toolchain", "unknown", detail=str(exc)[:200])

    overall = _rollup_status(layers)
    if overall == "ok":
        note = "all mandatory probed layers are ok; inactive layers are disabled features"
    elif overall == "degraded":
        note = "one or more layers are degraded/fallback/inactive; inspect layers.*"
    else:
        note = "one or more mandatory layers failed; inspect layers.* and logs"

    return {
        "status": overall,
        "ts": ts,
        "note": note,
        "layers": layers,
    }


def readiness_from_health(health_report: dict[str, Any]) -> dict[str, Any]:
    """Return LB/operator readiness without exposing secrets.

    Readiness is stricter than liveness but not perfectionist: optional disabled
    layers (STT/vision/frontend toolchain) do not block readiness. Mandatory
    layers with ``error`` do block startup/deploy gates.
    """
    layers = health_report.get("layers") if isinstance(health_report, dict) else {}
    if not isinstance(layers, dict):
        layers = {}
    mandatory = ["app", "database", "memory_v1", "memory_v2", "embeddings", "vector", "llm", "psyche", "web"]
    blocking: list[dict[str, Any]] = []
    degraded: list[dict[str, Any]] = []
    for name in mandatory:
        layer = layers.get(name) if isinstance(layers.get(name), dict) else {}
        status = str(layer.get("status") or "unknown")
        if status == "error":
            blocking.append({"layer": name, "status": status, "detail": layer.get("detail", "")})
        elif status in {"degraded", "unknown"}:
            degraded.append({"layer": name, "status": status, "detail": layer.get("detail", "")})
    return {
        "ready": not blocking,
        "status": "ok" if not blocking else "error",
        "health_status": health_report.get("status"),
        "blocking": blocking,
        "degraded": degraded,
        "mandatory_layers": mandatory,
        "ts": health_report.get("ts", time.time()),
    }


def capability_matrix() -> dict[str, Any]:
    """Non-secret compact runtime capability matrix for cockpit and deploy gates."""
    health = get_platform_health()
    layers = health.get("layers") if isinstance(health, dict) else {}
    if not isinstance(layers, dict):
        layers = {}

    def layer_status(name: str) -> str:
        row = layers.get(name) if isinstance(layers.get(name), dict) else {}
        return str(row.get("status") or "unknown")

    caps = {
            "chat": layer_status("llm") != "error" and layer_status("database") != "error",
            "memory": layer_status("memory_v1") != "error" and layer_status("memory_v2") != "error",
            "memory_semantic_index": layer_status("vector") != "error" and layer_status("embeddings") != "error",
            "psyche": layer_status("psyche") != "error",
            "web": layer_status("web") != "error",
            "web_research": layer_status("web") == "ok",
            "stt": layer_status("stt") == "ok",
            "vision": layer_status("vision") == "ok",
            "frontend_toolchain": layer_status("frontend_toolchain") == "ok",
            "cost_ledger": True,
            "adaptive_runtime": True,
            "continuous_self_eval": True,
            "response_variants": True,
            "simulation": True,
            "planner": True,
            "agent_workers": _env_bool("AIHUB_BACKGROUND_AGENT_LOOP_ENABLED", "0")
            or _env_bool("AGENT_AUTOSTART", "0"),
        }
    # Agent worker observability for cockpit
    agent_info: dict[str, Any] = {
        "enabled": bool(caps["agent_workers"]),
        "interval_s": float(os.getenv("AGENT_INTERVAL_S", "3.5") or 3.5),
        "user_id": (os.getenv("AGENT_USER_ID") or "system:maintenance")[:64],
    }
    return {
        "ok": health.get("status") != "error",
        "status": health.get("status"),
        "environment": os.getenv("ENV", "development"),
        "frontend_environment_expected": "production" if os.getenv("ENV") == "production" else os.getenv("ENV", "development"),
        "capabilities": caps,
        "agent_workers": agent_info,
        "layers": {name: layer_status(name) for name in sorted(layers)},
        "ts": health.get("ts", time.time()),
    }
