"""Test-only helpers for signed principal injection into TestClient requests."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import urlparse

from aihub.local_auth import json_user_ids, path_user_id
from aihub.signed_principal import sign_principal_context

TEST_PRINCIPAL_SECRET = "test-principal-secret-value-123456"

PUBLIC_PATHS = {
    "/system/ping",
    "/ops/health",
    "/ops/ready",
    "/ops/capabilities",
    "/auth/login",
}

ADMIN_PATHS = {
    "/cockpit/schema-health",
}


def is_admin_test_path(path: str) -> bool:
    return path in ADMIN_PATHS or path.startswith("/admin/")


def ensure_test_principal_secret() -> None:
    os.environ.setdefault("AIHUB_BFF_PRINCIPAL_SECRET", TEST_PRINCIPAL_SECRET)


def is_public_test_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/openapi")


def infer_user_id(
    *,
    path: str,
    json_payload: Any | None,
    raw_content: bytes | str | None,
    query_user_id: str | None = None,
) -> str:
    uid = path_user_id(path)
    if uid:
        return uid
    parts = [segment for segment in path.split("/") if segment]
    if len(parts) >= 2 and parts[0] == "cockpit":
        candidate = parts[-1]
        if candidate not in {
            "schema-health",
            "retrieval",
            "habits",
            "relations",
            "overview",
            "consistency",
            "reflections",
            "policy",
            "simulations",
            "identity",
            "memory-v2",
            "psyche-v2",
        }:
            return candidate
    payload = json_payload
    if payload is None and raw_content:
        try:
            if isinstance(raw_content, bytes):
                text = raw_content.decode("utf-8")
            else:
                text = str(raw_content)
            payload = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
    users = json_user_ids(payload) if payload is not None else set()
    if query_user_id:
        users.add(str(query_user_id))
    if len(users) == 1:
        return next(iter(users))
    if users:
        return sorted(users)[0]
    return "default"


def signed_headers_for_request(
    *,
    method: str,
    path: str,
    user_id: str,
    roles: list[str] | None = None,
) -> dict[str, str]:
    ensure_test_principal_secret()
    header = sign_principal_context(
        principal_id=user_id,
        user_id=user_id,
        tenant_id=user_id,
        roles=roles or ["user"],
        session_id=f"test-session-{user_id}",
        method=method.upper(),
        path=path,
        request_id=f"test-req-{user_id}-{int(time.time() * 1000)}",
        nonce=f"test-nonce-{user_id}",
    )
    return {"x-aihub-principal": header}
