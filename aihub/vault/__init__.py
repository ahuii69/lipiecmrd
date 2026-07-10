# -*- coding: utf-8 -*-
"""
Pakiet vault (User Secret Plane).

Ten plik jest celowo lekki — **nie** importuje ``service`` ani ``user_vault``,
żeby ``from aihub.vault.transcript`` / ``.patterns`` nie wymuszało ``cryptography``
przy starcie aplikacji.

API produkcyjne:
  - ``aihub.vault.service`` — ``try_vault_turn``, ``classify_vault_intent``
  - ``aihub.vault.firewall`` — blokada retrievalu haseł
  - ``aihub.vault.transcript`` — redakcja sesji
"""
