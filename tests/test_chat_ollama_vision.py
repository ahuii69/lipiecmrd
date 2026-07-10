"""Vision Ollama: parsowanie odpowiedzi + mock HTTP."""

from __future__ import annotations

import base64

import pytest

from aihub.chat_attachment_vision import _ollama_extract_content


def test_ollama_extract_content_string():
    text = _ollama_extract_content(
        {"message": {"role": "assistant", "content": "  Kot na kanapie.  "}}
    )
    assert text == "Kot na kanapie."


def test_ollama_extract_content_list_parts():
    text = _ollama_extract_content(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Linia A"},
                    {"type": "text", "text": "Linia B"},
                ],
            }
        }
    )
    assert "Linia A" in text and "Linia B" in text


@pytest.mark.asyncio
async def test_describe_ollama_primary_then_fallback(monkeypatch):
    import aihub.chat_attachment_vision as vis

    monkeypatch.setattr(vis, "CHAT_VISION_OLLAMA_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(vis, "CHAT_VISION_MODEL", "primary:model")
    monkeypatch.setattr(vis, "CHAT_VISION_FALLBACK_MODEL", "fallback:model")

    calls: list[str] = []

    class _Resp:
        def __init__(self, code: int, body: dict):
            self.status_code = code
            self._body = body

        def json(self):
            return self._body

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url: str, json: dict):
            model = json["model"]
            calls.append(model)
            if model == "primary:model":
                return _Resp(404, {"error": "not found"})
            return _Resp(
                200,
                {"message": {"role": "assistant", "content": "Opis z fallback."}},
            )

    import types

    monkeypatch.setattr(
        vis,
        "httpx",
        types.SimpleNamespace(AsyncClient=lambda **kwargs: _Client()),
    )

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    b64 = base64.standard_b64encode(png).decode("ascii")
    text, err = await vis._describe_ollama(b64_image=b64)
    assert err is None
    assert "fallback" in text.lower() or "Opis" in text
    assert "primary:model" in calls
    assert "fallback:model" in calls
