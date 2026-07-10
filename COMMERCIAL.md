# AI-Hub — skrót pod klienta B2B

Dokument dla **decydenta / zakupu**, nie dla dewelopera. Szczegóły techniczne: [README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md), [API.md](API.md).

## Co kupujesz

- **Backend FastAPI** — czat (`POST /chat/turn`), capabilities, sesje, pamięć (V2 zalecana), agent, vault użytkownika, narzędzia web/research, trace.
- **Frontend Cockpit (Next.js)** — panel operatorski + BFF; self-host u klienta.
- **Testy i bramy** — duży zestaw pytest, smoke runtime, E2E Playwright (mock + ścieżka „prawdziwy hub”).
- **Dokumentacja produktowa** — API, pamięć, vault, web, ryzyka, runbook.

## Pakiety ofertowe (ramy do negocjacji)

| Warstwa | Zawartość (typowo) |
|---------|-------------------|
| **Engine** | Kod + dokumentacja + testy; klient wdraża samodzielnie. |
| **Deploy** | Engine + checklista VPS, backup SQLite, skrypt health po starcie, wsparcie przy pierwszym `start.sh` / proxy. |
| **Enterprise readiness** | Deploy + artefakty pod zakupy: brief RFI, zrzut OpenAPI, freeze zależności Python — patrz [docs/PROCUREMENT_BRIEF.md](docs/PROCUREMENT_BRIEF.md). |

Artefakty dla działu zakupów (generowane u siebie przed wysyłką): [export/README.md](export/README.md).

## Dla kogo

- Zespół **platform / internal AI** (asystent wewnętrzny, dane zostają u was).
- **Integrator / software house** (wdrożenie u końcowego klienta).
- **Startup B2B** potrzebujący kontrolowanego huba zamiast samego „wrappera” na API.

## Model prawny

- Kod podlega **[LICENSE](LICENSE)** (wszystkie prawa zastrzeżone). Użycie komercyjne = **osobna umowa** z właścicielem praw.
- Szablon punktów do umowy (nie jest poradą prawną): [docs/templates/COMMERCIAL_LICENSE_OUTLINE.md](docs/templates/COMMERCIAL_LICENSE_OUTLINE.md).

## Wdrożenie (VPS)

- Typowa ścieżka: **jedna maszyna**, `./start.sh`, reverse proxy (np. Caddy), systemd — [docs/RUNBOOK.md](docs/RUNBOOK.md).
- Checklista przekazania po sprzedaży: [docs/VPS_HANDOFF_CHECKLIST.md](VPS_HANDOFF_CHECKLIST.md).
- Kopia zapasowa bazy (SQLite): [scripts/backup_sqlite_vps.sh](scripts/backup_sqlite_vps.sh).
- **Szybki dowód że stack żyje:** [scripts/health_check_all.sh](scripts/health_check_all.sh) (same backend: `SKIP_COCKPIT_CHECK=1`).

## Baza danych

- **Produkcja na jednym VPS:** domyślnie **SQLite** (WAL) — sensowny default pod pojedynczą instancję.
- **PostgreSQL:** nie jest w tej chwili podmieniony pod cały silnik; wymaga osobnego projektu migracji — [docs/DATABASE_PRODUCTION.md](docs/DATABASE_PRODUCTION.md).

## Czego ten produkt nie obiecuje

- SLA bez waszej infrastruktury i monitoringu.
- Zgodność HIPAA/RODO „z pudełka” — wymaga DPA, architektury i procesów u klienta.
- Brak kosztów tokenów LLM i API zewnętrznych — to koszt eksploatacji u klienta.

## Wersjonowanie

- Aktualna etykieta wersji: plik [VERSION](VERSION). Historia zmian: [CHANGELOG.md](CHANGELOG.md).
