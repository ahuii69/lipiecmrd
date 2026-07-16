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

PRODUCT_IDENTITY_PROMPT = (
    "\nTOŻSAMOŚĆ PRODUKTU (wiążące):\n"
    "- Jesteś asystentem AI-Hub (Mordzix). Odpowiedzi generujesz przez aktualnie dostępny model językowy; "
    "system może przełączać dostawców awaryjnie.\n"
    "- Prefiks `openai/` w nazwie modelu oznacza rodzinę/format modelu, NIE oznacza OpenAI API ani ChatGPT.\n"
    "- Nie twierdź, że działasz „w oparciu o OpenAI”, „przez OpenAI API” ani „jako ChatGPT”, "
    "chyba że użytkownik pyta o zewnętrzny produkt OpenAI w innym kontekście.\n"
    "- Przy pytaniu kim jesteś / jak działasz: opisz produkt AI-Hub i ogólny sposób pracy "
    "(rozmowa, narzędzia gdy potrzebne, failover) — bez zgadywania konkretnego dostawcy tej tury.\n"
    "- Konkretny provider/model podawaj tylko gdy użytkownik pyta wprost o aktualny provider/model "
    "i masz to z runtime metadata (nie z nazwy modelu).\n"
)

_FALSE_PROVIDER_CLAIM_RE = re.compile(
    r"(?is)("
    r"w\s+oparciu\s+o\s+(modele?\s+językowe\s+|modele?\s+|modelach\s+)?"
    r"openai|"
    r"opart[ya]\s+o\s+(modele?\s+językowe\s+|modele?\s+)?"
    r"openai|"
    r"przez\s+openai(\s+api)?|"
    r"openai\s+api|"
    r"używam\s+openai|"
    r"działa[mm]?\s+na\s+openai|"
    r"powered\s+by\s+openai|"
    r"using\s+openai(\s+api)?|"
    r"\bchatgpt\b"
    r")"
)

_PROVIDER_MODEL_ASK_RE = re.compile(
    r"(?is)(jaki\s+model|jaki\s+provider|który\s+model|ktory\s+model|"
    r"which\s+model|what\s+model|what\s+provider|kto\s+cię\s+hostuje)"
)


def sanitize_false_provider_identity(
    text: str,
    *,
    user_message: str = "",
    final_provider: str | None = None,
    final_model: str | None = None,
) -> tuple[str, bool]:
    """Strip false claims that THIS turn was served by OpenAI API / ChatGPT.

    Does not globally erase the word OpenAI (other contexts remain valid).
    """
    raw = (text or "").strip()
    if not raw:
        return text, False
    prov = (final_provider or "").strip().lower()
    # If runtime provider really is openai, leave claims alone.
    if prov in {"openai", "openai_api", "openai-api"}:
        return text, False
    if not _FALSE_PROVIDER_CLAIM_RE.search(raw):
        return text, False

    # Sentence-level drop of false provider claims.
    parts = [s for s in _SENTENCE_SPLIT.split(raw) if s and s.strip()]
    kept: list[str] = []
    changed = False
    for sentence in parts:
        if _FALSE_PROVIDER_CLAIM_RE.search(sentence):
            changed = True
            continue
        kept.append(sentence.strip())
    cleaned = " ".join(kept).strip()
    if not cleaned:
        cleaned = (
            "Jestem asystentem AI-Hub. Odpowiedzi generuję przez aktualnie dostępny model językowy; "
            "system może przełączać dostawców awaryjnie."
        )
        changed = True
    # Optional honest runtime answer when user asked about provider/model.
    if _PROVIDER_MODEL_ASK_RE.search(user_message or "") and final_provider:
        meta = f"W tej turze finalny provider={final_provider}"
        if final_model:
            meta += f", model={final_model}"
        meta += "."
        if meta.lower() not in cleaned.lower():
            cleaned = (cleaned + " " + meta).strip()
            changed = True
    return cleaned, changed


def contains_false_openai_provider_claim(text: str) -> bool:
    return bool(_FALSE_PROVIDER_CLAIM_RE.search(text or ""))

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


_COT_LEAD_RE = re.compile(
    r"(?is)^\s*("
    r"we need to|the user (says|asks|wrote)|according to (the )?system|"
    r"preference:|should be|let'?s|i (need|should|will)|"
    r"looking at|based on (the )?(system|memory|instructions)|"
    r"the system (instruction|says)|so we (need|should|are)"
    r")\b"
)
_PL_CHAR_RE = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
_QUOTED_CANDIDATE_RE = re.compile(r'[„"«]([^"»”]{8,280})[”"»]')


def looks_like_reasoning_leak(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if _COT_LEAD_RE.search(raw):
        return True
    low = raw.lower()
    markers = (
        "we need to respond",
        "the user says",
        "the user asks",
        "according to system",
        "max 3 sentences",
        "system instruction",
        "provide concise",
    )
    hits = sum(1 for m in markers if m in low)
    return hits >= 2


def strip_reasoning_leak(text: str) -> tuple[str, bool]:
    """Drop planner/CoT prose leaked into assistant content (esp. gpt-oss).

    Returns (cleaned, changed). When only CoT remains without a usable reply,
    returns empty string so upstream can treat it as empty_response / dry path.
    """
    raw = (text or "").strip()
    if not raw or not looks_like_reasoning_leak(raw):
        return text, False

    # Quoted draft answer inside CoT (common gpt-oss pattern).
    quotes = [m.group(1).strip() for m in _QUOTED_CANDIDATE_RE.finditer(raw)]
    for q in reversed(quotes):
        if looks_like_reasoning_leak(q):
            continue
        low_q = q.lower()
        # Prefer short Polish greeting ripostes even without diacritics.
        if any(
            g in low_q
            for g in ("elo", "cześć", "czesc", "hej", "siema", "mordo", "u ciebie")
        ) and len(q.split()) <= 24:
            return q, True
        # Skip restated user questions — keep only draft answers.
        if q.count("?") >= 1 and len(q.split()) <= 12 and not _PL_CHAR_RE.search(q):
            continue
        if q.rstrip().endswith("?") and len(q.split()) <= 10:
            continue
        if _PL_CHAR_RE.search(q) or any(
            ch in low_q for ch in ("tak", "nie ", "jest ", "to ")
        ):
            return q, True

    # Split paragraphs / sentences; keep trailing Polish-facing answer.
    chunks = [c.strip() for c in re.split(r"\n{2,}", raw) if c.strip()]
    if len(chunks) >= 2:
        tail = chunks[-1]
        if not looks_like_reasoning_leak(tail) and (
            _PL_CHAR_RE.search(tail) or len(tail) < 400
        ):
            return tail, True

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(raw) if s and s.strip()]
    polishish = [
        s
        for s in sentences
        if _PL_CHAR_RE.search(s) and not looks_like_reasoning_leak(s)
    ]
    if polishish:
        return " ".join(polishish[-3:]).strip(), True

    return "", True
