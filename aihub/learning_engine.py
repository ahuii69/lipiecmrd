#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import logging
import re
from typing import Any, Dict, List, Set, Tuple

from aihub.db import append_event, now_ts
from aihub.memory_core import get_memory_core
from aihub.psyche_core import get_psyche_core

logger = logging.getLogger(__name__)


class LearningEngine:
    """
    System autouczenia się asystenta AI.
    Analizuje dialogi i automatycznie gromadzi wiedzę.
    """

    def __init__(self):
        self.learned_facts: Set[str] = set()  # Hashes faktów aby uniknąć duplikatów
        self.extraction_rules: List[Dict[str, Any]] = self._init_extraction_rules()

    def _init_extraction_rules(self) -> List[Dict[str, Any]]:
        """Inicjalizacja reguł ekstrakcji wiedzy."""
        return [
            {
                "name": "user_identity",
                "patterns": [
                    r"(?:my name|mam na imię|jestem|называюсь)\s+([A-Ząćęłńóśźż\w\s]+)",
                    r"(?:I'm|jestem)\s+([A-Ząćęłńóśźż\w\s]+)(?:\s+(?:yo)?u)",
                ],
                "tags": ["user", "identity", "personal"],
                "importance": 0.75,
                "confidence": 0.85,
            },
            {
                "name": "user_preference",
                "patterns": [
                    r"(?:lubię|nie lubię|preferuję|wolę)\s+(.+?)(?:\.|,|!|\?|$)",
                    r"(?:my favorite|ulubio[ny]|najlepszy)\s+(.+?)(?:\.|,|!|\?|$)",
                ],
                "tags": ["user", "preference"],
                "importance": 0.65,
                "confidence": 0.75,
            },
            {
                "name": "user_declarative_pl",
                "patterns": [
                    # „mój kolor to zielony” → „kolor → zielony” (czytelny fakt)
                    r"(?:mój|moja|moje)\s+(\S+)\s+to\s+(.+?)(?:\.|,|!|\?|$)",
                    r"(?:mój|moja|moje)\s+(\S+)\s+jest\s+(.+?)(?:\.|,|!|\?|$)",
                    r"(?:zapamiętaj|zapamietaj)\s*,\s*że\s+(.+?)(?:\.|,|!|\?|$)",
                    r"(?:zapamiętaj|zapamietaj)\s*,\s*ze\s+(.+?)(?:\.|,|!|\?|$)",
                ],
                "tags": ["user", "fact", "declarative", "pl"],
                "importance": 0.72,
                "confidence": 0.82,
                "min_spaces": 0,
            },
            {
                "name": "user_work",
                "patterns": [
                    r"(?:pracuję|praca|jestem)\s+(?:in|w|jako)\s+(.+?)(?:\.|,|!|\?|$)",
                    r"(?:moja praca|moje stanowisko)\s+to\s+(.+?)(?:\.|,|!|\?|$)",
                ],
                "tags": ["user", "work", "profession"],
                "importance": 0.70,
                "confidence": 0.80,
            },
            {
                "name": "user_goal",
                "patterns": [
                    r"(?:chcę|chciałbym|marzę|celem mi)\s+(.+?)(?:\.|,|!|\?|$)",
                    r"(?:my goal|mój cel)\s+(?:to|is)\s+(.+?)(?:\.|,|!|\?|$)",
                ],
                "tags": ["user", "goal", "aspiration"],
                "importance": 0.80,
                "confidence": 0.70,
            },
            {
                "name": "technical_fact",
                "patterns": [
                    r"(?:używam|korzystam|programuję)\s+z\s+(.+?)(?:\.|,|!|\?|$)",
                    r"(?:I use|I work with)\s+(.+?)(?:\.|,|!|\?|$)",
                ],
                "tags": ["technical", "skill", "tool"],
                "importance": 0.60,
                "confidence": 0.75,
            },
            {
                "name": "constraint",
                "patterns": [
                    r"(?:nie mogę|nie mam|nie wiem|ograniczeni?)\s+(.+?)(?:\.|,|!|\?|$)",
                    r"(?:I can't|I don't have)\s+(.+?)(?:\.|,|!|\?|$)",
                ],
                "tags": ["constraint", "limitation"],
                "importance": 0.70,
                "confidence": 0.80,
            },
        ]

    def _extract_with_regex(self, text: str, patterns: List[str]) -> List[str]:
        """Ekstrakcja tekstu używając regex patterns."""
        matches: List[str] = []
        text_lower = text.lower()

        for pattern in patterns:
            try:
                found = re.findall(pattern, text_lower, re.IGNORECASE | re.UNICODE)
                for item in found:
                    if isinstance(item, tuple):
                        parts = [p.strip() for p in item if p and str(p).strip()]
                        if not parts:
                            continue
                        if len(parts) >= 2:
                            matches.append(f"{parts[0]} → {parts[1]}")
                        else:
                            matches.append(parts[0])
                    else:
                        m = str(item).strip()
                        if m and len(m) > 2:
                            matches.append(m)
            except Exception as e:
                logger.debug(f"Regex pattern error: {e}")

        return matches

    def _hash_fact(self, fact_text: str, category: str) -> str:
        """Tworzenie hash faktów do deduplikacji."""
        h = hashlib.sha256()
        h.update(category.encode())
        h.update(b"\0")
        h.update(fact_text.lower().encode())
        return h.hexdigest()

    def _is_duplicate(self, fact_hash: str) -> bool:
        """Sprawdzenie czy fakt został już nauczony."""
        return fact_hash in self.learned_facts

    def _validate_extraction(self, extracted: str, rule: Dict[str, Any]) -> bool:
        """Walidacja jakości ekstrakcji."""
        if not extracted or len(extracted.strip()) < 2:
            return False

        min_spaces = int(rule.get("min_spaces", 1))
        if extracted.count(" ") < min_spaces and "→" not in extracted:
            if len(extracted.strip()) < 4:
                return False

        # Jeśli to email/URL, skip
        if "@" in extracted or "://" in extracted:
            return False

        return True

    def extract_facts_from_message(
        self, user_id: str, text: str, role: str
    ) -> List[Tuple[str, List[str], float, float]]:
        """
        Ekstracja faktów z wiadomości.

        Returns: Lista (fact_text, tags, importance, confidence)
        """
        facts: List[Tuple[str, List[str], float, float]] = []

        # Przebierz rules i szukaj matches
        for rule in self.extraction_rules:
            matches = self._extract_with_regex(text, rule["patterns"])

            for match in matches:
                if not self._validate_extraction(match, rule):
                    continue

                # Czytelniejsza treść niż surowy klucz reguły (UI / retrieval).
                if rule.get("name") == "user_declarative_pl":
                    fact_text = f"Użytkownik (PL): {match}"
                else:
                    fact_text = f"{rule['name']}: {match}"
                fact_hash = self._hash_fact(fact_text, rule["name"])

                # Sprawdzenie duplikatów
                if self._is_duplicate(fact_hash):
                    continue

                facts.append(
                    (
                        fact_text,
                        rule["tags"],
                        rule.get("importance", 0.5),
                        rule.get("confidence", 0.6),
                    )
                )

                self.learned_facts.add(fact_hash)

        return facts

    def process_turn(
        self,
        user_id: str,
        user_msg: str,
        assistant_msg: str,
        intent: str,
        meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Przetwarzanie całej turny dialogu dla nauki.

        Args:
            user_id: ID użytkownika
            user_msg: Wiadomość od użytkownika
            assistant_msg: Odpowiedź asystenta
            intent: Zamiar użytkownika
            meta: Metadane dodatkowe

        Returns:
            Słownik ze statystyką nauki
        """
        get_psyche_core().ensure_user(user_id)

        try:
            learned_facts = []

            # Ekstrakcja faktów z wiadomości użytkownika
            user_facts = self.extract_facts_from_message(user_id, user_msg, "user")

            for fact_text, tags, importance, confidence in user_facts:
                try:
                    get_memory_core().ingest_fact(
                        user_id,
                        fact_text,
                        tags=tags + [intent],
                        meta={
                            "source": "learning.user",
                            "importance": importance,
                            "confidence": confidence,
                        },
                    )
                    learned_facts.append(
                        {
                            "text": fact_text,
                            "tags": tags,
                            "importance": importance,
                            "confidence": confidence,
                        }
                    )
                except Exception as e:
                    logger.error(f"Error adding fact: {e}")

            # Opcjonalnie: ekstrakcja z odpowiedzi asystenta (dla self-correction loop)
            assistant_facts = self.extract_facts_from_message(
                user_id, assistant_msg, "assistant"
            )
            for fact_text, tags, importance, confidence in assistant_facts:
                # Asystent może uczyć się swoich własnych obserwacji
                tags = tags + ["assistant_observation", intent]
                try:
                    get_memory_core().ingest_fact(
                        user_id,
                        fact_text,
                        tags=tags,
                        meta={
                            "source": "learning.assistant",
                            "importance": importance * 0.7,  # Niższy priorytet
                            "confidence": confidence * 0.8,
                        },
                    )
                    learned_facts.append(
                        {
                            "text": fact_text,
                            "tags": tags,
                            "importance": importance * 0.7,
                            "confidence": confidence * 0.8,
                        }
                    )
                except Exception as e:
                    logger.error(f"Error adding assistant fact: {e}")

            # Log nauki
            append_event(
                user_id,
                "learning.process_turn",
                {
                    "intent": intent,
                    "facts_learned": len(learned_facts),
                    "facts": learned_facts,
                },
            )

            logger.info(
                f"Learning for user {user_id}: {len(learned_facts)} facts extracted"
            )

            return {
                "ok": True,
                "facts_learned": len(learned_facts),
                "facts": learned_facts,
                "ts": now_ts(),
            }

        except Exception as e:
            logger.error(
                f"Error in learning.process_turn for user {user_id}: {e}", exc_info=True
            )
            append_event(user_id, "learning.error", {"error": str(e)})
            return {"ok": False, "error": str(e), "ts": now_ts()}

    def learn_from_reflection(
        self, user_id: str, reflection: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Meta-nauka: nauka na podstawie refleksji nad dialogami.

        Dodaje high-level insights jako fakty.
        """
        get_psyche_core().ensure_user(user_id)

        try:
            facts_added = 0

            # Z recommendations
            for rec in reflection.get("recommendations", []):
                try:
                    get_memory_core().ingest_fact(
                        user_id,
                        f"AI insight: {rec}",
                        tags=["ai_insight", "meta_learning"],
                        meta={
                            "source": "learning.reflection",
                            "importance": 0.50,
                            "confidence": 0.6,
                        },
                    )
                    facts_added += 1
                except Exception as e:
                    logger.debug(f"Error adding reflection fact: {e}")

            # Z topics
            if reflection.get("topics"):
                topics_str = ", ".join(reflection["topics"][:3])
                try:
                    get_memory_core().ingest_fact(
                        user_id,
                        f"User topics of interest: {topics_str}",
                        tags=["user", "interests", "topics"],
                        meta={
                            "source": "learning.reflection",
                            "importance": 0.45,
                            "confidence": 0.70,
                        },
                    )
                    facts_added += 1
                except Exception as e:
                    logger.debug(f"Error adding topics fact: {e}")

            append_event(user_id, "learning.reflection", {"facts_added": facts_added})

            logger.debug(
                f"Learning from reflection for user {user_id}: {facts_added} facts"
            )

            return {"ok": True, "facts_added": facts_added, "ts": now_ts()}

        except Exception as e:
            logger.error(f"Error in learn_from_reflection: {e}", exc_info=True)
            return {"ok": False, "error": str(e), "ts": now_ts()}


# Singleton
_learning_engine = LearningEngine()


def process_turn(
    user_id: str, user_msg: str, assistant_msg: str, intent: str, meta: Dict[str, Any]
) -> Dict[str, Any]:
    """Public API dla przetwarzania tury."""
    return _learning_engine.process_turn(user_id, user_msg, assistant_msg, intent, meta)


def learn_from_reflection(user_id: str, reflection: Dict[str, Any]) -> Dict[str, Any]:
    """Public API dla meta-nauki."""
    return _learning_engine.learn_from_reflection(user_id, reflection)
