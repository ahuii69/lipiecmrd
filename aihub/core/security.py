from __future__ import annotations

"""Single security allowlist policy for middleware and API auth gates.

This file replaces the old split-brain ``security.py`` / ``security1.py`` pair.
It keeps a broad firewall allowlist for compatibility but a strict auth allowlist; user-scoped operational paths such as /system/health/{user_id} still require auth.
"""

from typing import Tuple

ALWAYS_ALLOW_PREFIXES: Tuple[str, ...] = (
    "/health",
    "/system/ping",
    "/system/health",
    "/ops/health",
    "/ops/ready",
    "/ops/capabilities",
    "/openapi.json",
    "/gpt-openapi.json",
    "/docs",
    "/redoc",
    "/ai",
    "/system/self-heal",
    "/system/self-heal-db",
    "/psyche/brain/live",
    "/sse",
    "/system/security",
    "/memory/get",
    "/memory",
    "/memory/",
    "/fs",
    "/fs/",
)

NO_AUTH_PATHS: Tuple[str, ...] = (
    "/system/ping",
    "/ops/health",
    "/ops/ready",
    "/ops/capabilities",
    "/openapi.json",
    "/gpt-openapi.json",
    "/docs",
    "/redoc",
)


def starts_with_any(path: str, prefixes: Tuple[str, ...]) -> bool:
    if not path:
        return False
    return any(path.startswith(prefix) for prefix in prefixes)
