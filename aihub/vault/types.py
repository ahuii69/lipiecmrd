# -*- coding: utf-8 -*-
"""Typy zamkniętej warstwy vault — zero zależności od czatu ani pamięci."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VaultOp = Literal["store", "read", "delete", "list"]


@dataclass(frozen=True, slots=True)
class VaultTurnOutcome:
    """Wynik deterministycznej tury vault (tekst dla użytkownika + etykieta operacji)."""

    response_text: str
    operation: VaultOp
