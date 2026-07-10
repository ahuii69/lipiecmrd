# -*- coding: utf-8 -*-
"""
Orkiestracja tur vault: dopasowanie wzorca → magazyn → audyt → kontrakt odpowiedzi.

Importuje ``aihub.db.append_event``, ``aihub.user_vault`` oraz moduły ``vault.*``
(bez pamięci semantycznej i bez czatu).
"""

from __future__ import annotations

from aihub.db import append_event
from aihub.user_vault import get_user_vault, normalize_alias
from aihub.vault.contracts import VAULT_FALLBACK_ALIAS, VaultCopy
from aihub.vault.patterns import (
    DELETE,
    LIST_KEYS,
    READ,
    STORE_BROAD_FALLBACK,
    STORE_CREDENTIAL,
    STORE_CREDENTIAL_INLINE,
    STORE_SECRET,
)
from aihub.vault.types import VaultOp, VaultTurnOutcome


def broad_vault_store_intent(message: str) -> bool:
    """Wykrywa prośbę o schowanie sekretu bez poprawnego regexu składni."""
    msg = (message or "").strip()
    if not msg:
        return False
    lowered = msg.lower()
    if any(
        token in lowered
        for token in (
            "zapamiętaj ważny fakt",
            "zapamietaj wazny fakt",
            "hasło projektu",
            "haslo projektu",
            "testowe hasło",
            "testowe haslo",
            "robocze hasło",
            "robocze haslo",
        )
    ):
        return False
    if READ.match(msg) or DELETE.match(msg) or LIST_KEYS.search(msg):
        return False
    if (
        STORE_CREDENTIAL.match(msg)
        or STORE_CREDENTIAL_INLINE.match(msg)
        or STORE_SECRET.match(msg)
    ):
        return False
    return bool(STORE_BROAD_FALLBACK.search(msg))


def classify_vault_intent(message: str) -> VaultOp | None:
    """Klasyfikacja bez I/O — np. decision core, metryki, testy."""
    msg = (message or "").strip()
    if not msg:
        return None
    if (
        STORE_CREDENTIAL.match(msg)
        or STORE_CREDENTIAL_INLINE.match(msg)
        or STORE_SECRET.match(msg)
    ):
        return "store"
    if READ.match(msg):
        return "read"
    if DELETE.match(msg):
        return "delete"
    if LIST_KEYS.search(msg):
        return "list"
    if broad_vault_store_intent(msg):
        return "store"
    return None


def try_vault_turn(user_id: str, message: str) -> VaultTurnOutcome | None:
    """Pełna obsługa tur vault albo None (wtedy wyższa warstwa idzie dalej)."""
    msg = (message or "").strip()
    if not msg:
        return None

    vault = get_user_vault()

    m = STORE_CREDENTIAL.match(msg)
    if m:
        alias_raw, secret = m.group(1).strip(), m.group(2).strip()
        if not secret:
            return VaultTurnOutcome(
                "Format: zapisz hasło do alias: wartość",
                "store",
            )
        vault.upsert(user_id, alias_raw, secret)
        append_event(
            user_id,
            "user_vault.store",
            {"alias_key": normalize_alias(alias_raw), "kind": "credential"},
        )
        return VaultTurnOutcome(VaultCopy.stored(alias_raw), "store")

    m = STORE_CREDENTIAL_INLINE.match(msg)
    if m:
        alias_raw, secret = m.group(1).strip(), m.group(2).strip()
        if not secret:
            return VaultTurnOutcome(
                "Format: zapisz hasło do alias: wartość",
                "store",
            )
        vault.upsert(user_id, alias_raw, secret)
        append_event(
            user_id,
            "user_vault.store",
            {"alias_key": normalize_alias(alias_raw), "kind": "credential_inline"},
        )
        return VaultTurnOutcome(VaultCopy.stored(alias_raw), "store")

    m = STORE_SECRET.match(msg)
    if m:
        alias_raw, secret = m.group(1).strip(), m.group(2).strip()
        if not secret:
            return VaultTurnOutcome(
                "Format: zapisz sekret alias: wartość",
                "store",
            )
        vault.upsert(user_id, alias_raw, secret)
        append_event(
            user_id,
            "user_vault.store",
            {"alias_key": normalize_alias(alias_raw), "kind": "secret"},
        )
        return VaultTurnOutcome(VaultCopy.stored(alias_raw), "store")

    m = READ.match(msg)
    if m:
        alias_raw = m.group(1).strip()
        val = vault.get_plain(user_id, alias_raw)
        append_event(
            user_id,
            "user_vault.read",
            {"alias_key": normalize_alias(alias_raw)},
        )
        if val is None:
            return VaultTurnOutcome(VaultCopy.missing(alias_raw), "read")
        return VaultTurnOutcome(VaultCopy.read_hit(alias_raw, val), "read")

    m = DELETE.match(msg)
    if m:
        alias_raw = m.group(1).strip()
        ok = vault.delete(user_id, alias_raw)
        append_event(
            user_id,
            "user_vault.delete",
            {"alias_key": normalize_alias(alias_raw), "ok": ok},
        )
        if ok:
            return VaultTurnOutcome(VaultCopy.deleted_ok(), "delete")
        return VaultTurnOutcome(VaultCopy.missing(alias_raw), "delete")

    if LIST_KEYS.search(msg):
        keys = vault.list_alias_keys(user_id)
        append_event(user_id, "user_vault.list", {"alias_count": len(keys)})
        if not keys:
            return VaultTurnOutcome(VaultCopy.list_empty(), "list")
        return VaultTurnOutcome(VaultCopy.list_keys(keys), "list")

    if broad_vault_store_intent(msg):
        vault.upsert(user_id, VAULT_FALLBACK_ALIAS, msg)
        append_event(
            user_id,
            "user_vault.store",
            {
                "alias_key": normalize_alias(VAULT_FALLBACK_ALIAS),
                "kind": "fallback_full_message",
            },
        )
        return VaultTurnOutcome(VaultCopy.stored(VAULT_FALLBACK_ALIAS), "store")

    return None
