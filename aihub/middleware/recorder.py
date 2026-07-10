"""LEGACY / NOT REGISTERED — does not run in the live application.

Status (06.07 repair sprint, P1 security review):
- This ASGI middleware is **not added** via ``app.add_middleware(...)`` anywhere in
  ``aihub/main.py``. It does not capture, store, or expose any request/response data today.
- If it were ever registered as-is, it would persist **full raw request and response bodies**
  (base64-encoded, no redaction) plus a truncated API-key fingerprint into the ``events`` /
  ``sidecar.http_events`` table — and the archived ``aihub/api/admin_router.py``
  (``archive/legacy_routers/admin_router.py``) used to expose that table's bodies unredacted over
  HTTP. Do not register this middleware without first adding secret/PII redaction to the bodies
  it captures, and without re-deciding whether the admin exposure endpoint should exist at all.
- ``_guess_data_dir()`` below still references the stale hardcoded path ``/root/ai-hub/data`` as
  one of its fallbacks; kept as-is since this module is inert, but do not rely on that fallback
  if this module is ever revived.
"""

import base64
import hashlib
import json
import os
import time
import uuid
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from starlette.types import ASGIApp, Receive, Scope, Send, Message

from aihub.sidecar_db import ensure_http_events_schema_sqlite, http_events_insert_row, is_postgres

log = logging.getLogger("aihub.recorder")


def _guess_data_dir() -> Path:
    env = os.getenv("AIHUB_DATA_DIR", "").strip()
    if env:
        return Path(env)
    p1 = Path("/root/ai-hub/data")
    if p1.exists():
        return p1
    repo_guess = Path(__file__).resolve().parents[2] / "data"
    if repo_guess.exists():
        return repo_guess
    return Path("/tmp/aihub-data")


class EventRecorderMiddleware:
    """
    Prosty ASGI middleware:
      - łapie request/response body (base64)
      - zapisuje do SQLite
      - NIGDY nie ma prawa wysypać aplikacji (błędy lecą do loga)
    """

    def __init__(self, app: ASGIApp):
        self.app = app
        self._recording_disabled = False
        try:
            if is_postgres():
                log.info("recorder: PostgreSQL (sidecar.http_events)")
            else:
                ensure_http_events_schema_sqlite()
                log.info("recorder db: %s", _guess_data_dir() / "events.db")
        except Exception as e:
            log.error("recorder schema init failed: %s", e)
            self._recording_disabled = True

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or self._recording_disabled:
            await self.app(scope, receive, send)
            return

        start = time.time()
        req_body = b""
        resp_body = b""
        status_code = 500
        resp_headers: Dict[bytes, bytes] = {}

        async def receive_wrapper():
            nonlocal req_body
            msg = await receive()
            if msg.get("type") == "http.request":
                req_body += msg.get("body", b"") or b""
            return msg

        async def send_wrapper(message: Message):
            nonlocal resp_body, status_code, resp_headers
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
                hdrs = message.get("headers") or []
                try:
                    resp_headers = dict(hdrs)
                except Exception:
                    resp_headers = {}
            elif message.get("type") == "http.response.body":
                resp_body += message.get("body", b"") or b""
            await send(message)

        await self.app(scope, receive_wrapper, send_wrapper)

        latency_ms = int((time.time() - start) * 1000)

        try:
            headers_list = scope.get("headers") or []
            headers = dict(headers_list)  # bytes->bytes
        except Exception:
            headers = {}

        api_key = (headers.get(b"x-api-key") or b"").decode(errors="ignore")
        api_key_fp = hashlib.sha256(api_key.encode()).hexdigest()[:16] if api_key else ""

        client_ip = ""
        try:
            if scope.get("client"):
                client_ip = scope["client"][0]
        except Exception:
            client_ip = ""

        user_agent = (headers.get(b"user-agent") or b"").decode(errors="ignore")

        # Bezpieczne zdekodowanie headers do JSON
        def hdr_json(h: Dict[bytes, bytes]) -> str:
            out: Dict[str, str] = {}
            for k, v in (h or {}).items():
                try:
                    out[k.decode(errors="ignore")] = v.decode(errors="ignore")
                except Exception as exc:
                    logging.getLogger("aihub.recorder").debug("Header decode skipped: %s", exc)
            return json.dumps(out, ensure_ascii=False)

        record = (
            str(uuid.uuid4()),
            int(time.time()),
            scope.get("method"),
            scope.get("path"),
            (scope.get("query_string") or b"").decode(errors="ignore"),
            status_code,
            latency_ms,
            hdr_json(headers),
            base64.b64encode(req_body).decode(),
            hdr_json(resp_headers),
            base64.b64encode(resp_body).decode(),
            client_ip,
            user_agent,
            api_key_fp,
        )

        try:
            http_events_insert_row(record)
        except Exception as e:
            log.error("RECORDER ERROR: %s", e)
