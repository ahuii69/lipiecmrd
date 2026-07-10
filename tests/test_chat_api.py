"""API tests for /chat endpoints."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from aihub.chat_contracts import ChatTurnResult


def test_chat_turn_endpoint_and_capabilities(monkeypatch):
    import aihub.chat_api as chat_api
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    class _FakeRuntime:
        async def run_turn(self, _payload):
            return ChatTurnResult(
                ok=True,
                response_text="ok",
                model="openai/gpt-oss-120b",
                provider="deepinfra",
                selected_mode="chat",
            )

    def _runtime_factory():
        return _FakeRuntime()

    monkeypatch.setattr(chat_api, "get_chat_runtime", _runtime_factory)

    with TestClient(main.app) as client:
        resp = client.post(
            "/chat/turn",
            json={
                "user_id": "api_user",
                "session_id": "s1",
                "message": "hej",
                "mode": "chat",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["response_text"] == "ok"
        assert body["provider"] == "deepinfra"

        caps = client.get("/chat/capabilities?mode=readonly")
        assert caps.status_code == 200
        caps_body = caps.json()
        assert caps_body["ok"] is True
        assert caps_body["mode"] == "readonly"
        assert isinstance(caps_body["capabilities"], list)


def test_chat_turn_stream_sse_deltas_and_done(monkeypatch):
    import aihub.chat_api as chat_api
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    long_text = "abcdefghij" * 5  # 50 chars → multiple 44-char chunks

    class _FakeRuntime:
        async def run_turn(self, _payload):
            return ChatTurnResult(
                ok=True,
                response_text=long_text,
                model="m",
                provider="p",
                selected_mode="chat",
            )

    monkeypatch.setattr(chat_api, "get_chat_runtime", lambda: _FakeRuntime())

    with TestClient(main.app) as client:
        with client.stream(
            "POST",
            "/chat/turn?stream=true&include_turn_result=true",
            json={
                "user_id": "u",
                "session_id": "s",
                "message": "hi",
                "mode": "chat",
            },
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in (resp.headers.get("content-type") or "")
            raw = b"".join(resp.iter_bytes())
    text = raw.decode("utf-8")
    assert "data:" in text
    assert '"type": "delta"' in text
    assert '"type": "done"' in text
    assert '"result"' in text
    # Reassembled content matches full response
    deltas: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "delta":
            deltas.append(obj.get("content") or "")
        if obj.get("type") == "done":
            assert obj.get("result", {}).get("response_text") == long_text
    assert "".join(deltas) == long_text


def test_chat_turn_stream_includes_status_from_runtime(monkeypatch):
    import aihub.chat_api as chat_api
    from aihub import main
    from aihub.chat_stream_session import emit_status

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    class _FakeRuntime:
        async def run_turn(self, _payload):
            await emit_status("thinking", label_pl="Test")
            return ChatTurnResult(
                ok=True,
                response_text="x",
                model="m",
                provider="p",
                selected_mode="chat",
            )

    monkeypatch.setattr(chat_api, "get_chat_runtime", lambda: _FakeRuntime())

    with TestClient(main.app) as client:
        with client.stream(
            "POST",
            "/chat/turn?stream=true",
            json={
                "user_id": "u",
                "session_id": "s",
                "message": "hi",
                "mode": "chat",
            },
        ) as resp:
            raw = b"".join(resp.iter_bytes())
    assert b'"type": "status"' in raw
    assert b'"stage": "thinking"' in raw


def test_legacy_turn_endpoint_is_explicitly_deprecated(monkeypatch):
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        resp = client.post(
            "/turn",
            json={
                "user_id": "legacy_turn_user",
                "role": "user",
                "content": "raw stm event",
                "meta": {},
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body.get("id"), str) and body["id"]
    assert resp.headers.get("Deprecation") == "true"
    assert resp.headers.get("X-AIHub-Endpoint-Role") == "legacy-stm-write"
    assert resp.headers.get("X-AIHub-Legacy-Stm-Write") == "true"
    assert resp.headers.get("X-AIHub-Canonical-Chat-Path") == "/chat/turn"
    assert "</chat/turn>" in resp.headers.get("Link", "")


def test_legacy_turn_returns_410_when_disabled(monkeypatch):
    from aihub import main

    monkeypatch.setenv("AIHUB_DISABLE_LEGACY_STM_TURN", "1")
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        resp = client.post(
            "/turn",
            json={
                "user_id": "legacy_turn_disabled_user",
                "role": "user",
                "content": "raw stm event",
                "meta": {},
            },
        )

    assert resp.status_code == 410
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("canonical_chat_path") == "/chat/turn"
