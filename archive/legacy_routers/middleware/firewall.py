"""LEGACY / NOT REGISTERED — does not protect the running application.

Status (06.07 repair sprint, P1 security review):
- This ASGI middleware is **not added** via ``app.add_middleware(...)`` anywhere in
  ``aihub/main.py``. It provides **zero** runtime protection today. Do not treat its presence in
  this file as evidence that anomaly-based blocking is active.
- Its block-list (``aihub.workers.nervous_system.blocked``) is only ever populated by
  ``aihub.workers.nervous_system.nervous_loop``, which itself is never started (no call to
  ``aihub.workers.nervous_system.start()`` anywhere in the active runtime). So even if this
  middleware were registered today, ``blocked`` would always be empty.
- A previous version logged a warning on every single ``/fs`` request ("DEBUG_FIREWALL_FS");
  that debug logging has been removed (06.07) since it had no diagnostic owner and would spam
  logs in production if ever wired up.

To make this a real protection, a deliberate follow-up would need to: (1) start
``nervous_system.nervous_loop`` as a supervised background task, (2) register this class with
``app.add_middleware(FirewallMiddleware)`` in ``aihub/main.py``, and (3) add a regression test
that a blocked path actually returns 403. None of that is done here — see ``06.07naprawa.md``.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Scope, Receive, Send
from fastapi.responses import JSONResponse

from aihub.workers.nervous_system import blocked
from aihub.core.security import ALWAYS_ALLOW_PREFIXES, starts_with_any


class FirewallMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            path = scope.get("path", "") or ""

            # Never block allowlisted paths
            if starts_with_any(path, ALWAYS_ALLOW_PREFIXES):
                await self.app(scope, receive, send)
                return

            # Block only exact match (as originally)
            if path in blocked:
                response = JSONResponse(
                    {"status": "blocked", "reason": "anomaly detected"},
                    status_code=403,
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
