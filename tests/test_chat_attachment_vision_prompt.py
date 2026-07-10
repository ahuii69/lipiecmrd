"""Vision attachment prompt block — honest status when provider missing."""

from __future__ import annotations

import base64
import io

import pytest


def test_image_attachment_prompt_honest_when_vision_disabled(monkeypatch, tmp_path):
    """When CHAT_VISION_ENABLED=0, prompt must say VISION_PROVIDER_MISSING — not generic 'no access'."""
    import aihub.chat_file_service as cfs
    from aihub.chat_file_service import (
        build_attachment_prompt_block,
        save_multipart_upload,
    )

    monkeypatch.setattr(cfs, "DATA_DIR", tmp_path)
    monkeypatch.setattr("aihub.config.CHAT_VISION_ENABLED", False)

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

    block, meta = build_attachment_prompt_block(
        user_id="uv", session_id="sv", file_ids=[fid]
    )
    low = block.lower()
    assert "vision_provider_missing" in low
    assert "nie mamy dostępu do jego wizualnej treści" not in low
    assert "włączonej wizji" not in low
    assert meta["attachments_usable_count"] == 0
    assert meta["files"][0]["ok"] is False


def test_image_attachment_prompt_ok_after_vision_enrichment(monkeypatch, tmp_path):
    """When vision enriches the row, attachment block contains the description."""
    import aihub.chat_attachment_vision as vis
    import aihub.chat_file_service as cfs
    from aihub.chat_file_service import (
        build_attachment_prompt_block,
        save_multipart_upload,
    )

    monkeypatch.setattr(cfs, "DATA_DIR", tmp_path)
    monkeypatch.setattr(vis, "CHAT_VISION_ENABLED", True)

    async def _fake(path: str):
        return "Na obrazie widać niebieskie niebo.", None

    monkeypatch.setattr(vis, "describe_image_file", _fake)

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    res = save_multipart_upload(
        user_id="uv2",
        session_id="sv2",
        filename="sky.png",
        content_type="image/png",
        file_obj=io.BytesIO(png),
    )
    fid = res["file_id"]

    import asyncio

    asyncio.run(
        vis.enrich_image_attachments_for_turn(
            user_id="uv2", session_id="sv2", file_ids=[fid]
        )
    )

    block, meta = build_attachment_prompt_block(
        user_id="uv2", session_id="sv2", file_ids=[fid]
    )
    assert "niebieskie niebo" in block
    assert meta["attachments_usable_count"] == 1
    assert "nie mamy dostępu" not in block.lower()
