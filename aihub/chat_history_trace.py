#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pola diagnostyczne dla historii żądania czatu (observability, bez pełnej treści w logach)."""

from __future__ import annotations

from typing import Any

from aihub.chat_contracts import ChatMessage, ChatTurnInput

_PREVIEW_LEN = 200


def build_history_trace(turn: ChatTurnInput) -> dict[str, Any]:
    """Liczby + skróty pierwszej/ostatniej wiadomości usera z ``turn.history``."""
    hist = list(turn.history or [])
    n = len(hist)
    first_preview: str | None = None
    last_preview: str | None = None
    user_turns = 0
    for item in hist:
        if isinstance(item, ChatMessage):
            role, content = item.role, (item.content or "").strip()
        elif isinstance(item, dict):
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
        else:
            continue
        if role != "user" or not content:
            continue
        user_turns += 1
        snippet = content[:_PREVIEW_LEN]
        if first_preview is None:
            first_preview = snippet
        last_preview = snippet
    return {
        "history_message_count": n,
        "history_user_turns_in_payload": user_turns,
        "history_first_user_message_preview": first_preview,
        "history_last_user_message_preview": last_preview,
    }
