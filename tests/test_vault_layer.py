"""Testy warstwy ``aihub.vault`` bez pełnego stacku czatu (kontrakty + NLU)."""

from __future__ import annotations

from aihub.vault.contracts import TranscriptRedaction, VaultCopy
from aihub.vault.patterns import LIST_KEYS
from aihub.vault.service import classify_vault_intent
from aihub.vault.transcript import redact_transcript_for_vault_turn


def test_vault_copy_contract_shapes():
    assert VaultCopy.stored("gmail") == "Zapisane."
    assert VaultCopy.read_hit("gmail", "x") == "Odczytano: x"
    assert VaultCopy.missing("x") == "Brak wpisu."
    assert VaultCopy.deleted_ok() == "Usunięte."
    assert VaultCopy.list_empty() == "Brak kluczy."
    assert VaultCopy.list_keys(["a", "b"]) == "Klucze: a, b."


def test_classify_intent_roundtrip():
    assert classify_vault_intent("zapamiętaj kod do api: 1") == "store"
    assert classify_vault_intent("podaj kod do api") == "read"
    assert classify_vault_intent("usuń kod do api") == "delete"
    assert classify_vault_intent("jakie mam klucze?") == "list"
    assert classify_vault_intent("co tam?") is None


def test_classify_broad_store_intent_without_colon_syntax():
    assert (
        classify_vault_intent(
            "zapisz proszę moje hasło w jednej linii bez dwukropka i aliasu",
        )
        == "store"
    )


def test_list_keys_pattern_shared_with_policy_surface():
    assert LIST_KEYS.search("jakie mam klucze?")


def test_transcript_redaction_policy():
    class _R:
        trace = {"vault_turn": True, "vault_operation": "store"}

    u, a = redact_transcript_for_vault_turn(
        "secret msg",
        "ok",
        result=_R(),
        error=None,
    )
    assert u == TranscriptRedaction.USER_ON_STORE
    assert a == "ok"

    class _R2:
        trace = {"vault_turn": True, "vault_operation": "read"}

    u2, a2 = redact_transcript_for_vault_turn(
        "podaj hasło",
        "Hasło do x: y",
        result=_R2(),
        error=None,
    )
    assert u2 == "podaj hasło"
    assert a2 == TranscriptRedaction.ASSISTANT_SENSITIVE


def test_firewall_blocks_credential_memory_recall():
    from aihub.vault.firewall import blocks_memory_fact_recall_for_credentials

    assert blocks_memory_fact_recall_for_credentials("co wiesz o moim haśle?")
