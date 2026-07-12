"""Regression check: /agent/* contracts still work after chat integration."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aihub.auth_patch import HUB_KEY_ENV_NAMES
from aihub.chat_contracts import ChatTurnResult


def _isolate_hub_auth_env(monkeypatch) -> None:
    for name in HUB_KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_agent_run_contract_unchanged(monkeypatch):
    from aihub import main

    _isolate_hub_auth_env(monkeypatch)
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        resp = client.post(
            "/agent/run",
            json={
                "user_id": "reg_agent",
                "text": "test run",
                "dry_run": True,
                "max_steps": 2,
                "timeout_seconds": 5,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    for key in [
        "ok",
        "mode",
        "strategy",
        "strategy_reason",
        "planning_used",
        "reasoning_used",
        "execution_summary",
        "trace",
        "errors",
        "reflection",
    ]:
        assert key in body


def test_main_module_uses_package_qualified_uvicorn_target():
    main_path = (Path(__file__).resolve().parents[1] / "aihub" / "main.py").resolve()
    source = main_path.read_text(encoding="utf-8")
    assert re.search(r'uvicorn\.run\(\s*"aihub\.main:app"', source, re.DOTALL)


@pytest.mark.no_auth_injection
def test_auth_missing_or_invalid_key_returns_401_json(monkeypatch):
    from aihub import main

    _isolate_hub_auth_env(monkeypatch)
    monkeypatch.setenv("API_KEY", "expected-key")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        missing = client.get("/system/health/auth_user")
        assert missing.status_code == 401
        assert missing.json()["detail"] == "authentication required"

        api_key_only = client.get(
            "/system/health/auth_user", headers={"x-api-key": "expected-key"}
        )
        assert api_key_only.status_code == 401
        assert api_key_only.json()["detail"] == "authentication required"


@pytest.mark.no_auth_injection
def test_auth_accepts_aihub_api_key_alias_without_api_key(monkeypatch):
    """Service API key alone no longer grants user-scoped access."""
    from aihub import main

    _isolate_hub_auth_env(monkeypatch)
    monkeypatch.setenv("AIHUB_API_KEY", "alias-secret")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        bad = client.get("/system/health/auth_user")
        assert bad.status_code == 401
        alias_only = client.get(
            "/system/health/auth_user", headers={"x-api-key": "alias-secret"}
        )
        assert alias_only.status_code == 401


@pytest.mark.no_auth_injection
def test_chat_turn_auth_401_without_key_200_with_key(monkeypatch):
    import aihub.chat_api as chat_api
    from aihub import main

    _isolate_hub_auth_env(monkeypatch)
    monkeypatch.setenv("API_KEY", "hub-turn-secret")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    class _FakeRuntime:
        async def run_turn(self, _payload):
            return ChatTurnResult(
                ok=True,
                response_text="ok",
                model="m",
                provider="p",
                selected_mode="chat",
            )

    monkeypatch.setattr(chat_api, "get_chat_runtime", lambda: _FakeRuntime())

    with TestClient(main.app) as client:
        missing = client.post(
            "/chat/turn",
            json={
                "user_id": "u",
                "session_id": "s",
                "message": "hi",
                "mode": "chat",
            },
        )
        assert missing.status_code == 401
        assert missing.json()["detail"] == "authentication required"

        api_key_only = client.post(
            "/chat/turn",
            json={
                "user_id": "u",
                "session_id": "s",
                "message": "hi",
                "mode": "chat",
            },
            headers={"x-api-key": "hub-turn-secret"},
        )
        assert api_key_only.status_code == 401

        bearer_only = client.post(
            "/chat/turn",
            json={
                "user_id": "u",
                "session_id": "s",
                "message": "hi",
                "mode": "chat",
            },
            headers={"authorization": "Bearer hub-turn-secret"},
        )
        assert bearer_only.status_code == 401


@pytest.mark.no_auth_injection
def test_chat_turn_accepts_x_aihub_proxy_token_without_x_api_key(monkeypatch):
    """Proxy token without signed principal must not unlock user-scoped routes."""
    import aihub.chat_api as chat_api
    from aihub import main

    _isolate_hub_auth_env(monkeypatch)
    monkeypatch.setenv("API_KEY", "shared-hub-secret")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    class _FakeRuntime:
        async def run_turn(self, _payload):
            return ChatTurnResult(
                ok=True,
                response_text="proxy-ok",
                model="m",
                provider="p",
                selected_mode="chat",
            )

    monkeypatch.setattr(chat_api, "get_chat_runtime", lambda: _FakeRuntime())

    with TestClient(main.app) as client:
        bad = client.post(
            "/chat/turn",
            json={
                "user_id": "u",
                "session_id": "s",
                "message": "hi",
                "mode": "chat",
            },
            headers={"x-aihub-proxy-token": "wrong"},
        )
        assert bad.status_code == 401

        proxy_only = client.post(
            "/chat/turn",
            json={
                "user_id": "u",
                "session_id": "s",
                "message": "hi",
                "mode": "chat",
            },
            headers={"x-aihub-proxy-token": "shared-hub-secret"},
        )
        assert proxy_only.status_code == 401


@pytest.mark.no_auth_injection
def test_chat_turn_proxy_token_prefers_aihub_proxy_token_env(monkeypatch):
    """Even valid proxy token cannot substitute session/principal on user routes."""
    import aihub.chat_api as chat_api
    from aihub import main

    _isolate_hub_auth_env(monkeypatch)
    monkeypatch.setenv("API_KEY", "api-key-value")
    monkeypatch.setenv("AIHUB_PROXY_TOKEN", "bff-only-secret")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    class _FakeRuntime:
        async def run_turn(self, _payload):
            return ChatTurnResult(
                ok=True,
                response_text="bff",
                model="m",
                provider="p",
                selected_mode="chat",
            )

    monkeypatch.setattr(chat_api, "get_chat_runtime", lambda: _FakeRuntime())

    with TestClient(main.app) as client:
        api_key_as_proxy = client.post(
            "/chat/turn",
            json={
                "user_id": "u",
                "session_id": "s",
                "message": "hi",
                "mode": "chat",
            },
            headers={"x-aihub-proxy-token": "api-key-value"},
        )
        assert api_key_as_proxy.status_code == 401

        proxy_token = client.post(
            "/chat/turn",
            json={
                "user_id": "u",
                "session_id": "s",
                "message": "hi",
                "mode": "chat",
            },
            headers={"x-aihub-proxy-token": "bff-only-secret"},
        )
        assert proxy_token.status_code == 401
