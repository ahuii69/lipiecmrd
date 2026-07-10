#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wykrywanie prośb o grafikę i budowa gotowych promptów (bez ogólnych odmów)."""

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

    # Warstwa „bezpieczna”: zawsze ramka artystyczna / surreal / stylizacja.
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
    """Tekst odpowiedzi użytkownikowi: zawsze konkretny prompt + opis."""
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
    _ctx: Any, inp: ImageGenerateIn
) -> dict[str, Any]:
    hint = (inp.subject_hint or inp.subject or "").strip()
    if not hint and (inp.user_message or "").strip():
        hint = extract_image_subject(inp.user_message)
    pkg = compose_image_prompt_package(hint)
    return {
        "ok": True,
        "result": {
            "prompt_en": pkg["prompt_en"],
            "description_pl": pkg["description_pl"],
            "negative_prompt": pkg["negative_prompt"],
            "subject_used": pkg["subject_used"],
        },
    }
