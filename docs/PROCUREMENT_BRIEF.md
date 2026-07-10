# Brief dla działu zakupów / bezpieczeństwa (AI-Hub)

Krótki, **faktograficzny** opis pod RFI / checklistę vendor risk. **Nie jest poradą prawną ani certyfikatem.**

## Model wdrożenia

- **On-premise / private VPS:** oprogramowanie działa **u klienta**; dane konwersacji i baza SQLite zwykle **nie opuszczają** infrastruktury klienta, poza tym, co klient świadomie wysyła do **zewnętrznych API** (patrz niżej).

## Procesory / usługi zewnętrzne (typowe)

Konfigurowane kluczami w `.env` — klient decyduje, które włączyć:

| Obszar | Przykładowy typ usługi | Dane potencjalnie wychodzące |
|--------|-------------------------|------------------------------|
| LLM | np. DeepInfra / inny provider OpenAI-compatible | Treść promptów, historia z kontekstu wysłanego do modelu |
| Embeddingi | np. Voyage | Fragmenty tekstu wysłane do embeddingu |
| Wyszukiwarka | np. Brave (research) | Zapytania wyszukiwania |

Szczegóły env: [docs/ENV.md](ENV.md), web: [WEB.md](../WEB.md).

## Magazyn danych

- Domyślnie **SQLite** (plik na dysku klienta): [DATABASE_PRODUCTION.md](DATABASE_PRODUCTION.md).
- **Vault użytkownika:** sekrety szyfrowane (Fernet); klucz `AIHUB_USER_VAULT_KEY` — utrata = brak odszyfrowania: [VAULT.md](../VAULT.md).

## Kontrakt API (do audytu integracji)

- Interaktywnie: `GET /docs`, `GET /openapi.json` na uruchomionym backendzie.
- **Statyczny zrzut** na potrzeby oferty / załącznika: [scripts/dump_openapi.sh](../scripts/dump_openapi.sh) → plik `export/openapi.json` (generowany lokalnie, domyślnie poza gitem).

## Dowód jakości (regresja)

- Zestaw testów automatycznych (pytest) + CI (build frontend + Playwright): [README.md](../README.md).
- Jedna komenda sprawdzenia health po starcie: [scripts/health_check_all.sh](../scripts/health_check_all.sh).

## Lista zależności (SBOM-light)

- Zamrożenie wersji pakietów Python pod audyt: [scripts/sbom_python_freeze.sh](../scripts/sbom_python_freeze.sh) → `export/requirements-freeze.txt`.

## Ograniczenia (transparentnie)

- Brak wbudowanego **SLA** — zależy od środowiska klienta.
- **RODO / HIPAA / ISO** — wymagają analizy i umów po stronie klienta; produkt nie „certyfikuje” procesu.
