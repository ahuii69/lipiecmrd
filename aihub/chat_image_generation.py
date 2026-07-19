#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wykrywanie prośb o grafikę + tool ``image.generate`` (prawdziwy render)."""

from __future__ import annotations

import re
from typing import Any, Final

from pydantic import BaseModel, Field

# Intencja: rysunek / obraz / prompt pod modele obrazu (PL + EN).
_IMAGE_INTENT: Final[re.Pattern[str]] = re.compile(
    r"(?is)"
    r"(?:"
    r"\bnarysuj\b|\bnamaluj\b|\brysuj\b|"
    r"stwórz\s+obraz|stworz\s+obraz|wygeneruj\s+obraz|wygeneruj\s+grafikę|wygeneruj\s+grafike|"
    r"zrób\s+obraz|zrob\s+obraz|"
    r"\bobrazek\b|\bobrazu\b|\bgrafik[ęeaę]\b|\bilustracj[ęęei]\b|"
    r"prompt\s+do\s+(?:modelu|obrazu|obrazka|grafiki|dall|dalle|midjourney|mj\b|stable\s*diffusion|sd\b)|"
    r"\bdraw\b|\bgenerate\s+(?:an?\s+)?image\b|\bcreate\s+(?:an?\s+)?(?:image|picture)\b|"
    r"\bimage\s+prompt\b"
    r")"
)

_STRIP_LEADING: Final[re.Pattern[str]] = re.compile(
    r"(?is)^\s*(?:"
    r"narysuj|namaluj|rysuj|"
    r"stwórz\s+obraz|stworz\s+obraz|wygeneruj\s+obraz|wygeneruj\s+grafikę|wygeneruj\s+grafike|"
    r"zrób\s+obraz|zrob\s+obraz|"
    r"draw|generate\s+image|create\s+an?\s+image|create\s+image"
    r")\s*[:\-–]?\s*"
)


def is_image_generation_intent(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return bool(_IMAGE_INTENT.search(t))


def extract_image_subject(user_message: str) -> str:
    """Treść po usunięciu słów-intencji; jeśli pusto — domyślny motyw artystyczny."""
    raw = (user_message or "").strip()
    if not raw:
        return "whimsical abstract surreal scene with playful unexpected shapes"
    s = _STRIP_LEADING.sub("", raw).strip()
    s = re.sub(
        r"(?is)^\s*(?:proszę|poproszę|chcę|chce|potrzebuję|potrzebuje)\s+", "", s
    ).strip()
    if len(s) < 2:
        return "whimsical abstract surreal scene with playful unexpected shapes"
    return s


def compose_image_prompt_package(subject_hint: str) -> dict[str, Any]:
    """Buduje bezpieczny, stylizowany prompt (bez ocen moralnych)."""
    subj = (subject_hint or "").strip()
    if len(subj) < 2:
        subj = "whimsical abstract surreal scene with playful unexpected shapes"

    en_core = (
        "Stylized digital illustration, whimsical surreal art, vibrant harmonious colors, "
        "clean composition, high detail, fantasy art style, no text, no watermark, no logo: "
        f"{subj}"
    )
    pl_desc = (
        "Stylizowana ilustracja cyfrowa w klimacie surrealistycznym / fantastycznym, "
        f"na podstawie motywu: „{subj}”."
    )
    negative = "blurry, low quality, watermark, text, logo"
    return {
        "prompt_en": en_core,
        "description_pl": pl_desc,
        "negative_prompt": negative,
        "subject_used": subj,
    }


def build_image_generation_reply(user_message: str) -> str:
    """Legacy text package (tests / fallback when render unavailable)."""
    pkg = compose_image_prompt_package(extract_image_subject(user_message))
    en = pkg["prompt_en"]
    pl = pkg["description_pl"]
    neg = pkg["negative_prompt"]
    return (
        "**Prompt (DALL·E / Stable Diffusion / Midjourney, EN):**\n"
        f"```\n{en}\n```\n\n"
        f"**Negatywny (opcjonalnie, EN):** `{neg}`\n\n"
        f"**Opis (PL):** {pl}\n\n"
        "**Użycie:** wklej blok EN do generatora; parametry (CFG, kroki) wg Twojego modelu."
    )


class ImageGenerateIn(BaseModel):
    """Wejście narzędzia ``image.generate`` — pełna wiadomość lub sam motyw."""

    user_message: str = Field(default="", max_length=200000)
    subject: str = Field(default="", max_length=4000)
    subject_hint: str = Field(default="", max_length=4000)


async def tool_image_generate_handler(
    ctx: Any, inp: ImageGenerateIn
) -> dict[str, Any]:
    """Render real PNG via DeepInfra FLUX; fall back to prompt package if not configured."""
    hint = (inp.subject_hint or inp.subject or "").strip()
    if not hint and (inp.user_message or "").strip():
        hint = extract_image_subject(inp.user_message)
    pkg = compose_image_prompt_package(hint)

    from aihub.chat_image_render import image_gen_configured, public_chat_file_path, render_image_png

    if not image_gen_configured():
        return {
            "ok": False,
            "result": {
                "error": "image_gen_not_configured",
                "prompt_en": pkg["prompt_en"],
                "description_pl": pkg["description_pl"],
                "negative_prompt": pkg["negative_prompt"],
                "subject_used": pkg["subject_used"],
                "rendered": False,
            },
        }

    user_id = str(getattr(ctx, "user_id", None) or "default")
    session_id = str(getattr(ctx, "session_id", None) or "default")
    rendered = await render_image_png(
        prompt=pkg["prompt_en"],
        user_id=user_id,
        session_id=session_id,
        negative_prompt=pkg["negative_prompt"],
    )
    if not rendered.get("ok"):
        return {
            "ok": False,
            "result": {
                "error": str(rendered.get("error") or "image_gen_failed"),
                "message": str(rendered.get("message") or "")[:400],
                "prompt_en": pkg["prompt_en"],
                "description_pl": pkg["description_pl"],
                "negative_prompt": pkg["negative_prompt"],
                "subject_used": pkg["subject_used"],
                "rendered": False,
            },
        }

    file_id = str(rendered.get("file_id") or "")
    return {
        "ok": True,
        "result": {
            "file_id": file_id,
            "filename": rendered.get("filename"),
            "content_type": rendered.get("content_type"),
            "size": rendered.get("size"),
            "model": rendered.get("model"),
            "url_path": public_chat_file_path(file_id),
            "public_url": f"/api/aihub{public_chat_file_path(file_id)}",
            "prompt_en": pkg["prompt_en"],
            "description_pl": pkg["description_pl"],
            "negative_prompt": pkg["negative_prompt"],
            "subject_used": pkg["subject_used"],
            "rendered": True,
        },
    }
