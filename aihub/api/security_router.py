"""ACTIVE_CONFIRMED: mounted in :mod:`aihub.main` — ``app.include_router(security_router)``.

Prefix ``/system/security/*``. Canonical route list: :mod:`aihub.canonical_http_surface`.

**Not** the default for most of ``aihub/api/*`` — those modules are **unmounted** unless
listed in ``main`` — see ``aihub/api/_LEGACY.md``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from fastapi import APIRouter, Request

from aihub.core.security import ALWAYS_ALLOW_PREFIXES
from aihub.core.security import NO_AUTH_PATHS
from aihub.workers.nervous_system import blocked

router = APIRouter(prefix="/system/security", tags=["security"])


@router.get("/allowlist", operation_id="security_allowlist")
def security_allowlist() -> Dict[str, Any]:
    return {
        "status": "ok",
        "ts": int(time.time()),
        "always_allow_prefixes": list(ALWAYS_ALLOW_PREFIXES),
        "no_auth_paths": list(NO_AUTH_PATHS),
    }


@router.get("/blocked", operation_id="security_blocked")
def security_blocked() -> Dict[str, Any]:
    items: List[str] = sorted(list(blocked))[:1000]
    return {
        "status": "ok",
        "ts": int(time.time()),
        "blocked_count": len(blocked),
        "blocked_preview": items,
    }


@router.get("/whoami", operation_id="security_whoami")
def security_whoami(request: Request) -> Dict[str, Any]:
    hdr = request.headers
    return {
        "status": "ok",
        "ts": int(time.time()),
        "client": request.client.host if request.client else None,
        "path": request.url.path,
        "has_x_api_key": bool(hdr.get("x-api-key") or hdr.get("X-API-Key")),
        "x_forwarded_for": hdr.get("x-forwarded-for"),
        "user_agent": hdr.get("user-agent"),
    }
