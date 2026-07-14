from __future__ import annotations

"""Single security allowlist policy for middleware and API auth gates.

This file replaces the old split-brain ``security.py`` / ``security1.py`` pair.
It keeps a broad firewall allowlist for compatibility but a strict auth allowlist; user-scoped operational paths such as /system/health/{user_id} still require auth.
"""

from typing import Tuple

# 19.07: keep ALWAYS_ALLOW aligned with NO_AUTH (+ docs/self-heal status).
# Broad legacy prefixes (/memory, /fs, /sse, /psyche/brain) removed — they were
# only consumed by the archived FirewallMiddleware and invited auth bypass if remounted.
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
    "/system/self-heal",
    "/system/self-heal-db",
    "/system/security",
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
