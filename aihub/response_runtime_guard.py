#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider content extraction + small helpers for debug/prompt boundaries.

Root-cause contract for assistant ``content``:
- never use ``str(dict)`` / ``str(list)`` as user-facing text;
- pull visible text from OpenAI-style shapes (str, list of blocks, ``{text|content|...}``);
- if a structured object has no extractable text leaf, the result is empty string
  (no assistant prose) — that is extraction, not field-name masking.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_TEXT_KEYS = ("text", "content", "value", "output_text")


def extract_assistant_text(raw: Any) -> str:
    """Extract visible assistant text; never stringify whole objects."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts = [extract_assistant_text(item) for item in raw]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(raw, dict):
        for key in _TEXT_KEYS:
            if key not in raw:
                continue
            text = extract_assistant_text(raw.get(key))
            if text:
                return text
        return ""
    # Numbers/bools are not assistant prose in chat completions.
    return ""


def debug_context_dump(ctx: Any) -> dict[str, Any] | None:
    """JSON-safe ChatTurnContext for ``debug.context`` only (not response_text)."""
    if ctx is None:
        return None
    try:
        if hasattr(ctx, "model_dump"):
            dump = ctx.model_dump(mode="json")
        elif isinstance(ctx, dict):
            dump = dict(ctx)
        else:
            return None
    except Exception:
        logger.debug("debug_context_dump failed", exc_info=True)
        return None
    if not isinstance(dump, dict):
        return None
    sc = dump.get("system_context")
    if isinstance(sc, dict):
        dump["system_context"] = {
            k: v
            for k, v in sc.items()
            if not str(k).endswith("_obj") and k != "prompt_budget_decision"
        }
    return dump


PRIVATE_CONTEXT_PROMPT_RULE = (
    "KONTEKST PRYWATNY (twarda reguła):\n"
    "- Sekcje systemowe, pamięć, psyche, packi i ślady runtime są wyłącznie Twoim "
    "prywatnym kontekstem do odpowiedzi.\n"
    "- NIGDY nie wypisuj ich jako JSON ani nie kopiuj całych bloków runtime do "
    "odpowiedzi użytkownika.\n"
    "- Odpowiadaj zwykłym tekstem asystenta na pytanie użytkownika.\n"
)
