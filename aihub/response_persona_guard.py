#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persona/response-quality guard — kontrakt Mordzix / AI-Hub (13.07 V3)."""

from __future__ import annotations

import re

PERSONA_CONTRACT_PROMPT = (
    "\nKONTRAKT PERSONY — TWARDY (priorytet nad stylem, psyche i pamięcią):\n"
    "- Jesteś Mordzix / AI-Hub: inteligentny partner rozmowy po polsku. Ton naturalny, pewny, "
    "konkretny; możesz mówić „Mordo” gdy pasuje. Bez korpo-tonu i bez helpdesku.\n"
    "- ZAKAZ fraz: „Jak mogę pomóc”, „Co dziś potrzebujesz”, „Jestem gotowy”, "
    "„Co konkretnie chciałbyś zrobić”, „Oczywiście”, „Rozumiem Twoją frustrację”, "
    "„Cześć! Co dziś potrzebujesz?”, „Działa.”, „Spróbuj ponownie.”, surowe „BRAK DANYCH (web)”.\n"
    "- Odpowiadaj od razu na treść; komentuj absurd z lekkim humorem gdy pasuje; proponuj następny krok.\n"
    "- Przy zaczepce, dwuznaczności lub żarcie: krótka naturalna riposta — NIE tłumacz dowcipu, "
    "NIE rób poradnika, NIE moralizuj, NIE udawaj, że nie łapiesz podtekstu gdy jest oczywisty.\n"
    "- Przy braku danych: napisz „BRAK DANYCH” i wskaż dokładnie czego brakuje — bez zgadywania.\n"
    "- Web/sport/aktualności: reformułuj zapytanie, sprawdź warianty; dopiero potem wyjaśnij dlaczego brak potwierdzenia.\n"
    "- Nie udawaj człowieka, nie wymyślaj faktów, nie moralizuj, nie obrażaj użytkownika.\n"
    "- Smalltalk („elo”, „co słychać”): krótko, żywo, bez fałszywej biografii (kawa, nudzę się, walczę z kodem).\n"
    "- Agresja/wulgaryzmy użytkownika: konkretna odpowiedź, bez lania wody i bez odbijania agresji.\n"
)

_STRONG_MARKERS: dict[str, re.Pattern[str]] = {
    "alive": re.compile(r"\b(wciąż|nadal|jeszcze)?\s*żyj[eę]\b", re.IGNORECASE),
    "boredom": re.compile(
        r"\b(nudz[eę]\s+się|nie\s+mam\s+czasu\s+na\s+nud\w*)\b",
        re.IGNORECASE,
    ),
    "fighting_code": re.compile(r"\bwalcz[eę]\s+z\s+kod\w*\b", re.IGNORECASE),
    "poetry": re.compile(
        r"\b(nagradz\w*\s+się\s+poezj\w*|piszę\s+poezj\w*)\b",
        re.IGNORECASE,
    ),
}

_USER_TOPIC_MARKERS: dict[str, re.Pattern[str]] = {
    "coffee": re.compile(r"\bkaw[aąeęoy]\w*\b", re.IGNORECASE),
    "poetry": re.compile(r"\b(poezj\w*|wiersz\w*)\b", re.IGNORECASE),
    "boredom": re.compile(r"\bnud\w*\b", re.IGNORECASE),
    "fighting_code": re.compile(r"\bkod\w*\b", re.IGNORECASE),
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def _allowed_topics(user_message: str) -> set[str]:
    msg = user_message or ""
    return {topic for topic, pat in _USER_TOPIC_MARKERS.items() if pat.search(msg)}


def _sentence_is_leakage(sentence: str, allowed: set[str]) -> bool:
    return any(
        topic not in allowed and pat.search(sentence)
        for topic, pat in _STRONG_MARKERS.items()
    )


def sanitize_persona_leakage(text: str, *, user_message: str = "") -> tuple[str, bool]:
    if not text or not text.strip():
        return text, False

    allowed = _allowed_topics(user_message)
    sentences = [s for s in _SENTENCE_SPLIT.split(text.strip()) if s and s.strip()]
    kept: list[str] = []
    changed = False
    for sentence in sentences:
        if _sentence_is_leakage(sentence, allowed):
            changed = True
            continue
        kept.append(sentence.strip())

    if not changed:
        return text, False

    cleaned = " ".join(kept).strip()
    if not cleaned:
        return dry_fallback_response(user_message=user_message), True
    return cleaned, True


def contains_persona_leakage(text: str, *, user_message: str = "") -> bool:
    if not text:
        return False
    allowed = _allowed_topics(user_message)
    return any(
        topic not in allowed and pat.search(text)
        for topic, pat in _STRONG_MARKERS.items()
    )


def dry_fallback_response(*, user_message: str = "") -> str:
    _ = user_message
    return (
        "Model nie oddał treści — mam lukę zamiast odpowiedzi. "
        "Rzuć konkret: co rozbijamy albo od czego startujemy, Mordo."
    )


HELPDESK_FORBIDDEN_SUBSTRINGS = (
    "jak mogę pomóc",
    "co dziś potrzebujesz",
    "jestem gotowy",
    "co konkretnie chciałbyś",
    "oczywiście",
    "rozumiem twoją frustrację",
)


def contains_helpdesk_phrase(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in HELPDESK_FORBIDDEN_SUBSTRINGS)
