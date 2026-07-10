"""Upload + attachment context for user chat (.txt / .md / .pdf / obrazy)."""

from __future__ import annotations

import base64
import io

import pytest

# 1×1 PNG (ważny nagłówek CRC)
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
from fastapi.testclient import TestClient

from aihub.chat_contracts import ChatTurnInput, ChatTurnResult
from aihub.chat_file_service import MAX_UPLOAD_BYTES, build_attachment_prompt_block


@pytest.fixture(autouse=True)
def _isolated_uploads_dir(monkeypatch, tmp_path):
    """06.07 repair sprint: isolate real disk writes to a per-test tmp dir.

    Without this, ``save_multipart_upload`` wrote real files into the shared
    ``data/uploads/`` tree. Repeated runs with the same fixed test fixture bytes then produced
    byte-identical leftovers across runs, which made
    ``tests/test_ops_readiness_and_release_audit.py::test_release_audit_core_checks_are_green``
    (exact-duplicate-file check) fail nondeterministically depending on prior test runs.
    """
    import aihub.chat_file_service as cfs

    monkeypatch.setattr(cfs, "DATA_DIR", tmp_path)


def test_upload_txt_and_preview(monkeypatch):
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        files = {"file": ("note.txt", b"Witaj z pliku \xc4\x99", "text/plain")}
        data = {"user_id": "u1", "session_id": "sess_a"}
        r = client.post("/chat/upload", data=data, files=files)
        assert r.status_code == 200
        body = r.json()
        assert body["file_id"].startswith("cf_")
        assert body["filename"] == "note.txt"
        assert body["status"] == "ok"
        assert "Witaj" in (body.get("extracted_text_preview") or "")


def test_upload_png_stored_as_image(monkeypatch):
    from aihub import main
    from aihub.chat_file_service import build_attachment_prompt_block

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        files = {"file": ("x.png", _PNG_1X1, "image/png")}
        r = client.post(
            "/chat/upload",
            data={"user_id": "u_img", "session_id": "s_img"},
            files=files,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "image"
        assert body["file_id"].startswith("cf_")

    fid = body["file_id"]
    block, meta = build_attachment_prompt_block(
        user_id="u_img",
        session_id="s_img",
        file_ids=[fid],
    )
    assert "Obraz:" in block or "obraz" in block.lower()
    assert meta.get("primary_file_id") == fid
    assert meta.get("attachments_usable_count") == 0


def test_attachment_text_and_error_in_block(monkeypatch):
    import io

    from aihub import main
    from aihub.chat_file_service import (
        build_attachment_prompt_block,
        save_multipart_upload,
    )

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    r1 = save_multipart_upload(
        user_id="um",
        session_id="sm",
        filename="a.txt",
        content_type="text/plain",
        file_obj=io.BytesIO("treść a".encode("utf-8")),
    )
    r2 = save_multipart_upload(
        user_id="um",
        session_id="sm",
        filename="b.png",
        content_type="image/png",
        file_obj=io.BytesIO(_PNG_1X1),
    )
    assert r1["ok"] and r2["ok"]
    block, meta = build_attachment_prompt_block(
        user_id="um",
        session_id="sm",
        file_ids=[r1["file_id"], r2["file_id"]],
    )
    assert "treść a" in block
    assert "OSTATNI" in block
    assert meta.get("primary_file_id") == r2["file_id"]


def test_upload_rejects_wrong_extension(monkeypatch):
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        files = {"file": ("x.exe", b"abc", "application/octet-stream")}
        r = client.post(
            "/chat/upload",
            data={"user_id": "u1", "session_id": "s1"},
            files=files,
        )
        assert r.status_code == 400
        err = r.json()["detail"]
        assert isinstance(err, dict)
        assert err.get("error") == "unsupported_type"


def test_upload_rejects_too_large(monkeypatch):
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    big = b"x" * (MAX_UPLOAD_BYTES + 1)
    with TestClient(main.app) as client:
        files = {"file": ("big.txt", big, "text/plain")}
        r = client.post(
            "/chat/upload",
            data={"user_id": "u1", "session_id": "s1"},
            files=files,
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "file_too_large"


def test_build_attachment_prompt_after_upload(monkeypatch):
    from aihub import main
    from aihub.chat_file_service import save_multipart_upload

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    res = save_multipart_upload(
        user_id="ux",
        session_id="sx",
        filename="doc.md",
        content_type="text/markdown",
        file_obj=io.BytesIO(b"# Tytul\n\nTresc dokumentu."),
    )
    assert res["ok"] is True
    fid = res["file_id"]
    block, meta = build_attachment_prompt_block(
        user_id="ux",
        session_id="sx",
        file_ids=[fid],
    )
    assert "Tresc dokumentu" in block
    assert meta.get("total_context_chars", 0) > 0


def test_chat_turn_with_attached_file_ids(monkeypatch):
    import aihub.chat_api as chat_api
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    seen: list[ChatTurnInput] = []

    class _Cap:
        async def run_turn(self, payload: ChatTurnInput) -> ChatTurnResult:
            seen.append(payload)
            return ChatTurnResult(
                ok=True,
                response_text="ok",
                model="m",
                provider="p",
                selected_mode="chat",
                attachments_summary="Plik: doc.txt",
            )

    monkeypatch.setattr(chat_api, "get_chat_runtime", lambda: _Cap())

    with TestClient(main.app) as client:
        r = client.post(
            "/chat/turn",
            json={
                "user_id": "u1",
                "session_id": "s1",
                "message": "streść",
                "mode": "chat",
                "attached_file_ids": ["cf_test123"],
            },
        )
        assert r.status_code == 200
        assert len(seen) == 1
        assert seen[0].attached_file_ids == ["cf_test123"]
        assert r.json().get("attachments_summary") == "Plik: doc.txt"


def test_derive_context_chips_from_trace():
    from aihub.chat_context_compose import derive_context_chips_from_trace

    chips = derive_context_chips_from_trace(
        {
            "attached_files": {
                "files": [
                    {"ok": True, "kind": "text"},
                    {"ok": True, "kind": "image"},
                ]
            },
            "memory_lookup_happened": True,
            "web_grounding_in_prompt": True,
        }
    )
    assert "attachment-used" in chips
    assert "image-used" in chips
    assert "memory-used" in chips
    assert "web-used" in chips

    chips2 = derive_context_chips_from_trace({}, input_via_stt=True)
    assert "stt-input" in chips2


def test_chat_turn_rejects_too_many_attached_ids(monkeypatch):
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    with TestClient(main.app) as client:
        r = client.post(
            "/chat/turn",
            json={
                "user_id": "u1",
                "session_id": "s1",
                "message": "hi",
                "mode": "chat",
                "attached_file_ids": [f"id{i}" for i in range(6)],
            },
        )
        assert r.status_code == 422
