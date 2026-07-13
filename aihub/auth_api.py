"""HTTP endpoints for local account sessions."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from aihub.local_auth import (
    SESSION_COOKIE_NAME,
    Principal,
    RegistrationClosedError,
    UsernameTakenError,
    WeakPasswordError,
    authenticate,
    cookie_secure,
    create_bootstrap_admin,
    issue_session,
    registration_open,
    revoke_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_REGISTER_WINDOW_SECONDS = 900.0
_REGISTER_MAX_ATTEMPTS = 8
_register_attempts: dict[str, deque[float]] = defaultdict(deque)
_register_lock = threading.Lock()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def _authenticated_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:128]
    if request.client and request.client.host:
        return request.client.host[:128]
    return "unknown"


def _check_register_rate_limit(ip: str) -> None:
    now = time.time()
    with _register_lock:
        bucket = _register_attempts[ip]
        while bucket and now - bucket[0] > _REGISTER_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= _REGISTER_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="too many registration attempts")
        bucket.append(now)


def _session_response(issued) -> Response:
    response = JSONResponse(
        {
            "principal": issued.principal.public_dict(),
            "csrf_token": issued.principal.csrf_token,
            "expires_at": issued.principal.expires_at,
        }
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        issued.token,
        max_age=max(0, int(issued.principal.expires_at - time.time())),
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/registration-status")
def registration_status() -> dict[str, Any]:
    open_now = registration_open()
    return {
        "open": open_now,
        "mode": "bootstrap_first_admin" if open_now else "closed",
        "detail": (
            "Pierwsze konto admina można utworzyć teraz."
            if open_now
            else "Rejestracja zamknięta — konto już istnieje."
        ),
    }


@router.post("/register")
def register(body: RegisterRequest, request: Request) -> Response:
    _check_register_rate_limit(_client_ip(request))
    if not registration_open():
        raise HTTPException(status_code=403, detail="registration closed")
    try:
        account = create_bootstrap_admin(username=body.username, password=body.password)
    except RegistrationClosedError as exc:
        raise HTTPException(status_code=403, detail="registration closed") from exc
    except UsernameTakenError as exc:
        raise HTTPException(status_code=409, detail="username already exists") from exc
    except WeakPasswordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    issued = issue_session(account)
    return _session_response(issued)


@router.post("/login")
def login(body: LoginRequest) -> Response:
    account = authenticate(body.username, body.password)
    if account is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    issued = issue_session(account)
    return _session_response(issued)


@router.post("/logout")
def logout(request: Request) -> Response:
    _authenticated_principal(request)
    revoke_session(request.cookies.get(SESSION_COOKIE_NAME, ""))
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    principal = _authenticated_principal(request)
    return {
        "principal": principal.public_dict(),
        "csrf_token": principal.csrf_token,
        "expires_at": principal.expires_at,
    }
