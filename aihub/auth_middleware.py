"""Authentication, signed principal verification, and ownership enforcement."""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from aihub.auth_patch import collect_hub_auth_secrets, hub_proxy_token_expected, safe_check_api_key
from aihub.core.security import NO_AUTH_PATHS, starts_with_any
from aihub.local_auth import (
    CSRF_HEADER_NAME,
    Principal,
    SAFE_METHODS,
    SESSION_COOKIE_NAME,
    auth_required,
    csrf_valid,
    is_user_scoped_path,
    parse_json_body,
    request_user_ids,
    resolve_session,
)
from aihub.signed_principal import verify_principal_header

log = logging.getLogger(__name__)

_AUTH_PREFIXES = (
    "/auth/login",
    "/auth/logout",
)

_ADMIN_PREFIXES = (
    "/admin/",
    "/cockpit/schema-health",
)


def _is_public_path(path: str) -> bool:
    if path in {"/auth/login"}:
        return True
    return starts_with_any(path, NO_AUTH_PATHS)


def _is_admin_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _ADMIN_PREFIXES)


def _principal_from_signed(request: Request) -> Principal | None:
    header = (request.headers.get("x-aihub-principal") or "").strip()
    if not header:
        return None
    ctx = verify_principal_header(
        header,
        method=request.method,
        path=request.url.path,
    )
    if ctx is None:
        return None
    role = "admin" if "admin" in ctx.roles else "user"
    return Principal(
        account_id=ctx.user_id or ctx.principal_id,
        username=ctx.principal_id,
        tenant_id=ctx.tenant_id,
        role=role,
        status="active",
        session_id=ctx.session_id,
        csrf_token="",
        expires_at=ctx.timestamp + 60.0,
    )


def _principal_from_cookie(request: Request) -> Principal | None:
    token = (request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if not token:
        return None
    return resolve_session(token)


async def _read_body_snapshot(request: Request) -> bytes:
    """Return request body bytes without mutating ASGI receive (streaming-safe)."""
    return await request.body()


def _enforce_ownership(
    request: Request,
    principal: Principal,
    *,
    body: bytes,
) -> None:
    if principal.role == "admin" and _is_admin_path(request.url.path):
        return
    query_user_id = request.query_params.get("user_id")
    payload = parse_json_body(body, request.headers.get("content-type", ""))
    requested = {
        principal.user_id if uid == "me" else uid
        for uid in request_user_ids(request.url.path, query_user_id, payload)
    }
    if not requested:
        return
    allowed = {principal.user_id}
    for uid in requested:
        if uid not in allowed:
            raise HTTPException(status_code=403, detail="forbidden user scope")


def _enforce_csrf(request: Request, principal: Principal) -> None:
    if request.method.upper() in SAFE_METHODS:
        return
    if request.url.path.startswith("/auth/"):
        return
    supplied = (request.headers.get(CSRF_HEADER_NAME) or "").strip()
    if not csrf_valid(principal, supplied):
        raise HTTPException(status_code=403, detail="invalid csrf token")


async def auth_middleware(request: Request, call_next) -> Response:
    path = request.url.path
    if request.method.upper() == "OPTIONS":
        return await call_next(request)

    if _is_public_path(path):
        return await call_next(request)

    body = b""
    if request.method.upper() not in SAFE_METHODS:
        body = await _read_body_snapshot(request)

    principal = _principal_from_signed(request)
    from_signed = principal is not None
    if principal is None:
        principal = _principal_from_cookie(request)

    if is_user_scoped_path(path) or path.startswith("/auth/"):
        if principal is None:
            return JSONResponse(status_code=401, content={"detail": "authentication required"})
        if _is_admin_path(path) and principal.role != "admin":
            return JSONResponse(status_code=403, content={"detail": "admin role required"})
        try:
            if not from_signed:
                _enforce_csrf(request, principal)
            _enforce_ownership(request, principal, body=body)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        request.state.principal = principal
        return await call_next(request)

    if principal is not None:
        request.state.principal = principal
        try:
            _enforce_ownership(request, principal, body=body)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    secrets = collect_hub_auth_secrets()
    if secrets and auth_required():
        proxy_expected = hub_proxy_token_expected()
        req_proxy = (request.headers.get("x-aihub-proxy-token") or "").strip()
        if proxy_expected and req_proxy and req_proxy == proxy_expected:
            return JSONResponse(
                status_code=401,
                content={"detail": "service credential requires signed principal for user routes"},
            )
        try:
            safe_check_api_key(request)
        except HTTPException as exc:
            if int(getattr(exc, "status_code", 0)) == 401:
                return JSONResponse(status_code=401, content={"detail": "invalid api key"})
            raise
    return await call_next(request)
