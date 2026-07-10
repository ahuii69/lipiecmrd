#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Produktowe bramki czatu AI-Hub (experience / eskalacja).

Nie eskalujemy i nie twardo blokujemy tylko dlatego, że użytkownik:
  - użył słów „hasło”, „kod”, „token”, „sekret”;
  - testuje pamięć lub pyta o początek / wcześniejsze wiadomości w sesji;
  - wyraźnie korzysta z vault (zapis/odczyt/usunięcie sekretu).

Sekrety na wyraźną prośbę: wyłącznie zaszyfrowany vault — nie STM/LTM/embeddings.
"""

from __future__ import annotations

import re
from typing import Final

from aihub.chat_image_generation import is_image_generation_intent
from aihub.vault.patterns import LIST_KEYS

# --- Wykrywanie tur „niskiego ryzyka” — bez eskalacji experience/policy blockerów ---

_VAULT_STORE_HINT: Final[re.Pattern[str]] = re.compile(
    r"(?:zapamiętaj|zapisz)\s+(?:hasło|haslo|hasła|hasla|kod|token|sekret)\b",
    re.IGNORECASE,
)
_VAULT_READ_HINT: Final[re.Pattern[str]] = re.compile(
    r"(?:podaj|daj|pokaż|pokaz|jaki\s+mam|zweryfikuj|odczytaj|wczytaj)\s+"
    r"(?:hasło|haslo|kod|token)\b",
    re.IGNORECASE,
)
_VAULT_DELETE_HINT: Final[re.Pattern[str]] = re.compile(
    r"(?:usuń|usun|zapomnij|skasuj)\s+(?:hasło|haslo|kod|token|sekret)\b",
    re.IGNORECASE,
)
_SESSION_META_HINT: Final[re.Pattern[str]] = re.compile(
    r"(co\s+pisałem\s+na\s+początku|"
    r"co\s+pisałem\s+(wyżej|wyzej)|"
    r"co\s+napisałem\s+(wyżej|wyzej)|"
    r"co\s+było\s+(wyżej|wyzej)|"
    r"pierwsz[ąa]\s+wiadomo|"
    r"początku\s+(tej\s+)?rozmow|"
    r"jak\s+zacząłem\s+rozmow|"
    r"kilka\s+wiadomości\s*(wyżej|wyzej)|"
    r"\d+\s*wiadomości\s*(wyżej|wyzej)|"
    r"co\s+było\s+wcześniej|"
    r"wcześniejsz(e|ą)\s+wiadomo)",
    re.IGNORECASE,
)
_MEMORY_PLAY_HINT: Final[re.Pattern[str]] = re.compile(
    r"(test\s+pamięci|zapamiętaj\s+to\b|sprawdź\s+pamięć|sprawdz\s+pamiec)",
    re.IGNORECASE,
)
MEMORY_FACT_RECALL_HINT: Final[re.Pattern[str]] = re.compile(
    r"(?is)(?:^|\n)\s*("
    r"co\s+wiesz\s+o|jaka\s+jest|jaki\s+jest|jaki\s+to|jaką\s+wartość|jaki\s+był|"
    r"jakie\s+było|jakie\s+jest|"
    r"ile\s+wynosi|ile\s+mam|kiedy\s+to|gdzie\s+jest|w\s+którym\s+roku|"
    r"jak\s+nazywa\s+się|jak\s+brzmi|jak\s+się\s+nazywa|"
    r"czy\s+pamiętasz|czy\s+to\s+prawda(?:\s+że|\s*,\s*że)?|"
    r"przypomnij|co\s+to\s+jest|podaj\s+nazwę|kto\s+to\s+jest|"
    r"o\s+czym\s+rozmawialiśmy|jak\s+nazywał(?:a|e)?\s+się"
    r")\b",
)


def skip_experience_derived_gates(user_message: str) -> bool:
    """True → nie stosuj experience blocker / eskalacji z experience do hard block."""
    t = (user_message or "").strip()
    if not t:
        return False
    if is_image_generation_intent(t):
        return True
    if (
        _VAULT_STORE_HINT.search(t)
        or _VAULT_READ_HINT.search(t)
        or _VAULT_DELETE_HINT.search(t)
        or LIST_KEYS.search(t)
    ):
        return True
    if _SESSION_META_HINT.search(t):
        return True
    if _MEMORY_PLAY_HINT.search(t):
        return True
    return False


def skip_experience_blocker_escalation(user_message: str) -> bool:
    """Szerszy skip niż ``skip_experience_derived_gates`` — także proste pytania o jeden fakt z pamięci."""
    if skip_experience_derived_gates(user_message):
        return True
    t = (user_message or "").strip()
    if len(t) > 280:
        return False
    return bool(MEMORY_FACT_RECALL_HINT.search(t))


# --- Globalna warstwa anty-halucynacyjna (runtime post-process) ----------------

ANTI_HALLUCINATION_CLAMP_MESSAGE: Final[str] = (
    "Nie mam tych danych — podaj szczegóły."
)

_GLOBAL_PROMPT_BLOCK: Final[str] = (
    "GLOBAL — najwyższy priorytet (wszystkie tryby: copy, rewrite, Q&A, technical):\n"
    "NIE WOLNO: wymyślać danych; dopisywać parametrów; zgadywać braków.\n"
    "JEŚLI BRAK DANYCH: użyj dokładnie „BRAK DANYCH” albo dopytaj o brakujący konkret.\n\n"
)


def global_anti_hallucination_prompt_prefix() -> str:
    """Fragment system promptu — najwyższa priorytetyzacja; importowany przez ``chat_runtime``."""
    return _GLOBAL_PROMPT_BLOCK

_YEAR_RE: Final[re.Pattern[str]] = re.compile(r"\b(19|20)\d{2}\b")
_POWER_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(\d{2,4})\s*(?:KM|kW|kM|HP|hp)\b",
    re.IGNORECASE,
)
_PRICE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(\d{3,7})\s*(?:zł|złotych|PLN|pln)\b",
    re.IGNORECASE,
)
_MILEAGE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(\d{1,3}(?:[ \u00A0]\d{3})+|\d{5,7})\s*km\b",
    re.IGNORECASE,
)
_MILEAGE_TYS_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(\d{1,3})\s*(?:tys\.?|tyś\.?)\s*\.?\s*km\b",
    re.IGNORECASE,
)
_LITER_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(\d{1,2}[.,]\d)\s*(?:l|L)\b",
)
_CC_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(\d{3,4})\s*(?:cm³|ccm|cc)\b",
    re.IGNORECASE,
)
_TORQUE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(\d{2,4})\s*Nm\b",
    re.IGNORECASE,
)
_FEATURE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    r"diesel|benzyna|benzynowy|dieslowy|hybryda|hybrydowy|"
    r"awd|4x4|lpg|cng|biturbo|"
    r"automatyczna\s+skrzynia|manualna\s+skrzynia|"
    r"napęd\s+na\s+cztery|naped\s+na\s+cztery|"
    r"turbo\s+diesel"
    r")\b",
    re.IGNORECASE,
)


def _token_in_corpus(token: str, corpus: str) -> bool:
    if not token or not corpus:
        return False
    esc = re.escape(token.strip())
    return bool(re.search(rf"(?<!\d){esc}(?!\d)", corpus))


def _feature_token_in_corpus(feature: str, corpus: str) -> bool:
    f = feature.strip().lower()
    if not f:
        return False
    c = corpus.lower()
    if f in c:
        return True
    # odmiany / złożenia: „diesel” vs „diesla”
    stem = re.sub(r"(owy|owa|owe|ych|ego|emu|em|a|ie)$", "", f)
    if len(stem) >= 4 and stem in c:
        return True
    return False


def _assistant_has_speculative_product_signals(assistant_text: str) -> bool:
    t = assistant_text or ""
    if not t.strip():
        return False
    if _YEAR_RE.search(t):
        return True
    if _POWER_RE.search(t):
        return True
    if _PRICE_RE.search(t):
        return True
    if _MILEAGE_RE.search(t) or _MILEAGE_TYS_RE.search(t):
        return True
    if _LITER_RE.search(t) or _CC_RE.search(t):
        return True
    if _TORQUE_RE.search(t):
        return True
    if _FEATURE_RE.search(t):
        return True
    return False


def _speculative_signals_grounded_in_corpus(assistant_text: str, corpus: str) -> bool:
    """Zwraca True tylko wtedy, gdy każdy wykryty sygnał ma pokrycie w korpusie użytkownika."""
    a = assistant_text or ""
    c = corpus or ""

    for m in _YEAR_RE.finditer(a):
        y = m.group(0)
        if not _token_in_corpus(y, c):
            return False

    for m in _POWER_RE.finditer(a):
        num = m.group(1)
        if not _token_in_corpus(num, c):
            return False

    for m in _PRICE_RE.finditer(a):
        num = m.group(1)
        if not _token_in_corpus(num, c):
            return False

    for m in _MILEAGE_RE.finditer(a):
        raw = m.group(1)
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 4 and digits not in re.sub(r"\D", "", c):
            return False

    for m in _MILEAGE_TYS_RE.finditer(a):
        num = m.group(1)
        if not _token_in_corpus(num, c):
            return False

    for m in _LITER_RE.finditer(a):
        lit = m.group(1).replace(",", ".")
        if lit not in c.replace(",", "."):
            return False

    for m in _CC_RE.finditer(a):
        num = m.group(1)
        if not _token_in_corpus(num, c):
            return False

    for m in _TORQUE_RE.finditer(a):
        num = m.group(1)
        if not _token_in_corpus(num, c):
            return False

    for m in _FEATURE_RE.finditer(a):
        feat = m.group(0)
        if not _feature_token_in_corpus(feat, c):
            return False

    return True


def clamp_ungrounded_speculative_reply(
    user_message: str,
    assistant_text: str,
    *,
    history_user_messages: list[str] | None = None,
    skip_clamp: bool = False,
) -> tuple[str, str | None]:
    """Twardy override: liczby/cechy ofertowe bez pokrycia w treści użytkownika → stały komunikat.

    ``skip_clamp=True`` gdy wynik pochodzi z zweryfikowanych narzędzi (grounding_mode tool_verified).
    Zwraca ``(tekst, powód_klampu|None)``.
    """
    if skip_clamp:
        return (assistant_text or "").strip(), None
    text = (assistant_text or "").strip()
    if not text:
        return text, None
    if text == ANTI_HALLUCINATION_CLAMP_MESSAGE:
        return text, None
    if not _assistant_has_speculative_product_signals(text):
        return text, None

    parts = list(history_user_messages or [])
    um = (user_message or "").strip()
    if um:
        parts.append(um)
    corpus = re.sub(r"\s+", " ", " ".join(parts)).strip().lower()

    if _speculative_signals_grounded_in_corpus(text, corpus):
        return text, None
    return ANTI_HALLUCINATION_CLAMP_MESSAGE, "ungrounded_specs_or_numbers"
