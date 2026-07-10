# -*- coding: utf-8 -*-
"""
Kontrakt produktowy vault: jedna składnia odpowiedzi, stałe redakcji transkryptu.

Żadnych importów spoza stdlib — można testować i czytać w izolacji.
"""

from __future__ import annotations

# Stały alias dla zapisu fallback (cała wiadomość jako sekret).
VAULT_FALLBACK_ALIAS = "autozapis"


class VaultCopy:
    """Teksty odpowiedzi zwracane użytkownikowi (spójne, przewidywalne)."""

    @staticmethod
    def stored(_display_alias: str) -> str:
        return "Zapisane."

    @staticmethod
    def read_hit(_display_alias: str, secret_plain: str) -> str:
        return f"Odczytano: {secret_plain}"

    @staticmethod
    def missing(_display_alias: str) -> str:
        return "Brak wpisu."

    @staticmethod
    def deleted_ok() -> str:
        return "Usunięte."

    @staticmethod
    def list_keys(keys: list[str]) -> str:
        return f"Klucze: {', '.join(keys)}."

    @staticmethod
    def list_empty() -> str:
        return "Brak kluczy."


class TranscriptRedaction:
    """Redakcje w ``chat_session_messages`` — sekrety nie są duplikowane w sesji."""

    USER_ON_STORE = "[vault: treść wiadomości użytkownika ukryta]"
    ASSISTANT_SENSITIVE = "[vault: odpowiedź ukryta]"

    @staticmethod
    def should_redact_user(op: str) -> bool:
        return op == "store"

    @staticmethod
    def should_redact_assistant(op: str) -> bool:
        return op in ("read", "list")
