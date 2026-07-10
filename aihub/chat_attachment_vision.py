"""Opis obrazów: Ollama (VPS) lub OpenAI-compatible (legacy)."""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from aihub.chat_file_service import (
    MAX_UPLOAD_BYTES,
    fetch_files_for_ids,
    update_file_extraction,
)
from aihub.config import (
    CHAT_VISION_API_KEY,
    CHAT_VISION_API_URL,
    CHAT_VISION_BACKEND,
    CHAT_VISION_ENABLED,
    CHAT_VISION_FALLBACK_MODEL,
    CHAT_VISION_MODEL,
    CHAT_VISION_OLLAMA_URL,
    CHAT_VISION_TIMEOUT_S,
    HTTP_CA_BUNDLE,
    HTTP_TRUST_ENV,
    LLM_API_KEY,
    LLM_BASE_URL,
)

logger = logging.getLogger(__name__)

_VISION_PROMPT = (
    "Opisz zwięźle po polsku, co widać na obrazie. "
    "Jeśli widać tekst — przytocz go w cudzysłowie. "
    "Jeśli obraz jest pusty lub nieczytelny, napisz to wprost."
)


def _ssl_verify() -> bool | str:
    if HTTP_CA_BUNDLE:
        return HTTP_CA_BUNDLE
    return True


def _guess_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime
    suf = Path(path).suffix.lower()
    if suf == ".png":
        return "image/png"
    if suf in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suf == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _ollama_extract_content(data: dict[str, Any]) -> str:
    msg = data.get("message")
    if not isinstance(msg, dict):
        return ""
    c = msg.get("content")
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        parts: list[str] = []
        for item in c:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts).strip()
    return ""


async def _describe_ollama(*, b64_image: str) -> tuple[str | None, str | None]:
    base = CHAT_VISION_OLLAMA_URL.strip().rstrip("/")
    if not base:
        return None, "ollama_url_missing"
    url = f"{base}/api/chat"
    models_to_try: list[str] = []
    if CHAT_VISION_MODEL:
        models_to_try.append(CHAT_VISION_MODEL)
    fb = (CHAT_VISION_FALLBACK_MODEL or "").strip()
    if fb and fb not in models_to_try:
        models_to_try.append(fb)

    last_err: str | None = None
    async with httpx.AsyncClient(
        timeout=CHAT_VISION_TIMEOUT_S,
        verify=_ssl_verify(),
        trust_env=HTTP_TRUST_ENV,
    ) as client:
        for model in models_to_try:
            body: dict[str, Any] = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": _VISION_PROMPT,
                        "images": [b64_image],
                    }
                ],
                "stream": False,
            }
            try:
                resp = await client.post(url, json=body)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ollama vision transport: %s", exc)
                return None, f"ollama_transport:{type(exc).__name__}"

            if resp.status_code >= 400:
                try:
                    detail = resp.json()
                except Exception:
                    detail = resp.text[:400]
                logger.warning(
                    "ollama vision HTTP %s model=%s: %s",
                    resp.status_code,
                    model,
                    detail,
                )
                last_err = f"ollama_http_{resp.status_code}:{model}"
                continue

            try:
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001
                last_err = f"ollama_bad_json:{exc}"
                continue

            text = _ollama_extract_content(payload)
            if text:
                return text, None
            last_err = f"ollama_empty_content:{model}"

    return None, last_err or "ollama_no_model_succeeded"


async def _describe_openai_compatible(
    *, b64_image: str, mime: str
) -> tuple[str | None, str | None]:
    api_url = (CHAT_VISION_API_URL or LLM_BASE_URL or "").strip().rstrip("/")
    api_key = (CHAT_VISION_API_KEY or LLM_API_KEY or "").strip()
    model = (CHAT_VISION_MODEL or "").strip()
    if not api_url or not api_key:
        return None, "vision_openai_not_configured"
    if not model:
        return None, "vision_model_missing"

    url = f"{api_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 1024,
        "temperature": 0.2,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VISION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64_image}"},
                    },
                ],
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            timeout=CHAT_VISION_TIMEOUT_S,
            verify=_ssl_verify(),
            trust_env=HTTP_TRUST_ENV,
        ) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("vision openai transport: %s", exc)
        return None, f"vision_transport:{type(exc).__name__}"

    if resp.status_code >= 400:
        return None, f"vision_http_{resp.status_code}"

    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return None, f"vision_bad_json:{exc}"

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, "vision_no_choices"
    msg0 = choices[0]
    if not isinstance(msg0, dict):
        return None, "vision_bad_choice"
    m = msg0.get("message")
    content = ""
    if isinstance(m, dict):
        c = m.get("content")
        if isinstance(c, str):
            content = c.strip()
        elif isinstance(c, list):
            parts: list[str] = []
            for item in c:
                if isinstance(item, dict) and item.get("type") == "text":
                    t = item.get("text")
                    if isinstance(t, str):
                        parts.append(t)
            content = "\n".join(parts).strip()
    if not content:
        return None, "vision_empty_content"
    return content, None


async def describe_image_file(stored_path: str) -> tuple[str | None, str | None]:
    """Zwraca (opis PL, kod błędu)."""
    if not CHAT_VISION_ENABLED:
        return None, "vision_disabled"

    p = Path(stored_path)
    if not p.is_file():
        return None, "file_missing"
    raw = p.read_bytes()
    if len(raw) > MAX_UPLOAD_BYTES:
        return None, "file_too_large"
    mime = _guess_mime(str(p))
    b64 = base64.standard_b64encode(raw).decode("ascii")

    backend = (CHAT_VISION_BACKEND or "ollama").strip().lower()
    if backend in ("ollama", "local_ollama"):
        return await _describe_ollama(b64_image=b64)

    if backend in ("openai_compatible", "openai", "remote"):
        return await _describe_openai_compatible(b64_image=b64, mime=mime)

    return None, f"vision_bad_backend:{backend}"


async def enrich_image_attachments_for_turn(
    *,
    user_id: str,
    session_id: str,
    file_ids: list[str],
) -> None:
    """Dla wierszy extract_status=image — podpis vision (aktualizacja SQLite)."""
    if not file_ids:
        return
    rows = fetch_files_for_ids(
        user_id=user_id, session_id=session_id, file_ids=file_ids
    )
    for row in rows:
        if row.get("extract_status") != "image":
            continue
        path = str(row.get("stored_path") or "")
        fid = str(row.get("file_id") or "")
        if not path or not fid:
            continue
        text, err = await describe_image_file(path)
        if text:
            update_file_extraction(
                file_id=fid,
                user_id=user_id,
                session_id=session_id,
                extracted_text=text,
                extract_status="ok",
                extract_error=None,
            )
        elif err:
            # Persist the concrete error code (transport error, http 404, misconfig, disabled, ...) so
            # the attachment prompt block can tell the user a specific, honest status instead of a
            # generic "no visual access" line.
            update_file_extraction(
                file_id=fid,
                user_id=user_id,
                session_id=session_id,
                extracted_text="",
                extract_status="image",
                extract_error=err,
            )
