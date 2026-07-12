"""HTTP endpoints for local account sessions."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from aihub.local_auth import (
    SESSION_COOKIE_NAME,
    Principal,
    authenticate,
    cookie_secure,
    issue_session,
    revoke_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def _authenticated_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


@router.post("/login")
def login(body: LoginRequest) -> Response:
    account = authenticate(body.username, body.password)
    if account is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    issued = issue_session(account)
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
