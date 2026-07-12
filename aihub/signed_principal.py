"""HMAC-signed principal context between Cockpit BFF and AI-Hub backend."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any

HEADER_NAME = "x-aihub-principal"
SCHEME = "v1"
DEFAULT_TTL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class SignedPrincipalContext:
    principal_id: str
    user_id: str
    tenant_id: str
    roles: tuple[str, ...]
    session_id: str
    method: str
    path: str
    timestamp: float
    request_id: str
    nonce: str


def _secret_bytes() -> bytes:
    raw = (
        os.getenv("AIHUB_BFF_PRINCIPAL_SECRET")
        or os.getenv("AIHUB_PROXY_TOKEN")
        or os.getenv("AIHUB_API_KEY")
        or os.getenv("API_KEY")
        or ""
    ).strip()
    if not raw:
        raise RuntimeError("AIHUB_BFF_PRINCIPAL_SECRET is required for signed principals")
    return raw.encode("utf-8")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def sign_principal_context(
    *,
    principal_id: str,
    user_id: str,
    tenant_id: str,
    roles: list[str] | tuple[str, ...],
    session_id: str,
    method: str,
    path: str,
    request_id: str,
    nonce: str,
    timestamp: float | None = None,
) -> str:
    ts = time.time() if timestamp is None else float(timestamp)
    payload = {
        "principal_id": principal_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "roles": list(roles),
        "session_id": session_id,
        "method": method.upper(),
        "path": path,
        "timestamp": ts,
        "request_id": request_id,
        "nonce": nonce,
    }
    canonical = _canonical_payload(payload).encode("utf-8")
    digest = hmac.new(_secret_bytes(), canonical, hashlib.sha256).digest()
    return f"{SCHEME}.{_b64url(canonical)}.{_b64url(digest)}"


def verify_principal_header(
    header_value: str,
    *,
    method: str,
    path: str,
    max_age_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> SignedPrincipalContext | None:
    if not header_value or not header_value.startswith(f"{SCHEME}."):
        return None
    parts = header_value.split(".", 2)
    if len(parts) != 3:
        return None
    _, payload_b64, sig_b64 = parts
    try:
        canonical = _b64url_decode(payload_b64)
        supplied_sig = _b64url_decode(sig_b64)
    except (ValueError, UnicodeError):
        return None
    expected = hmac.new(_secret_bytes(), canonical, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, supplied_sig):
        return None
    try:
        payload = json.loads(canonical.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    current = time.time() if now is None else float(now)
    ts = float(payload.get("timestamp") or 0.0)
    if ts <= 0 or abs(current - ts) > max(1, int(max_age_seconds)):
        return None
    req_method = str(payload.get("method") or "").upper()
    req_path = str(payload.get("path") or "")
    if req_method != method.upper() or req_path != path:
        return None
    roles_raw = payload.get("roles") or []
    roles = tuple(str(r) for r in roles_raw) if isinstance(roles_raw, list) else ()
    return SignedPrincipalContext(
        principal_id=str(payload.get("principal_id") or ""),
        user_id=str(payload.get("user_id") or ""),
        tenant_id=str(payload.get("tenant_id") or ""),
        roles=roles,
        session_id=str(payload.get("session_id") or ""),
        method=req_method,
        path=req_path,
        timestamp=ts,
        request_id=str(payload.get("request_id") or ""),
        nonce=str(payload.get("nonce") or ""),
    )
