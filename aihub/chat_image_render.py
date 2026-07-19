"""Real text-to-image render via DeepInfra OpenAI-compatible Images API."""

from __future__ import annotations

import base64
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx

from aihub.chat_file_service import insert_upload_row, uploads_root
from aihub.config import (
    CHAT_IMAGE_GEN_BASE_URL,
    CHAT_IMAGE_GEN_ENABLED,
    CHAT_IMAGE_GEN_MODEL,
    CHAT_IMAGE_GEN_SIZE,
    CHAT_IMAGE_GEN_TIMEOUT_S,
    LLM_API_KEY,
)

logger = logging.getLogger(__name__)


def image_gen_configured() -> bool:
    return bool(CHAT_IMAGE_GEN_ENABLED and (LLM_API_KEY or "").strip())


async def render_image_png(
    *,
    prompt: str,
    user_id: str,
    session_id: str,
    negative_prompt: str = "",
) -> dict[str, Any]:
    """Call DeepInfra images API, store PNG as chat upload, return file metadata.

    Returns keys: ok, file_id?, filename?, content_type?, model?, error?, prompt_used?
    """
    prompt_s = (prompt or "").strip()
    if not prompt_s:
        return {"ok": False, "error": "empty_prompt"}
    if not image_gen_configured():
        return {
            "ok": False,
            "error": "image_gen_not_configured",
            "message": "CHAT_IMAGE_GEN_ENABLED=0 or missing LLM_API_KEY/DEEPINFRA_API_KEY",
        }

    url = f"{CHAT_IMAGE_GEN_BASE_URL}/images/generations"
    body: dict[str, Any] = {
        "prompt": prompt_s[:4000],
        "model": CHAT_IMAGE_GEN_MODEL,
        "size": CHAT_IMAGE_GEN_SIZE or "1024x1024",
        "n": 1,
        "response_format": "b64_json",
    }
    # Some DeepInfra models accept negative_prompt on inference; ignore if rejected.
    if (negative_prompt or "").strip():
        body["negative_prompt"] = negative_prompt.strip()[:1000]

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY.strip()}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=CHAT_IMAGE_GEN_TIMEOUT_S) as client:
            resp = await client.post(url, json=body, headers=headers)
    except Exception as exc:  # noqa: BLE001
        logger.warning("image render request failed: %s", exc, exc_info=True)
        return {"ok": False, "error": "image_gen_transport", "message": str(exc)[:400]}

    if resp.status_code >= 400:
        # Retry without negative_prompt if model rejects it.
        if resp.status_code == 422 and "negative_prompt" in body:
            body.pop("negative_prompt", None)
            try:
                async with httpx.AsyncClient(timeout=CHAT_IMAGE_GEN_TIMEOUT_S) as client:
                    resp = await client.post(url, json=body, headers=headers)
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "error": "image_gen_transport",
                    "message": str(exc)[:400],
                }
        if resp.status_code >= 400:
            return {
                "ok": False,
                "error": "image_gen_http",
                "status_code": resp.status_code,
                "message": (resp.text or "")[:500],
            }

    try:
        payload = resp.json()
    except Exception:
        return {"ok": False, "error": "image_gen_bad_json"}

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        return {"ok": False, "error": "image_gen_empty_data", "raw_keys": list(payload) if isinstance(payload, dict) else []}

    first = data[0] if isinstance(data[0], dict) else {}
    b64 = str(first.get("b64_json") or "").strip()
    if not b64:
        # Some gateways return url — fetch bytes.
        img_url = str(first.get("url") or "").strip()
        if img_url:
            try:
                async with httpx.AsyncClient(timeout=CHAT_IMAGE_GEN_TIMEOUT_S) as client:
                    ir = await client.get(img_url)
                if ir.status_code < 400 and ir.content:
                    raw = ir.content
                else:
                    return {"ok": False, "error": "image_gen_url_fetch_failed"}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": "image_gen_url_fetch", "message": str(exc)[:300]}
        else:
            return {"ok": False, "error": "image_gen_missing_b64"}
    else:
        try:
            raw = base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "image_gen_b64_decode", "message": str(exc)[:200]}

    if not raw or len(raw) < 32:
        return {"ok": False, "error": "image_gen_empty_bytes"}

    file_id = f"cf_{uuid.uuid4().hex}"
    safe_name = f"generated_{file_id[-8:]}.png"
    user_dir = uploads_root() / "".join(c if c.isalnum() or c in "-_" else "_" for c in (user_id or "default"))[:120]
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / f"{file_id}_{safe_name}"
    path.write_bytes(raw)

    insert_upload_row(
        file_id=file_id,
        user_id=(user_id or "").strip() or "default",
        session_id=(session_id or "").strip() or "default",
        original_filename=safe_name,
        stored_path=str(path),
        content_type="image/png",
        size_bytes=len(raw),
        extracted_text="",
        extract_status="image",
        extract_error=None,
    )

    return {
        "ok": True,
        "file_id": file_id,
        "filename": safe_name,
        "content_type": "image/png",
        "size": len(raw),
        "model": CHAT_IMAGE_GEN_MODEL,
        "prompt_used": prompt_s[:500],
        "stored_path": str(path),
    }


def public_chat_file_path(file_id: str) -> str:
    """Relative path the BFF/proxy can serve (frontend prefixes /api/aihub)."""
    fid = (file_id or "").strip()
    return f"/chat/file/{fid}"
