# -*- coding: utf-8 -*-
"""Wzorce NLU dla vault — skompilowane raz, udokumentowane, bez efektów ubocznych."""

from __future__ import annotations

import re
from typing import Final

# --- Zapis (hasło/kod/token do aliasu lub sekret alias: wartość) ---
STORE_CREDENTIAL: Final[re.Pattern[str]] = re.compile(
    r"(?is)(?:zapamiętaj|zapisz)\s+(?:hasło|haslo|hasła|hasla|kod|token)\s+"
    r"(?:do|dla)\s+([^:\n]+?)\s*:\s*(.+)\s*$"
)
# Ten sam zapis bez słów „do/dla”: „zapisz hasło github: tajne”
STORE_CREDENTIAL_INLINE: Final[re.Pattern[str]] = re.compile(
    r"(?is)(?:zapamiętaj|zapisz)\s+(?:hasło|haslo|hasła|hasla|kod|token)\s+"
    r"([^\s:]{1,200})\s*:\s*(.+)\s*$"
)
STORE_SECRET: Final[re.Pattern[str]] = re.compile(
    r"(?is)(?:zapamiętaj|zapisz)\s+sekret\s+([^:\n]+?)\s*:\s*(.+)\s*$"
)
# Intent zapisu bez poprawnej składni — fallback zapisuje całą wiadomość (patrz ``try_vault_turn``).
STORE_BROAD_FALLBACK: Final[re.Pattern[str]] = re.compile(
    r"(?is)(?:zapamiętaj|zapisz|schowaj)\b[\s\S]{0,200}\b(?:hasło|haslo|hasła|hasla|sekret|token|kod)\b"
)

# --- Odczyt / usunięcie ---
READ: Final[re.Pattern[str]] = re.compile(
    r"(?is)(?:podaj|daj|pokaż|pokaz|zweryfikuj|jaki\s+mam|odczytaj|wczytaj)\s+"
    r"(?:hasło|haslo|kod|token)\s+(?:do|dla)\s+(.+?)\s*$"
)
DELETE: Final[re.Pattern[str]] = re.compile(
    r"(?is)(?:usuń|usun|zapomnij|skasuj)\s+"
    r"(?:hasło|haslo|kod|token|sekret)\s+(?:do|dla)\s+(.+?)\s*$"
)

# --- Lista kluczy (meta danych, nie wartości) ---
LIST_KEYS: Final[re.Pattern[str]] = re.compile(
    r"(?is)(?:^|\n)\s*(?:jakie\s+mam\s+(?:klucze|aliasy)|"
    r"jakie\s+(?:klucze|aliasy)\s+mam|"
    r"wypisz\s+(?:klucze|aliasy|sekrety)|"
    r"pokaż\s+(?:klucze|aliasy)\s+(?:w\s+)?vaultu|"
    r"pokaz\s+(?:klucze|aliasy)\s+(?:w\s+)?vaultu|"
    r"lista\s+(?:kluczy|aliasów|aliasow)\s+(?:w\s+)?vault)\b"
)

# --- Firewall względem retrievalu „faktów” (pytanie o pamięć + leksykon poświadczeń) ---
CREDENTIAL_IN_USER_QUESTION: Final[re.Pattern[str]] = re.compile(
    r"(?i)(hasło|haslo|haśle|haśla|hasła|hasla|password|kod|token|sekret|pin|"
    r"klucz\s+api|api\s+key)"
)
