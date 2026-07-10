# -*- coding: utf-8 -*-
"""
Granica vault ↔ ogólna pamięć: nie podajemy treści poświadczeń z retrievalu.

Zależność od ``chat_product_policy`` jest celowa — ten sam słownik „pytania o fakt”
co w ``try_memory_fact_read_turn``.
"""

from __future__ import annotations

from aihub.chat_product_policy import MEMORY_FACT_RECALL_HINT
from aihub.vault.patterns import CREDENTIAL_IN_USER_QUESTION


def blocks_memory_fact_recall_for_credentials(message: str) -> bool:
    """True → ścieżka „jeden fakt z pamięci” musi się wyłączyć."""
    t = (message or "").strip()
    if not t or len(t) > 280:
        return False
    if not MEMORY_FACT_RECALL_HINT.search(t):
        return False
    # Block only when user is likely asking about a real credential/secret alias,
    # not when they use test/project wording for memory validation.
    has_credential_lexeme = bool(CREDENTIAL_IN_USER_QUESTION.search(t))
    if not has_credential_lexeme:
        return False
    lowered = t.lower()
    if any(
        token in lowered
        for token in (
            "testowe hasło",
            "testowe haslo",
            "hasło projektu",
            "haslo projektu",
            "robocze hasło",
            "robocze haslo",
        )
    ):
        return False
    return True
