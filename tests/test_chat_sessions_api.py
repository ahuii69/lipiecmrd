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
        row = next(x for x in body["sessions"] if x["id"] == "cs1")
        assert row.get("archived") is False

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


def test_chat_sessions_archive_unarchive(monkeypatch):
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        client.patch(
            "/chat/session/rename",
            json={
                "user_id": "cu_arch",
                "session_id": "s_arch",
                "title": "To archive",
            },
        )
        ar = client.post(
            "/chat/session/archive",
            json={"user_id": "cu_arch", "session_id": "s_arch"},
        )
        assert ar.status_code == 200
        assert ar.json()["archived"] is True

        all_sessions = client.get(
            "/chat/sessions", params={"user_id": "cu_arch"}
        ).json()["sessions"]
        hit = next(x for x in all_sessions if x["id"] == "s_arch")
        assert hit["archived"] is True
        assert "archived_at" in hit

        active_only = client.get(
            "/chat/sessions",
            params={"user_id": "cu_arch", "include_archived": "false"},
        ).json()["sessions"]
        assert not any(x["id"] == "s_arch" for x in active_only)

        un = client.post(
            "/chat/session/unarchive",
            json={"user_id": "cu_arch", "session_id": "s_arch"},
        )
        assert un.status_code == 200
        assert un.json()["archived"] is False

        restored = client.get(
            "/chat/sessions", params={"user_id": "cu_arch"}
        ).json()["sessions"]
        hit2 = next(x for x in restored if x["id"] == "s_arch")
        assert hit2["archived"] is False


def test_delete_one_session_does_not_wipe_other_archives(monkeypatch):
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        uid = "cu_del_arch"
        for sid, title in (("keep_arch", "Keep"), ("drop_me", "Drop"), ("keep_active", "Active")):
            client.patch(
                "/chat/session/rename",
                json={"user_id": uid, "session_id": sid, "title": title},
            )
        client.post(
            "/chat/session/archive",
            json={"user_id": uid, "session_id": "keep_arch"},
        )
        client.post(
            "/chat/session/archive",
            json={"user_id": uid, "session_id": "drop_me"},
        )

        de = client.request(
            "DELETE",
            "/chat/session",
            content=json.dumps({"user_id": uid, "session_id": "drop_me"}).encode(),
            headers={"content-type": "application/json"},
        )
        assert de.status_code == 200

        body = client.get("/chat/sessions", params={"user_id": uid}).json()
        by_id = {x["id"]: x for x in body["sessions"]}
        assert "drop_me" not in by_id
        assert by_id["keep_arch"]["archived"] is True
        assert by_id["keep_active"]["archived"] is False


def test_list_sessions_has_no_silent_pagination_truncation(monkeypatch):
    """API returns the full list with archived flags — no page/limit that drops archives."""
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        uid = "cu_many"
        for i in range(12):
            sid = f"s_{i}"
            client.patch(
                "/chat/session/rename",
                json={"user_id": uid, "session_id": sid, "title": f"T{i}"},
            )
            if i % 3 == 0:
                client.post(
                    "/chat/session/archive",
                    json={"user_id": uid, "session_id": sid},
                )
        all_rows = client.get("/chat/sessions", params={"user_id": uid}).json()[
            "sessions"
        ]
        assert len(all_rows) == 12
        archived = [r for r in all_rows if r["archived"]]
        assert len(archived) == 4
        active = client.get(
            "/chat/sessions",
            params={"user_id": uid, "include_archived": "false"},
        ).json()["sessions"]
        assert len(active) == 8
        assert all(not r["archived"] for r in active)


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
