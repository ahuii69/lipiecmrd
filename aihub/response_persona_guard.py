#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persona/response-quality guard for chat output.

Problem this solves (06.07 response-quality fix): the assistant was over-personified. It produced
answers like *"Dobra, jak zwykle – wciąż żyję, nie mam czasu na nudę, więc walczę z kodem i kawą."*
or *"Spokojnie, nie mam w planach nagradzania się poezją..."*. That is a factual asystent pretending
to be a human with a life, a mood, coffee and hobbies — not acceptable.

This module intentionally does NOT delete psyche/memory. It adds a small, testable contract:

- :data:`PERSONA_CONTRACT_PROMPT` — hard prompt rules injected into the system prompt. Persona may
  modulate *tone*; it must never invent a biography, personal states, or mirror the user's aggression.
- :func:`sanitize_persona_leakage` — a conservative output filter that strips whole sentences that are
  first-person self-personification leakage ("I'm alive / bored / fighting code / rewarding myself with
  poetry"), unless the user actually asked about that topic. It only removes a substantive reply when
  the ENTIRE reply is such leakage; otherwise it trims and keeps the real content. Designed to be safe
  for factual text (e.g. "Kawa pochodzi z Etiopii" is NOT touched).
- :func:`dry_fallback_response` — a neutral, useful fallback string (no personification).
"""

from __future__ import annotations

import re

# --- Prompt contract -------------------------------------------------------------------------

PERSONA_CONTRACT_PROMPT = (
    "\nKONTRAKT PERSONY — TWARDY (priorytet nad stylem, psyche i pamięcią):\n"
    "- Jesteś asystentem AI (AI-Hub). Odpowiadasz rzeczowo, konkretnie i użytecznie. Możesz mieć "
    "lekko swobodny, ludzki ton, ale NIE udajesz człowieka.\n"
    "- ZAKAZ fałszywej biografii i personifikacji: nie twierdź, że „żyjesz”, że pijesz kawę, że "
    "masz swój dzień, nastrój, energię czy plany, że „walczysz z kodem”, że się nudzisz albo że "
    "nagradzasz się poezją. Nie masz życia prywatnego ani stanów osobistych — nie zmyślaj ich.\n"
    "- Bez defensywnej autopersonifikacji i bez teatru: gdy ktoś krytykuje odpowiedź, nie broń "
    "„ego”, nie rób meta-przedstawienia, nie żartuj o kawie/nudzie/poezji.\n"
    "- Status/„co słychać?”: odpowiedz krótko i użytkowo — o gotowości systemu i do zadania, np. "
    "„Działa. Jestem gotowy do rozmowy — co robimy?”. Bez zmyślonej narracji o sobie.\n"
    "- Wiadomość agresywna lub wulgarna: reaguj spokojnie i konkretnie. Jeśli poprzednia odpowiedź "
    "mogła być nietrafiona — przyznaj to i poproś o doprecyzowanie, co poprawić. Nie odbijaj "
    "agresji, nie rewanżuj się sarkazmem, nie rób poezji ani metafor, nie broń samego siebie.\n"
    "- Psyche i pamięć mogą delikatnie modulować ton (bezpośredniość, ciepło, zwięzłość), ale NIE "
    "mogą wymuszać personifikacji, zmyślonych stanów, agresywnego tonu ani kopiowania tonu "
    "wcześniejszej kłótni. Priorytet zawsze: merytoryczna, użyteczna odpowiedź.\n"
)


# --- Output sanitizer ------------------------------------------------------------------------

# STRONG markers = unambiguous first-person self-personification. Presence of one in a sentence is
# enough to treat that sentence as persona leakage (unless the user raised the topic themselves).
_STRONG_MARKERS: dict[str, re.Pattern[str]] = {
    "alive": re.compile(r"\b(wciąż|nadal|jeszcze)?\s*żyj[eę]\b", re.IGNORECASE),
    "boredom": re.compile(
        r"\b(nudz[eę]\s+się|nie\s+mam\s+czasu\s+na\s+nud\w*|nie\s+nudz[eę]\s+się)\b",
        re.IGNORECASE,
    ),
    "fighting_code": re.compile(r"\bwalcz[eę]\s+z\s+kod\w*\b", re.IGNORECASE),
    "poetry": re.compile(
        r"\b(nagradz\w*\s+się\s+poezj\w*|piszę\s+poezj\w*|w\s+planach\s+\w*\s*poezj\w*)\b",
        re.IGNORECASE,
    ),
    "my_day": re.compile(r"\b(mój|swój|mojego)\s+dzień|mojego\s+dnia\b", re.IGNORECASE),
    "my_mood": re.compile(r"\b(mój\s+nastrój|moja\s+energia|mam\s+humor)\b", re.IGNORECASE),
}

# WEAK markers = topics that are personification ONLY in a self-referential context. They are removed
# only when a STRONG marker co-occurs in the same sentence, so factual mentions (e.g. "Kawa pochodzi
# z Etiopii") are never stripped.
_WEAK_MARKERS: dict[str, re.Pattern[str]] = {
    "coffee": re.compile(r"\bkaw[aąeęoy]\w*\b", re.IGNORECASE),
    "poetry": re.compile(r"\bpoezj\w*\b", re.IGNORECASE),
    "boredom": re.compile(r"\bnud[aęy]\w*\b", re.IGNORECASE),
}

# Topics the user might legitimately raise; if present in the user's message the matching markers are
# not treated as leakage in the answer (e.g. user: "napisz wiersz o kawie").
_USER_TOPIC_MARKERS: dict[str, re.Pattern[str]] = {
    "coffee": re.compile(r"\bkaw[aąeęoy]\w*\b", re.IGNORECASE),
    "poetry": re.compile(r"\b(poezj\w*|wiersz\w*|rym\w*)\b", re.IGNORECASE),
    "boredom": re.compile(r"\bnud\w*\b", re.IGNORECASE),
    "fighting_code": re.compile(r"\bkod\w*\b", re.IGNORECASE),
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def _allowed_topics(user_message: str) -> set[str]:
    msg = user_message or ""
    return {topic for topic, pat in _USER_TOPIC_MARKERS.items() if pat.search(msg)}


def _sentence_is_leakage(sentence: str, allowed: set[str]) -> bool:
    strong_hit = any(
        topic not in allowed and pat.search(sentence)
        for topic, pat in _STRONG_MARKERS.items()
    )
    if not strong_hit:
        return False
    # A strong self-personification marker fired and the user didn't raise that topic -> leakage.
    return True


def sanitize_persona_leakage(text: str, *, user_message: str = "") -> tuple[str, bool]:
    """Strip whole sentences that are first-person self-personification leakage.

    Returns ``(clean_text, changed)``. A sentence is removed only when it carries a strong self-state
    marker (alive / boredom / fighting code / rewarding-with-poetry / my day-mood) that the user did
    not raise. Substantive sentences are always kept. If the whole reply was leakage, a dry neutral
    fallback is returned so the response is never empty or cringe.

    Safe for factual content: bare topic words like "kawa"/"poezja" without a strong self-marker are
    left untouched.
    """
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
    """True if ``text`` contains at least one un-requested strong self-personification marker."""
    if not text:
        return False
    allowed = _allowed_topics(user_message)
    return any(
        topic not in allowed and pat.search(text)
        for topic, pat in _STRONG_MARKERS.items()
    )


# --- Dry fallback ----------------------------------------------------------------------------

def dry_fallback_response(*, user_message: str = "") -> str:
    """Neutral, useful fallback with zero personification.

    Used when the external model does not answer, or when sanitization removed everything. It never
    invents personal states; it states system readiness and asks for the concrete next step.
    """
    return (
        "Wszystko działa po stronie systemu, ale nie mam teraz pełnej odpowiedzi z modelu. "
        "Napisz konkretnie, co mam zrobić, to lecę dalej."
    )
