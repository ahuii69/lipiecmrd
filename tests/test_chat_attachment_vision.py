"""Vision: wzbogacenie wiersza image → ok + tekst (mock HTTP)."""

from __future__ import annotations

import io

import pytest

from aihub.chat_file_service import save_multipart_upload


@pytest.mark.asyncio
async def test_enrich_image_writes_extracted_text(monkeypatch, tmp_path):
    import aihub.chat_attachment_vision as vis
    import aihub.chat_file_service as cfs

    # 06.07 repair sprint: isolate real disk writes (see test_chat_file_upload.py for rationale).
    monkeypatch.setattr(cfs, "DATA_DIR", tmp_path)

    monkeypatch.setattr(vis, "CHAT_VISION_ENABLED", True)
    monkeypatch.setattr(vis, "CHAT_VISION_API_KEY", "k")
    monkeypatch.setattr(vis, "CHAT_VISION_API_URL", "https://example.com/v1/openai")

    async def _fake_describe(path: str):
        return "Na obrazie jest kwadrat.", None

    monkeypatch.setattr(vis, "describe_image_file", _fake_describe)

    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    res = save_multipart_upload(
        user_id="uv",
        session_id="sv",
        filename="z.png",
        content_type="image/png",
        file_obj=io.BytesIO(png),
    )
    assert res["ok"] is True
    fid = res["file_id"]

    await vis.enrich_image_attachments_for_turn(
        user_id="uv",
        session_id="sv",
        file_ids=[fid],
    )

    from aihub.chat_file_service import fetch_files_for_ids

    rows = fetch_files_for_ids(user_id="uv", session_id="sv", file_ids=[fid])
    assert len(rows) == 1
    assert rows[0]["extract_status"] == "ok"
    assert "kwadrat" in (rows[0]["extracted_text"] or "")
