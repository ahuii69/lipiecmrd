"""STT: wyłączenie, self-hosted (mock), openai_compatible (mock), endpoint /chat/stt."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_stt_disabled_returns_clear_error(monkeypatch):
    import aihub.chat_stt_service as stt_mod
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    monkeypatch.setattr(stt_mod, "CHAT_STT_ENABLED", False)

    with TestClient(main.app) as client:
        files = {"file": ("a.webm", b"fake", "audio/webm")}
        r = client.post("/chat/stt", files=files)
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is False
        assert body.get("code") == "stt_disabled"


@pytest.mark.asyncio
async def test_transcribe_self_hosted_path(monkeypatch):
    import aihub.chat_stt_service as stt_mod

    monkeypatch.setattr(stt_mod, "CHAT_STT_ENABLED", True)
    monkeypatch.setattr(stt_mod, "CHAT_STT_BACKEND", "self_hosted_whisper")

    async def _fake(*, data: bytes, filename: str):
        return {"ok": True, "text": "  Witaj z whisper  "}

    monkeypatch.setattr(stt_mod, "_transcribe_self_hosted", _fake)

    out = await stt_mod.transcribe_audio_bytes(data=b"x", filename="a.webm")
    assert out["ok"] is True
    assert out["text"] == "Witaj z whisper"


@pytest.mark.asyncio
async def test_transcribe_openai_compatible_mock(monkeypatch):
    import httpx

    import aihub.chat_stt_service as stt_mod

    monkeypatch.setattr(stt_mod, "CHAT_STT_ENABLED", True)
    monkeypatch.setattr(stt_mod, "CHAT_STT_BACKEND", "openai_compatible")
    monkeypatch.setattr(stt_mod, "CHAT_STT_API_KEY", "sk-test")

    class _Resp:
        status_code = 200

        def json(self):
            return {"text": "  API  "}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())

    out = await stt_mod.transcribe_audio_bytes(data=b"x", filename="x.webm")
    assert out["ok"] is True
    assert out["text"] == "API"


def test_stt_endpoint_mocked_pipeline(monkeypatch):
    import aihub.chat_api as chat_api
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    async def _pipe(*, data: bytes, filename: str):
        return {"ok": True, "text": "endpoint ok"}

    monkeypatch.setattr(chat_api, "transcribe_audio_bytes", _pipe)

    with TestClient(main.app) as client:
        files = {"file": ("a.webm", b"xyz", "audio/webm")}
        r = client.post("/chat/stt", files=files)
        assert r.status_code == 200
        assert r.json() == {"ok": True, "text": "endpoint ok"}


def test_stt_bad_backend(monkeypatch):
    import asyncio

    import aihub.chat_stt_service as stt_mod

    monkeypatch.setattr(stt_mod, "CHAT_STT_ENABLED", True)
    monkeypatch.setattr(stt_mod, "CHAT_STT_BACKEND", "nope")

    out = asyncio.run(stt_mod.transcribe_audio_bytes(data=b"a", filename="a.webm"))
    assert out["ok"] is False
    assert out.get("code") == "stt_bad_backend"
