#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kontrakty wyjątków warstwy pamięci (STM / L1-L2 / wektor)."""

from __future__ import annotations


class MemoryUserIdRequiredError(ValueError):
    """Brak identyfikatora użytkownika przy operacji wymagającej izolacji danych."""

    def __init__(self, message: str = "user_id is required") -> None:
        super().__init__(message)


class MemoryVectorWriteError(RuntimeError):
    """Nie udało się utrwalić wektora w indeksie osadzeń po stronie runtime."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


def require_user_id(user_id: str | None) -> str:
    """Zwraca niepusty ``user_id`` lub podnosi :class:`MemoryUserIdRequiredError`."""
    uid = (user_id or "").strip()
    if not uid:
        raise MemoryUserIdRequiredError()
    return uid
