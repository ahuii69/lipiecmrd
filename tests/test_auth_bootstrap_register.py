"""Bootstrap registration: first admin when auth_accounts is empty."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aihub.local_auth import create_account, registration_open


pytestmark = pytest.mark.no_auth_injection


@pytest.fixture
def auth_client(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_BFF_PRINCIPAL_SECRET", "test-principal-secret-value-123456")
    monkeypatch.setenv("AIHUB_AUTH_REQUIRED", "1")
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    with TestClient(main.app) as client:
        yield client


def test_registration_status_open_when_empty(auth_client):
    assert registration_open() is True
    response = auth_client.get("/auth/registration-status")
    assert response.status_code == 200
    body = response.json()
    assert body["open"] is True
    assert body["mode"] == "bootstrap_first_admin"
    assert "password" not in body
    assert "hash" not in str(body).lower()


def test_bootstrap_register_creates_admin_and_session(auth_client):
    response = auth_client.post(
        "/auth/register",
        json={"username": "first.admin", "password": "secure-password-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["principal"]["username"] == "first.admin"
    assert body["principal"]["role"] == "admin"
    assert "password" not in body
    assert "password_hash" not in body
    assert "aihub_session" in response.cookies

    me = auth_client.get("/auth/me", cookies={"aihub_session": response.cookies["aihub_session"]})
    assert me.status_code == 200
    assert me.json()["principal"]["role"] == "admin"


def test_second_register_is_closed(auth_client):
    first = auth_client.post(
        "/auth/register",
        json={"username": "only.admin", "password": "secure-password-1"},
    )
    assert first.status_code == 200

    status = auth_client.get("/auth/registration-status")
    assert status.status_code == 200
    assert status.json()["open"] is False

    second = auth_client.post(
        "/auth/register",
        json={"username": "second.user", "password": "secure-password-2"},
    )
    assert second.status_code == 403
    assert second.json()["detail"] == "registration closed"


def test_register_rejects_weak_password(auth_client):
    response = auth_client.post(
        "/auth/register",
        json={"username": "weak.user", "password": "short"},
    )
    assert response.status_code == 400
    assert "12" in response.json()["detail"]


def test_register_rejects_invalid_username(auth_client):
    response = auth_client.post(
        "/auth/register",
        json={"username": "ab", "password": "secure-password-1"},
    )
    assert response.status_code in {400, 422}


def test_login_works_after_bootstrap(auth_client):
    auth_client.post(
        "/auth/register",
        json={"username": "login.admin", "password": "secure-password-1"},
    )
    login = auth_client.post(
        "/auth/login",
        json={"username": "login.admin", "password": "secure-password-1"},
    )
    assert login.status_code == 200
    assert "aihub_session" in login.cookies


def test_register_closed_when_account_already_seeded(auth_client):
    create_account(
        username="seeded.user",
        password="secure-password-1",
        role="admin",
        account_id="seeded-user",
    )
    status = auth_client.get("/auth/registration-status")
    assert status.json()["open"] is False
    response = auth_client.post(
        "/auth/register",
        json={"username": "another.user", "password": "secure-password-2"},
    )
    assert response.status_code == 403
