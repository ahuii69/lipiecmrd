# Ryzyka i ograniczenia (product)

## Techniczne

| Ryzyko | Opis | Mitygacja |
|--------|------|-----------|
| **Zależność od LLM** | Jakość i dostępność zewnętrznego providera | Fallbacki, limity retry, monitoring trace |
| **Koszt API** | Tokeny + search + embedding | Limity, cache polityk, świadomy dobór modeli |
| **SQLite** | Wąskie gardło zapisu / brak HA | Migracja do Postgres dla produkcji wielowęzłowej |
| **Sekrety vaultu** | Klucz `AIHUB_USER_VAULT_KEY` — utrata = utrata danych **ORAZ** brak ustawienia klucza w `ENV=production` = błąd startu (od naprawy 06.07 — patrz `aihub/config.py::_validate_production_secrets`); w dev/test bez klucza aplikacja używa jawnie oznaczonego, nieprodukcyjnego fallbacku deterministycznego (`aihub/user_vault.py::_fernet`), logowanego jednorazowym ostrzeżeniem | Secret manager, rotacja procedur, hard-fail startu w produkcji |
| **Prompt injection** | Użytkownik manipuluje instrukcjami | Polityki, sandbox narzędzi, audyt |

## Prawne i compliance

- Repozytorium **nie stanowi porady prawnej**. RODO/GDPR, umowy powierzenia, DPIA — po stronie wdrożenia.
- Licencja **All Rights Reserved** — nieuprawnione użycie = naruszenie praw autorskich.

## Operacyjne

- Smoke i `final_runtime_gate` wymagają **kluczy i sieci** — fałszywe negatywy w CI bez sekretów.
- Logi mogą zawierać metadane; **nie** loguj treści vault read w plaintext (kod dąży do redakcji transkryptu).

## Co nadal może „paść”

- Playwright E2E zależny od buildu Next i przeglądarki.
- Testy integracyjne z żywym LLM — flaky.
- Zewnętrzne URL w testach web (404, rate limit).
