"""ACTIVE_CONFIRMED: canonical HTTP surface for ``aihub.main:app`` (truth manifest + introspection).

This file is the **authoritative list** of ``(method, path)`` pairs for the production
``app`` object. **Not** an exhaustive map of every Python module in the repo — only what
FastAPI registers (see ``collect_route_keys``).

This module is the **single committed list** of every route FastAPI registers on the
production app object.  ``tests/test_canonical_http_surface.py`` compares it to live
introspection so **drift** (new/removed/changed paths) fails CI until this manifest
is updated.

Columns per row: ``(method, path, source_file, cockpit_usage, tests_hint)``.

* ``source_file`` — which module owns the route (heuristic by path prefix).
* ``cockpit_usage`` — **TAK** if ``cockpit/lib/api/client.ts`` issues a direct
  ``request()`` to that path pattern; **NIE** if that file contains no such
  call (including dynamic paths built with template literals); **BRAK DANYCH**
  only when usage is uncertain without deeper tracing.
* ``tests_hint`` — **BRAK DANYCH** if no ``tests/*.py`` file was found with an
  HTTP call to this route; otherwise one or more ``tests/...py`` paths
  (semicolon-separated) that contain that evidence.

``aihub/api/*`` (legacy unmounted) is **out of scope** — not on ``app``.

Cockpit Next.js proxy (``cockpit/app/api/aihub/[...path]/route.ts``) may only
forward routes listed in ``cockpit/lib/api/cockpit-proxy-allowlist.json``; that
list is a **subset** of this manifest and is checked by
``tests/test_cockpit_proxy_allowlist.py`` and cockpit ``npm run test`` (Vitest).

Agent / cognitive HTTP semantics (see ``aihub/agent_http_surface.py``):

- **Canonical execution:** ``POST /agent/run`` (primary user-text cycle),
  ``POST /agent/loop`` (multi-iteration aggregate). Responses include
  ``X-AIHub-Endpoint-Role`` = ``agent-canonical-run`` / ``agent-canonical-loop``
  and ``X-AIHub-Canonical-Agent-Flow`` = ``run`` / ``loop``.
- **Secondary worker:** ``POST /agent/tick/{user_id}`` (optional; disable with
  ``AIHUB_ENABLE_AGENT_TICK_HTTP=0``), queue ops ``/agent/enable``,
  ``/agent/enqueue``, ``GET /agent/tasks/{user_id}``.
- **Secondary goals read:** ``GET /agent/goals/{user_id}``.
- **Observability:** ``GET /agent/status/{user_id}``, ``GET …/goals/…/trace``,
  ``GET /cognitive/health`` (``X-AIHub-Cognitive-Surface``).
- **Debug-only:** ``POST /cognitive/decide`` (env ``AIHUB_ENABLE_COGNITIVE_DEBUG_ENDPOINT``),
  ``GET …/goals/…/links`` and ``…/events`` (env ``AIHUB_ENABLE_AGENT_GOAL_ARTIFACT_HTTP``).
"""

from __future__ import annotations

from typing import FrozenSet, Tuple

MethodPath = Tuple[str, str]


def collect_route_keys(app) -> FrozenSet[MethodPath]:
    """Return frozenset of (METHOD, path) for all user-facing routes on ``app``.

    Skips HEAD (duplicate of GET for API routes). Includes FastAPI docs/OpenAPI
    routes and every ``APIRoute`` from ``main`` + mounted ``*_api`` routers.
    """
    keys: set[MethodPath] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is None:
            continue
        if not methods:
            continue
        for method in methods:
            mu = method.upper()
            if mu == "HEAD":
                continue
            keys.add((mu, path))
    return frozenset(keys)


def source_file_for_path(path: str) -> str:
    if path.startswith("/auth/"):
        return "aihub/auth_api.py"
    if path.startswith("/admin/"):
        return "aihub/admin_api.py"
    if path.startswith("/agent/"):
        return "aihub/agent_api.py"
    if (
        path == "/chat/sessions"
        or path.startswith("/chat/session/")
        or path == "/chat/session"
    ):
        return "aihub/chat_sessions_api.py"
    if path.startswith("/chat/"):
        return "aihub/chat_api.py"
    if path.startswith("/cockpit/"):
        return "aihub/cockpit_api.py"
    if path.startswith("/system/security/"):
        return "aihub/api/security_router.py"
    if path.startswith("/system/self-heal-db/"):
        return "aihub/api/self_heal_status_router.py"
    if path.startswith("/memory/v2/"):
        return "aihub/memory_v2_api.py"
    if path.startswith("/psyche/v2/"):
        return "aihub/psyche_v2_api.py"
    if path in ("/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"):
        return "fastapi/starlette (OpenAPI UI)"
    return "aihub/main.py"


# Row order: stable sort (method, path). Regenerate when routes change (test failure).
# Last synced from introspection of ``aihub.main:app`` (see test failure diff).
CANONICAL_HTTP_ROUTES: Tuple[Tuple[str, str, str, str, str], ...] = (
    (
        "DELETE",
        "/chat/session",
        "aihub/chat_sessions_api.py",
        "TAK",
        "tests/test_chat_sessions_api.py",
    ),
    ("GET", "/admin/ping", "aihub/admin_api.py", "NIE", "BRAK DANYCH"),
    ("GET", "/auth/me", "aihub/auth_api.py", "TAK", "tests/test_auth_ownership.py"),
    ("POST", "/auth/login", "aihub/auth_api.py", "TAK", "tests/test_auth_ownership.py"),
    ("POST", "/auth/logout", "aihub/auth_api.py", "TAK", "tests/test_auth_ownership.py"),
    (
        "GET",
        "/agent/goals/{user_id}",
        "aihub/agent_api.py",
        "NIE",
        "tests/test_agent_api_http_integration.py",
    ),
    (
        "GET",
        "/agent/goals/{user_id}/{goal_id}/events",
        "aihub/agent_api.py",
        "NIE",
        "tests/test_agent_http_surface.py",
    ),
    (
        "GET",
        "/agent/goals/{user_id}/{goal_id}/links",
        "aihub/agent_api.py",
        "NIE",
        "tests/test_agent_http_surface.py",
    ),
    (
        "GET",
        "/agent/goals/{user_id}/{goal_id}/trace",
        "aihub/agent_api.py",
        "TAK",
        "BRAK DANYCH",
    ),
    ("GET", "/agent/status/{user_id}", "aihub/agent_api.py", "TAK", "BRAK DANYCH"),
    ("GET", "/agent/tasks/{user_id}", "aihub/agent_api.py", "NIE", "BRAK DANYCH"),
    (
        "GET",
        "/chat/capabilities",
        "aihub/chat_api.py",
        "TAK",
        "tests/test_chat_api.py",
    ),
    (
        "GET",
        "/chat/sessions",
        "aihub/chat_sessions_api.py",
        "TAK",
        "tests/test_chat_sessions_api.py",
    ),
    (
        "GET",
        "/cockpit/calibration/{user_id}",
        "aihub/cockpit_api.py",
        "TAK",
        "tests/test_calibration_smoke.py",
    ),
    (
        "GET",
        "/cockpit/consistency/{user_id}",
        "aihub/cockpit_api.py",
        "TAK",
        "BRAK DANYCH",
    ),
    ("GET", "/cockpit/health", "aihub/cockpit_api.py", "NIE", "BRAK DANYCH"),
    (
        "GET",
        "/cockpit/identity/{user_id}",
        "aihub/cockpit_api.py",
        "TAK",
        "tests/test_smoke_behavioral.py; tests/test_cockpit_memory_psyche_v2.py",
    ),
    (
        "GET",
        "/cockpit/memory-v2/retrieval/{user_id}",
        "aihub/cockpit_api.py",
        "NIE",
        "tests/test_smoke_behavioral.py; tests/test_v2_api_endpoints.py",
    ),
    (
        "GET",
        "/cockpit/memory-v2/{user_id}",
        "aihub/cockpit_api.py",
        "TAK",
        "tests/test_smoke_behavioral.py; tests/test_cockpit_memory_psyche_v2.py; tests/test_v2_writeback_integration.py",
    ),
    (
        "GET",
        "/cockpit/overview/{user_id}",
        "aihub/cockpit_api.py",
        "TAK",
        "BRAK DANYCH",
    ),
    ("GET", "/cockpit/policy/{user_id}", "aihub/cockpit_api.py", "TAK", "BRAK DANYCH"),
    (
        "GET",
        "/cockpit/psyche-v2/habits/{user_id}",
        "aihub/cockpit_api.py",
        "TAK",
        "tests/test_smoke_behavioral.py; tests/test_v2_api_endpoints.py",
    ),
    (
        "GET",
        "/cockpit/psyche-v2/relations/{user_id}",
        "aihub/cockpit_api.py",
        "TAK",
        "tests/test_smoke_behavioral.py; tests/test_v2_api_endpoints.py",
    ),
    (
        "GET",
        "/cockpit/psyche-v2/{user_id}",
        "aihub/cockpit_api.py",
        "TAK",
        "tests/test_psyche_canonical_core.py; tests/test_smoke_behavioral.py; tests/test_cockpit_memory_psyche_v2.py; tests/test_v2_writeback_integration.py",
    ),
    (
        "GET",
        "/cockpit/reflections/{user_id}",
        "aihub/cockpit_api.py",
        "TAK",
        "BRAK DANYCH",
    ),
    (
        "GET",
        "/cockpit/schema-health",
        "aihub/cockpit_api.py",
        "TAK",
        "tests/test_v2_schema_migration.py",
    ),
    (
        "GET",
        "/cockpit/simulations/{user_id}",
        "aihub/cockpit_api.py",
        "TAK",
        "BRAK DANYCH",
    ),
    (
        "GET",
        "/cognitive/health",
        "aihub/main.py",
        "TAK",
        "tests/test_agent_http_surface.py",
    ),
    ("GET", "/docs", "fastapi/starlette (OpenAPI UI)", "NIE", "BRAK DANYCH"),
    (
        "GET",
        "/docs/oauth2-redirect",
        "fastapi/starlette (OpenAPI UI)",
        "NIE",
        "BRAK DANYCH",
    ),
    ("GET", "/gpt-openapi.json", "aihub/main.py", "NIE", "BRAK DANYCH"),
    (
        "GET",
        "/memory/health",
        "aihub/main.py",
        "NIE",
        "tests/test_memory_canonical_core.py",
    ),
    (
        "GET",
        "/memory/v2/autobio/{user_id}",
        "aihub/memory_v2_api.py",
        "NIE",
        "BRAK DANYCH",
    ),
    (
        "GET",
        "/memory/v2/contradictions/{user_id}",
        "aihub/memory_v2_api.py",
        "TAK",
        "tests/test_memory_v2_api.py; tests/test_smoke_behavioral.py",
    ),
    (
        "GET",
        "/memory/v2/index-jobs",
        "aihub/memory_v2_api.py",
        "TAK",
        "tests/test_memory_context_pack_and_index_jobs.py",
    ),
    (
        "GET",
        "/memory/v2/procedures/{user_id}",
        "aihub/memory_v2_api.py",
        "TAK",
        "tests/test_memory_v2_api.py",
    ),
    (
        "GET",
        "/memory/v2/retrieval-explain/{user_id}",
        "aihub/memory_v2_api.py",
        "NIE",
        "tests/test_smoke_behavioral.py; tests/test_v2_api_endpoints.py",
    ),
    (
        "GET",
        "/memory/v2/summary/{user_id}",
        "aihub/memory_v2_api.py",
        "NIE",
        "tests/test_memory_v2_api.py; tests/test_smoke_behavioral.py; tests/test_v2_schema_migration.py",
    ),
    ("GET", "/openapi.json", "fastapi/starlette (OpenAPI UI)", "NIE", "BRAK DANYCH"),
    (
        "GET",
        "/ops/health",
        "aihub/main.py",
        "TAK",
        "tests/test_smoke_behavioral.py",
    ),
    (
        "GET",
        "/ops/capabilities",
        "aihub/main.py",
        "TAK",
        "tests/test_ops_readiness_and_release_audit.py",
    ),
    (
        "GET",
        "/ops/ready",
        "aihub/main.py",
        "TAK",
        "tests/test_ops_readiness_and_release_audit.py",
    ),
    (
        "GET",
        "/psyche/v2/habits/{user_id}",
        "aihub/psyche_v2_api.py",
        "NIE",
        "tests/test_smoke_behavioral.py; tests/test_v2_api_endpoints.py",
    ),
    (
        "GET",
        "/psyche/v2/history/{user_id}",
        "aihub/psyche_v2_api.py",
        "NIE",
        "tests/test_psyche_v2_api.py",
    ),
    (
        "GET",
        "/psyche/v2/policy/{user_id}",
        "aihub/psyche_v2_api.py",
        "NIE",
        "tests/test_psyche_v2_api.py",
    ),
    (
        "GET",
        "/psyche/v2/relations/{user_id}",
        "aihub/psyche_v2_api.py",
        "NIE",
        "tests/test_smoke_behavioral.py; tests/test_v2_api_endpoints.py",
    ),
    (
        "GET",
        "/psyche/v2/{user_id}",
        "aihub/psyche_v2_api.py",
        "NIE",
        "tests/test_psyche_v2_api.py; tests/test_smoke_behavioral.py",
    ),
    (
        "GET",
        "/psyche/{user_id}",
        "aihub/main.py",
        "NIE",
        "tests/test_psyche_canonical_core.py",
    ),
    ("GET", "/redoc", "fastapi/starlette (OpenAPI UI)", "NIE", "BRAK DANYCH"),
    ("GET", "/sse/{user_id}", "aihub/main.py", "NIE", "BRAK DANYCH"),
    (
        "GET",
        "/system/health/{user_id}",
        "aihub/main.py",
        "TAK",
        "tests/test_chat_agent_regression.py",
    ),
    (
        "GET",
        "/system/ping",
        "aihub/main.py",
        "TAK",
        "tests/test_product_system_gate.py; tests/test_smoke_behavioral.py",
    ),
    (
        "GET",
        "/system/security/allowlist",
        "aihub/api/security_router.py",
        "NIE",
        "BRAK DANYCH",
    ),
    (
        "GET",
        "/system/security/blocked",
        "aihub/api/security_router.py",
        "NIE",
        "BRAK DANYCH",
    ),
    (
        "GET",
        "/system/security/whoami",
        "aihub/api/security_router.py",
        "NIE",
        "BRAK DANYCH",
    ),
    (
        "GET",
        "/system/self-heal-db/status",
        "aihub/api/self_heal_status_router.py",
        "NIE",
        "BRAK DANYCH",
    ),
    ("GET", "/system/snapshot/list", "aihub/main.py", "NIE", "BRAK DANYCH"),
    (
        "GET",
        "/chat/session/{session_id}/history",
        "aihub/chat_sessions_api.py",
        "TAK",
        "tests/test_chat_sessions_api.py",
    ),
    (
        "PATCH",
        "/chat/session/rename",
        "aihub/chat_sessions_api.py",
        "TAK",
        "tests/test_chat_sessions_api.py",
    ),
    ("POST", "/agent/enable", "aihub/agent_api.py", "NIE", "BRAK DANYCH"),
    ("POST", "/agent/enqueue", "aihub/agent_api.py", "NIE", "BRAK DANYCH"),
    (
        "POST",
        "/agent/loop",
        "aihub/agent_api.py",
        "TAK",
        "tests/test_agent_api_http_integration.py; tests/test_agent_http_surface.py",
    ),
    (
        "POST",
        "/agent/run",
        "aihub/agent_api.py",
        "TAK",
        "tests/test_agent_api_http_integration.py; tests/test_agent_http_surface.py; tests/test_v2_trace_fields.py",
    ),
    (
        "POST",
        "/agent/tick/{user_id}",
        "aihub/agent_api.py",
        "NIE",
        "tests/test_agent_api_http_integration.py; tests/test_agent_http_surface.py",
    ),
    ("POST", "/chat/capabilities/execute", "aihub/chat_api.py", "TAK", "BRAK DANYCH"),
    (
        "POST",
        "/chat/session/auto-title",
        "aihub/chat_sessions_api.py",
        "TAK",
        "tests/test_chat_sessions_api.py",
    ),
    (
        "POST",
        "/chat/stt",
        "aihub/chat_api.py",
        "TAK",
        "tests/test_chat_stt.py",
    ),
    (
        "POST",
        "/chat/upload",
        "aihub/chat_api.py",
        "TAK",
        "tests/test_chat_file_upload.py",
    ),
    (
        "POST",
        "/chat/turn",
        "aihub/chat_api.py",
        "TAK",
        "tests/test_product_system_gate.py; tests/test_chat_api.py; tests/test_v2_writeback_integration.py",
    ),
    (
        "POST",
        "/cognitive/decide",
        "aihub/main.py",
        "NIE",
        "tests/test_agent_api_http_integration.py; tests/test_agent_http_surface.py",
    ),
    ("POST", "/fs/list", "aihub/main.py", "NIE", "BRAK DANYCH"),
    ("POST", "/fs/read", "aihub/main.py", "NIE", "BRAK DANYCH"),
    ("POST", "/fs/write", "aihub/main.py", "NIE", "BRAK DANYCH"),
    (
        "POST",
        "/memory/add",
        "aihub/main.py",
        "NIE",
        "tests/test_memory_v1_http_legacy.py; tests/test_memory_canonical_core.py",
    ),
    (
        "POST",
        "/memory/search",
        "aihub/main.py",
        "NIE",
        "tests/test_memory_v1_http_legacy.py; tests/test_memory_canonical_core.py",
    ),
    (
        "POST",
        "/memory/v2/autobio/compact/{user_id}",
        "aihub/memory_v2_api.py",
        "NIE",
        "BRAK DANYCH",
    ),
    (
        "POST",
        "/memory/v2/consolidate/{user_id}",
        "aihub/memory_v2_api.py",
        "NIE",
        "BRAK DANYCH",
    ),
    (
        "POST",
        "/memory/v2/context-pack",
        "aihub/memory_v2_api.py",
        "TAK",
        "tests/test_memory_context_pack_and_index_jobs.py",
    ),
    (
        "POST",
        "/memory/v2/forgetting/{user_id}",
        "aihub/memory_v2_api.py",
        "NIE",
        "tests/test_smoke_behavioral.py; tests/test_v2_api_endpoints.py",
    ),
    (
        "POST",
        "/memory/v2/index-jobs/process",
        "aihub/memory_v2_api.py",
        "TAK",
        "tests/test_memory_context_pack_and_index_jobs.py",
    ),
    (
        "POST",
        "/memory/v2/item",
        "aihub/memory_v2_api.py",
        "NIE",
        "tests/test_memory_v2_api.py; tests/test_v2_api_endpoints.py",
    ),
    (
        "POST",
        "/memory/v2/item/archive",
        "aihub/memory_v2_api.py",
        "NIE",
        "BRAK DANYCH",
    ),
    (
        "POST",
        "/memory/v2/item/suppress",
        "aihub/memory_v2_api.py",
        "NIE",
        "tests/test_memory_v2_api.py",
    ),
    (
        "POST",
        "/memory/v2/item/pin",
        "aihub/memory_v2_api.py",
        "NIE",
        "tests/test_memory_v2_api.py",
    ),
    (
        "POST",
        "/memory/v2/search",
        "aihub/memory_v2_api.py",
        "NIE",
        "tests/test_memory_v2_api.py",
    ),
    (
        "GET",
        "/psyche/runtime/{user_id}",
        "aihub/main.py",
        "NIE",
        "tests/test_psyche_web_full_wiring.py",
    ),
    (
        "POST",
        "/psyche/reflect",
        "aihub/main.py",
        "NIE",
        "tests/test_psyche_engine_runtime.py",
    ),
    ("POST", "/psyche/update", "aihub/main.py", "NIE", "BRAK DANYCH"),
    (
        "POST",
        "/psyche/v2/event",
        "aihub/psyche_v2_api.py",
        "NIE",
        "tests/test_psyche_v2_api.py",
    ),
    (
        "GET",
        "/psyche/v2/runtime/{user_id}",
        "aihub/psyche_v2_api.py",
        "NIE",
        "tests/test_psyche_web_full_wiring.py",
    ),
    (
        "POST",
        "/psyche/v2/reflect/{user_id}",
        "aihub/psyche_v2_api.py",
        "NIE",
        "tests/test_psyche_v2_api.py",
    ),
    ("POST", "/system/snapshot/create", "aihub/main.py", "NIE", "BRAK DANYCH"),
    ("POST", "/system/snapshot/restore", "aihub/main.py", "NIE", "BRAK DANYCH"),
    (
        "POST",
        "/turn",
        "aihub/main.py",
        "NIE",
        "tests/test_product_system_gate.py; tests/test_chat_api.py",
    ),
    (
        "POST",
        "/web/fetch",
        "aihub/main.py",
        "NIE",
        "tests/test_product_system_gate.py; tests/test_web_tools_runtime.py",
    ),
    (
        "GET",
        "/web/health",
        "aihub/main.py",
        "NIE",
        "tests/test_psyche_web_full_wiring.py",
    ),
    (
        "POST",
        "/web/ingest",
        "aihub/main.py",
        "NIE",
        "tests/test_psyche_web_full_wiring.py",
    ),
    (
        "POST",
        "/web/research",
        "aihub/main.py",
        "NIE",
        "tests/test_psyche_web_full_wiring.py",
    ),
)

EXPECTED_ROUTE_KEYS: FrozenSet[MethodPath] = frozenset(
    (m, p) for m, p, _, _, _ in CANONICAL_HTTP_ROUTES
)

_COCKPIT_VALUES = frozenset({"TAK", "NIE", "BRAK DANYCH"})


def assert_manifest_well_formed() -> None:
    """Runtime check: manifest rows are internally consistent."""
    if len(EXPECTED_ROUTE_KEYS) != len(CANONICAL_HTTP_ROUTES):
        raise AssertionError(
            "duplicate (method, path) in CANONICAL_HTTP_ROUTES "
            f"({len(CANONICAL_HTTP_ROUTES)} rows vs {len(EXPECTED_ROUTE_KEYS)} keys)"
        )
    for _method, _path, _src, cockpit, tests in CANONICAL_HTTP_ROUTES:
        if cockpit not in _COCKPIT_VALUES:
            raise AssertionError(f"bad cockpit value: {cockpit}")
        if not tests:
            raise AssertionError("tests hint empty")


assert_manifest_well_formed()
