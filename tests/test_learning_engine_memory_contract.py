"""Kontrakt A/C: ekstrakcja PL + spójne meta pamięci (scope użytkownika)."""

from __future__ import annotations

import pytest


def test_declarative_pl_extracts_structured_fact():
    from aihub.learning_engine import LearningEngine

    le = LearningEngine()
    facts = le.extract_facts_from_message("u1", "mój kolor to zielony", "user")
    assert facts, "expected at least one fact"
    text = facts[0][0]
    assert "→" in text or "zielony" in text.lower(), text
    assert "Użytkownik (PL):" in text


def test_zapamietaj_ze_clause():
    from aihub.learning_engine import LearningEngine

    le = LearningEngine()
    facts = le.extract_facts_from_message(
        "u1", "zapamiętaj, że jutro mam deadline", "user"
    )
    assert facts, facts
    assert "deadline" in facts[0][0].lower()


def test_ingest_meta_sets_memory_scope():
    from aihub.memory_engine import _ingest_meta

    m = _ingest_meta({"session_id": "sess-1"}, source="test", source_episode="ep")
    assert m["memory_scope"] == "user"
    assert m["session_id"] == "sess-1"
    assert m["source"] == "test"
