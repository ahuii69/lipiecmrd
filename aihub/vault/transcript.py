# -*- coding: utf-8 -*-
"""Redakcja linii transkryptu sesji dla tur vault — polityka w ``contracts``."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from aihub.vault.contracts import TranscriptRedaction


class _SupportsVaultTrace(Protocol):
    """Minimalny kształt wyniku tury z polami ``vault_turn`` / ``vault_operation`` w trace."""

    trace: Mapping[str, Any]


def redact_transcript_for_vault_turn(
    user_text: str,
    assistant_text: str,
    *,
    result: _SupportsVaultTrace | None,
    error: BaseException | None,
) -> tuple[str, str]:
    """Zwraca (user, assistant) z ewentualną redakcją; bez sekretów w SQLite sesji."""
    if error is not None or result is None:
        return user_text, assistant_text
    if not result.trace.get("vault_turn") or not result.trace.get("vault_operation"):
        return user_text, assistant_text
    op = str(result.trace.get("vault_operation") or "")
    u, a = user_text, assistant_text
    if TranscriptRedaction.should_redact_user(op):
        u = TranscriptRedaction.USER_ON_STORE
    if TranscriptRedaction.should_redact_assistant(op):
        a = TranscriptRedaction.ASSISTANT_SENSITIVE
    return u, a
