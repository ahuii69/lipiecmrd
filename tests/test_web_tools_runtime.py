"""Runtime confidence tests for web_tools and web fetch integration."""

from __future__ import annotations

import asyncio
import ssl
from typing import Any, Dict

import httpx
import pytest
from fastapi.testclient import TestClient

from aihub.db import fetch_all
from aihub.psyche_engine import ensure_user


class _FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        status_code: int,
        headers: Dict[str, str],
        content: bytes,
        raise_exc: Exception | None = None,
    ):
        self.url = url
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self._raise_exc = raise_exc

    def raise_for_status(self) -> None:
        if self._raise_exc is not None:
            raise self._raise_exc


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, _url: str):
        if self._exc is not None:
            raise self._exc
        return self._response


def test_fetch_url_success_truncates_and_logs_event(monkeypatch):
    user_id = "web_tools_success"
    ensure_user(user_id)

    import aihub.web_tools as wt

    monkeypatch.setattr(wt, "HTTP_MAX_BYTES", 5)

    response = _FakeResponse(
        url="https://example.com/final",
        status_code=200,
        headers={"content-type": "text/plain"},
        content=b"abcdef",
    )

    monkeypatch.setattr(
        wt.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeAsyncClient(response=response),
    )

    out = asyncio.get_event_loop().run_until_complete(
        wt.fetch_url(user_id, "https://example.com")
    )

    assert out["ok"] is True
    assert out["status"] == 200
    assert out["bytes"] == 5
    assert out["text"] == "abcde"

    events = fetch_all(
        "SELECT type FROM event_log WHERE user_id=? AND type='web.fetch'",
        (user_id,),
    )
    assert len(events) >= 1


def test_fetch_url_rejects_invalid_scheme():
    import aihub.web_tools as wt

    with pytest.raises(ValueError):
        asyncio.get_event_loop().run_until_complete(
            wt.fetch_url("web_tools_bad_scheme", "ftp://example.com")
        )


def test_fetch_url_timeout_logs_error_event(monkeypatch):
    user_id = "web_tools_timeout"
    ensure_user(user_id)

    import aihub.web_tools as wt

    timeout_exc = httpx.ReadTimeout("timeout", request=httpx.Request("GET", "https://x"))
    monkeypatch.setattr(
        wt.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeAsyncClient(exc=timeout_exc),
    )

    with pytest.raises(httpx.ReadTimeout):
        asyncio.get_event_loop().run_until_complete(
            wt.fetch_url(user_id, "https://example.com")
        )

    events = fetch_all(
        "SELECT type FROM event_log WHERE user_id=? AND type='web.fetch.error'",
        (user_id,),
    )
    assert len(events) >= 1


def test_fetch_url_uses_explicit_verify_and_trust_env(monkeypatch):
    user_id = "web_tools_client_kwargs"
    ensure_user(user_id)

    import aihub.web_tools as wt

    captured: Dict[str, Any] = {}

    class _CapturedAsyncClient(_FakeAsyncClient):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            response = _FakeResponse(
                url="https://example.com/final",
                status_code=200,
                headers={"content-type": "text/plain"},
                content=b"hello",
            )
            super().__init__(response=response)

    monkeypatch.setattr(wt.httpx, "AsyncClient", _CapturedAsyncClient)

    out = asyncio.get_event_loop().run_until_complete(
        wt.fetch_url(user_id, "https://example.com")
    )

    assert out["ok"] is True
    assert captured["trust_env"] is False
    assert isinstance(captured["verify"], ssl.SSLContext)


def test_agent_engine_web_fetch_uses_fetch_url_ok_for_fact_write(monkeypatch):
    user_id = "web_tools_agent_engine_integration"
    ensure_user(user_id)

    from aihub.agent_engine import execute_task

    async def _fake_fetch(_uid: str, _url: str) -> Dict[str, Any]:
        return {
            "ok": True,
            "url": "https://example.com",
            "status": 200,
            "bytes": 42,
            "text": "sample body",
        }

    monkeypatch.setattr("aihub.agent_engine.fetch_url", _fake_fetch)

    asyncio.get_event_loop().run_until_complete(
        execute_task(user_id, {"type": "web.fetch", "payload": {"url": "https://example.com"}})
    )

    rows = fetch_all(
        "SELECT content FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
        (user_id,),
    )
    assert any("Web fetch https://example.com" in (r["content"] or "") for r in rows)


def test_web_fetch_endpoint_success_and_failure(monkeypatch):
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    async def _ok_fetch(_uid: str, _url: str):
        return {
            "ok": True,
            "url": "https://example.com",
            "status": 200,
            "bytes": 2,
            "text": "ok",
            "headers": {},
        }

    async def _fail_fetch(_uid: str, _url: str):
        raise ValueError("bad url")

    with TestClient(main.app) as client:
        monkeypatch.setattr(main, "fetch_url", _ok_fetch)
        ok_resp = client.post("/web/fetch?user_id=web_endpoint_user", json={"url": "https://example.com"})
        assert ok_resp.status_code == 200
        assert ok_resp.json()["ok"] is True

        monkeypatch.setattr(main, "fetch_url", _fail_fetch)
        fail_resp = client.post("/web/fetch?user_id=web_endpoint_user", json={"url": "https://example.com"})
        assert fail_resp.status_code == 400
