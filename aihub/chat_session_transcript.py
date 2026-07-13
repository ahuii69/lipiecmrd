#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trwały transkrypt sesji czatu (SQLite) — zapis po każdej turze /chat/turn."""

from __future__ import annotations

import logging

from aihub.chat_contracts import ChatTurnInput, ChatTurnResult
from aihub.vault.transcript import redact_transcript_for_vault_turn

logger = logging.getLogger(__name__)

_ASSISTANT_MAX = 500_000


def persist_chat_turn_messages(
    turn: ChatTurnInput,
    result: ChatTurnResult | None,
    error: BaseException | None,
) -> None:
    """Zapisz parę user+asystent po turze (gdy jest treść usera)."""
    uid = (turn.user_id or "").strip() or "default"
    if str(getattr(turn, "runtime_mode", "") or "").lower() == "audit":
        return
    sid = (turn.session_id or "").strip() or "default"
    user_text = (turn.message or "").strip()
    if not user_text:
        return

    if error is not None:
        assistant_text = f"[błąd serwera] {error}"
    elif result is not None:
        assistant_text = result.response_text or ""
    else:
        assistant_text = ""

    user_text, assistant_text = redact_transcript_for_vault_turn(
        user_text,
        assistant_text,
        result=result,
        error=error,
    )

    if len(assistant_text) > _ASSISTANT_MAX:
        assistant_text = assistant_text[:_ASSISTANT_MAX]

    try:
        from aihub.db import ensure_chat_session_row, insert_chat_session_message_pair

        ensure_chat_session_row(uid, sid)
        user_meta: dict[str, object] = {}
        ids = getattr(turn, "attached_file_ids", None) or []
        if isinstance(ids, list) and ids:
            user_meta["attached_file_ids"] = [str(x) for x in ids if str(x).strip()][
                :8
            ]
        insert_chat_session_message_pair(
            uid, sid, user_text, assistant_text, user_meta=user_meta or None
        )
    except Exception:
        logger.exception(
            "chat_session_transcript: persist failed user=%s session=%s",
            uid,
            sid,
        )
