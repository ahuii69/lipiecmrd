from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_chat_sessions_rename_list_delete(monkeypatch):
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        ren = client.patch(
            "/chat/session/rename",
            json={
                "user_id": "cu1",
                "session_id": "cs1",
                "title": "Custom title",
            },
        )
        assert ren.status_code == 200
        assert ren.json()["title"] == "Custom title"

        lst = client.get("/chat/sessions", params={"user_id": "cu1"})
        assert lst.status_code == 200
        body = lst.json()
        ids = {x["id"]: x["title"] for x in body["sessions"]}
        assert ids.get("cs1") == "Custom title"

        de = client.request(
            "DELETE",
            "/chat/session",
            content=json.dumps(
                {"user_id": "cu1", "session_id": "cs1"},
            ).encode(),
            headers={"content-type": "application/json"},
        )
        assert de.status_code == 200
        assert de.json()["ok"] is True

        lst2 = client.get("/chat/sessions", params={"user_id": "cu1"})
        assert lst2.status_code == 200
        assert not any(x["id"] == "cs1" for x in lst2.json()["sessions"])


def test_chat_sessions_auto_title(monkeypatch):
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        r = client.post(
            "/chat/session/auto-title",
            json={
                "user_id": "cu2",
                "session_id": "cs2",
                "first_user_message": "alpha beta gamma delta epsilon zeta eta",
            },
        )
        assert r.status_code == 200
        assert r.json()["title"] == "alpha beta gamma delta epsilon zeta eta"

        r2 = client.post(
            "/chat/session/auto-title",
            json={
                "user_id": "cu2",
                "session_id": "cs3",
                "first_user_message": "",
            },
        )
        assert r2.status_code == 200
        assert r2.json()["title"] == "Nowa rozmowa"

        long_msg = " ".join(f"w{i}" for i in range(15))
        r3 = client.post(
            "/chat/session/auto-title",
            json={
                "user_id": "cu2",
                "session_id": "cs4",
                "first_user_message": long_msg,
            },
        )
        assert r3.status_code == 200
        assert len(r3.json()["title"].split()) == 10


def test_chat_sessions_are_isolated_per_user_id(monkeypatch):
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        ra = client.patch(
            "/chat/session/rename",
            json={"user_id": "iso_user_a", "session_id": "s_a", "title": "A only"},
        )
        rb = client.patch(
            "/chat/session/rename",
            json={"user_id": "iso_user_b", "session_id": "s_b", "title": "B only"},
        )
        assert ra.status_code == 200
        assert rb.status_code == 200

        la = client.get("/chat/sessions", params={"user_id": "iso_user_a"})
        lb = client.get("/chat/sessions", params={"user_id": "iso_user_b"})
        assert la.status_code == 200
        assert lb.status_code == 200
        ids_a = {x["id"] for x in la.json().get("sessions", [])}
        ids_b = {x["id"] for x in lb.json().get("sessions", [])}
        assert "s_a" in ids_a and "s_b" not in ids_a
        assert "s_b" in ids_b and "s_a" not in ids_b
