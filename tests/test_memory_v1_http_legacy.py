"""Legacy memory v1 HTTP: POST /memory/add and /memory/search on main:app."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_memory_v1_http_surfaces_are_marked_legacy(monkeypatch):
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        add = client.post(
            "/memory/add",
            json={
                "user_id": "mem_v1_http_user",
                "user_msg": "hello",
                "assistant_msg": "hi",
                "intent": "chat",
                "meta": {},
            },
        )
        assert add.status_code == 200
        assert add.headers.get("X-AIHub-Legacy-Memory-V1") == "true"
        canon = add.headers.get("X-AIHub-Canonical-Memory-Surface")
        assert canon == "memory-v2"
        role = add.headers.get("X-AIHub-Endpoint-Role")
        assert role == "legacy-memory-v1-http"
        assert "/memory/v2/item" in (add.headers.get("Link") or "")

        sr = client.post(
            "/memory/search",
            json={
                "user_id": "mem_v1_http_user",
                "query": "hello",
                "limit": 5,
            },
        )
        assert sr.status_code == 200
        assert sr.headers.get("X-AIHub-Legacy-Memory-V1") == "true"
        assert "/memory/v2/search" in (sr.headers.get("Link") or "")


def test_memory_v1_http_returns_410_when_disabled(monkeypatch):
    from aihub import main

    monkeypatch.setenv("AIHUB_DISABLE_LEGACY_MEMORY_V1_HTTP", "1")
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        add = client.post(
            "/memory/add",
            json={
                "user_id": "mem_v1_off_user",
                "user_msg": "a",
                "assistant_msg": "b",
                "intent": "chat",
                "meta": {},
            },
        )
        assert add.status_code == 410
        d = add.json().get("detail")
        assert isinstance(d, dict)
        assert d.get("canonical_memory_surface") == "memory-v2"

        sr = client.post(
            "/memory/search",
            json={"user_id": "mem_v1_off_user", "query": "x", "limit": 3},
        )
        assert sr.status_code == 410
