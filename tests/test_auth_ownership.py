"""Auth, signed principal, and ownership enforcement tests."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from aihub.local_auth import create_account, issue_session
from aihub.signed_principal import sign_principal_context


pytestmark = pytest.mark.no_auth_injection


@pytest.fixture
def auth_client(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_BFF_PRINCIPAL_SECRET", "test-principal-secret-value-123456")
    monkeypatch.setenv("AIHUB_AUTH_REQUIRED", "1")
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    with TestClient(main.app) as client:
        yield client


def _signed_headers(
    *,
    user_id: str,
    method: str,
    path: str,
    roles: list[str] | None = None,
    timestamp: float | None = None,
) -> dict[str, str]:
    header = sign_principal_context(
        principal_id=user_id,
        user_id=user_id,
        tenant_id=user_id,
        roles=roles or ["user"],
        session_id="sess-test",
        method=method,
        path=path,
        request_id="req-test",
        nonce="nonce-test",
        timestamp=timestamp or time.time(),
    )
    return {"x-aihub-principal": header}


def test_anon_user_scoped_memory_summary_is_401(auth_client):
    response = auth_client.get("/memory/v2/summary/test-user")
    assert response.status_code == 401


def test_service_proxy_token_without_principal_is_401(auth_client, monkeypatch):
    monkeypatch.setenv("AIHUB_PROXY_TOKEN", "proxy-only-token")
    response = auth_client.get(
        "/memory/v2/summary/test-user",
        headers={"x-aihub-proxy-token": "proxy-only-token"},
    )
    assert response.status_code == 401


def test_signed_principal_allows_own_summary(auth_client):
    user_id = "auth_user_a"
    create_account(username="auth_user_a", password="secure-password-1", account_id=user_id)
    headers = _signed_headers(user_id=user_id, method="GET", path=f"/memory/v2/summary/{user_id}")
    response = auth_client.get(f"/memory/v2/summary/{user_id}", headers=headers)
    assert response.status_code == 200


def test_signed_principal_forbids_cross_user_summary(auth_client):
    user_a = "auth_user_cross_a"
    user_b = "auth_user_cross_b"
    create_account(username="auth_user_cross_a", password="secure-password-1", account_id=user_a)
    create_account(username="auth_user_cross_b", password="secure-password-2", account_id=user_b)
    headers = _signed_headers(user_id=user_a, method="GET", path=f"/memory/v2/summary/{user_b}")
    response = auth_client.get(f"/memory/v2/summary/{user_b}", headers=headers)
    assert response.status_code == 403


def test_expired_signed_principal_is_401(auth_client):
    user_id = "auth_user_expired"
    create_account(username="auth_user_expired", password="secure-password-1", account_id=user_id)
    headers = _signed_headers(
        user_id=user_id,
        method="GET",
        path=f"/memory/v2/summary/{user_id}",
        timestamp=time.time() - 3600,
    )
    response = auth_client.get(f"/memory/v2/summary/{user_id}", headers=headers)
    assert response.status_code == 401


def test_changed_path_signature_is_401(auth_client):
    user_id = "auth_user_path"
    create_account(username="auth_user_path", password="secure-password-1", account_id=user_id)
    headers = _signed_headers(
        user_id=user_id,
        method="GET",
        path="/memory/v2/summary/other-user",
    )
    response = auth_client.get(f"/memory/v2/summary/{user_id}", headers=headers)
    assert response.status_code == 401


def test_session_cookie_allows_own_psyche(auth_client):
    user_id = "auth_user_cookie"
    account = create_account(
        username="auth_user_cookie",
        password="secure-password-1",
        account_id=user_id,
    )
    issued = issue_session(account)
    response = auth_client.get(
        f"/psyche/{user_id}",
        cookies={"aihub_session": issued.token},
    )
    assert response.status_code == 200


def test_public_ping_stays_public(auth_client):
    response = auth_client.get("/system/ping")
    assert response.status_code == 200
