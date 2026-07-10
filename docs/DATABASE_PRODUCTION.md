# Baza danych — produkcja i PostgreSQL

## Stan obecny (repozytorium)

- Główny magazyn aplikacji to **SQLite** (`DB_PATH`, domyślnie `data/aihub.sqlite3`).
- Warstwa dostępu jest **ściśle związana z SQLite**: m.in. `sqlite3`, `PRAGMA`, **FTS5** i triggery synchronizujące FTS (`aihub/db.py`, `aihub/db/sqlite.py`).
- To jest **świadomy wybór** pod: development, demo, **pojedynczy VPS**, jeden proces writer.

## Kiedy SQLite wystarcza na produkcji

- Jedna instancja API za reverse proxy.
- Akceptowalny **pojedynczy writer** (typowy układ: uvicorn `workers=1` lub jedna maszyna).
- Backup i restore według [scripts/backup_sqlite_vps.sh](../scripts/backup_sqlite_vps.sh) + procedura w [VPS_HANDOFF_CHECKLIST.md](VPS_HANDOFF_CHECKLIST.md).

## Kiedy klient będzie wymagał PostgreSQL

- **Wiele instancji API** z jedną wspólną bazą (HA / skalowanie poziome).
- Wymogi **operacyjne** (replikacja, PITR, zarządzanie dostępem przez standardowe narzędzia DBA).
- Polityka firmy: „tylko serwerowy RDBMS”.

## Co oznacza migracja na PostgreSQL (realistycznie)

To **nie jest** zmiana `DB_PATH` na connection string. Wymaga m.in.:

1. **Warstwy abstrakcji** albo podwójnej implementacji repozytoriów dla wszystkich ścieżek zapisu/odczytu obecnie w `aihub/db.py` (duży moduł).
2. **Zastąpienia FTS5** — np. `tsvector` / `pg_trgm` + inna strategia indeksów niż triggery SQLite.
3. **Migracji schematu** wersjonowanych (np. Alembic) pod wdrożenia u klienta.
4. **Testów regresji** na obu backendach albo wyłącznie Postgres w trybie „enterprise”.

Szacunek dla due diligence: **wiele tygodni do kilku miesięcy** pracy doświadczonego zespołu, zależnie od zakresu (minimalny port vs pełna równoważność).

## Jak to sprzedawać dziś

- **Pilot / VPS / single-node:** „Produkcja na SQLite z WAL + backup” — zgodne z kodem.
- **Enterprise multi-instance:** „PostgreSQL jako osobny projekt migracji lub custom delivery” — bez obietnicy gotowego przełącznika w tym repozytorium.
