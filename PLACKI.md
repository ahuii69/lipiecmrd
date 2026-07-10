# PLACKI — audyt: co jest prawdą, co udaje, co z czym gada (back ↔ front)

Dokument roboczy. Ostatnia aktualizacja: ręczny przegląd repo (nie zastępuje CI).

## 0. Przed release — co uruchomić lokalnie

**Kanoniczny gate (systemowy — backend + Cockpit):**

```bash
make release
```

albo: `bash scripts/release_gate.sh` — kolejno:

1. `make check` (`dev_gate`): sync allowlist ↔ manifest, `import aihub.main`, `scripts/check_pg_ready.py --soft` (Postgres tylko gdy `DB_BACKEND=postgres` w `.env`), krótkie `pytest` kanonu.
2. `PYTHONPATH=. pytest -q tests` — **pełna** paczka testów Pythona w `tests/`.
3. Opcjonalnie w CI / `CHECK_PG_STRICT=1`: `scripts/check_pg_ready.py` (bez `--soft`) po `dev_gate`.
4. `cockpit`: `npm ci` (gdy jest `package-lock.json`) lub `npm install`, potem `npm run build`, `npm run test`.

Alias: `make quality` → to samo co `make release`.

**Szybki skan bez pełnego pytest / bez buildu Next:**

```bash
make check
```

**PostgreSQL (gdy `DB_BACKEND=postgres`):** sprawdzenie `POSTGRES_DSN`, `psycopg2` i `SELECT 1`:

```bash
make check-pg
```

Import danych z plików SQLite przy starcie backendu: `aihub/sqlite_pg_import.py` (zmienne `AIHUB_SQLITE_IMPORT`, … w `.env.example`). Ręcznie: `python -m aihub.sqlite_pg_import`.

Szablon zmiennych (nie commituj prawdziwych sekretów): `.env.example`. Lokalny `.env` nie jest w repozytorium — nie podmieniamy go w skryptach.

## 1. Naprawy vs „maskowanie” (ostatnie zmiany wokół testów / pamięci)

| Zmiana | Typ | Uwagi |
|--------|-----|--------|
| `query_nodes(..., user_id=...)` w runtime (executive_controller, planner) | **Naprawa kontraktu** | Produkcja woła prawdziwy `knowledge_graph.query_nodes`; wymóg `user_id` jest sensowny, nie zaślepka. |
| Filtrowanie „echa” zapytania w `chat_deterministic` (`_norm_text`, pomijanie treści = pytanie) | **Naprawa logiki** | Bez tego STM mógł zwracać ten sam tekst co bieżące pytanie i deterministyczna ścieżka czytała „fakt” z własnego pytania. |
| Testy: stub `query_nodes` → `lambda _q, limit=8, **_: []` | **Izolacja testów, nie produkcja** | Monkeypatch tylko w `tests/*`; nie ukrywa zachowania backendu — synchronizuje sygnaturę ze ścieżką produkcyjną. |
| `canonical_http_surface.py` + test manifestu | **Spójność dokumentacji z `app`** | Endpointy istnieją na FastAPI; manifest nie „udaje” tras — brak wpisu = fail testu. |
| `AIHUB_ENV_FILE` / ingest / memory v2 HTTP (wcześniejsze PR-y w tej linii) | Mieszanka **logiki + kontraktów** | Oceniaj per plik w diff; ogólnie: albo realna ścieżka, albo testowany kontrakt. |

**Wniosek:** nie chodziło o gaszenie testów przez ściemnianie produkcji — krytyczne ścieżki to kontrakt `user_id` i realny filtr kandydatów pamięci. Jedyny „fałsz” to **zamockowane zależności w testach** (normalne).

## 2. Co jest niepodłączone lub celowo węższe niż pełny backend

| Element | Status |
|---------|--------|
| `aihub/db.py` | **SQLite (domyślnie) lub PostgreSQL** — `DB_BACKEND` + `POSTGRES_DSN`; `init_db` + bootstrap `postgres_bootstrap.sql`. Import ze starych plików SQLite: `sqlite_pg_import` przy starcie (env w `.env.example`). |
| `aihub/workers/consolidation` + `core/background` | **Wątek konsolidacji** `compat_router.mem` — włącz/wyłącz `AIHUB_CONSOLIDATION_WORKER`; start z `main` lifespan. |
| `aihub/api/*` (np. `web_router` itd.) | **Nie montowane** w `aihub.main` — opis w `aihub/api/_LEGACY.md`. Importowalne, ale nie są domyślną powierzchnią HTTP. |
| `POST /memory/add`, `/memory/search` (v1) | Mogą być wyłączone env (`AIHUB_DISABLE_LEGACY_MEMORY_V1_HTTP`) — kanon kieruje na Memory V2. |
| Skrypty gate (np. embedding) z gałęziami „stub” przy błędzie | Raportują strukturę failure — to diagnostyka, nie runtime chatu. |

## 3. Co z czym gada (kanon)

- **Źródło:** `docs/WIREUP_CALLGRAPH.md` — główne łańcuchy (agent, memory, retrieval).
- **Aktywne HTTP:** `aihub.main` → `include_router`: `admin`, `agent`, `chat`, `chat_sessions`, `cockpit`, `memory_v2`, `psyche_v2`, `security`, `self_heal_status` + trasy inline w `main.py` (m.in. `/web/fetch`, legacy memory, fs, …).
- **Lista tras (manifest):** `aihub/canonical_http_surface.py` + `docs/CANONICAL_HTTP_SURFACE.md`.

## 4. Back (FastAPI) ↔ front (Cockpit Next.js) — najważniejsze

**Jak to działa:**

1. UI woła **relatywne** URL-e pod `/api/aihub/...` (klient: `cockpit/lib/api/client.ts` → `buildAihubProxyUrl`).
2. Route handler: `cockpit/app/api/aihub/[...path]/route.ts` robi `fetch` do **`AIHUB_BASE_URL`** (domyślnie `http://127.0.0.1:8080`) + ścieżka backendu.
3. Nagłówki: proxy może wstrzyknąć `x-api-key` / token zgodnie z `resolve-hub-api-key` i `auth_patch` po stronie Pythona.

**Allowlista BFF (`cockpit/lib/api/cockpit-proxy-allowlist.json`):**

- Tylko wybrane metody+ścieżki mogą przejść przez proxy Cockpit; reszta → **403** z `cockpit-proxy-gate`.
- **Memory V2:** allowlista zawiera pełny zestaw tras zgodny z backendem: m.in. `retrieval-explain`, `forgetting`, `search`, `item`, `consolidate`, `autobio` (+ kompakt) obok summary/procedures/contradictions i operacji na item.
- **`apiClient`** (`cockpit/lib/api/client.ts`): metody m.in. `getMemoryV2RetrievalExplain`, `runMemoryV2ForgettingSweep`, `searchMemoryV2Raw`, `createMemoryV2ItemRaw`, `postMemoryV2Consolidate`, `getMemoryV2Autobio`, `postMemoryV2AutobioCompact`.
- **UI:** panel Memory (`features/memory/memory-panel.tsx`) — sekcja „Memory V2 — bezpośredni HTTP” (summary, forgetting sweep, retrieval-explain).

**Status połączenia:** BFF → backend dla Memory V2 jest **wpięty** po stronie Cockpit (lista + klient + fragment UI). Test Python `tests/test_cockpit_proxy_allowlist.py` wymaga, by każda pozycja allowlisty istniała w `CANONICAL_HTTP_ROUTES` (`aihub/canonical_http_surface.py`).

**Synchronizacja (check bez pełnego importu `main`):**

```bash
PYTHONPATH=. python3 scripts/check_allowlist_canonical_sync.py
```

(z katalogu głównego repozytorium `morda`)

Manifest kanoniczny musi zawierać m.in. trasy używane przez allowlistę (`GET /ops/health`, `POST /chat/stt`, pełny zestaw `/memory/v2/*`). Po dodaniu endpointu na backendzie: zaktualizuj `canonical_http_surface.py` (albo introspekcja z `pytest tests/test_canonical_http_surface.py` na działającym drzewie), potem allowlistę i ewentualnie `client.ts`.

## 5. Co jest w plikach, ale „nie wpięte” a warto rozważyć

- **Legacy `aihub/api`** — świadomie odłączone; wpinanie wymaga decyzji (konflikt tras, podwójne kontrakty) — patrz `_LEGACY.md`.
- **Tworzenie itemów / search V2 z UI** — metody `*Raw` w kliencie są; osobny formularz w panelu można dodać później (kontrakt Pydantic po stronie API).

## 6. Gdzie szukać dalej (bez duplikacji)

| Temat | Plik |
|-------|------|
| Web / narzędzia | `WEB.md` |
| Cockpit / proxy | `cockpit/README.md`, `cockpit/DEPLOYMENT.md` |
| Call graph | `docs/WIREUP_CALLGRAPH.md` |
| Manifest HTTP | `docs/CANONICAL_HTTP_SURFACE.md`, `aihub/canonical_http_surface.py` |
| Legacy API | `aihub/api/_LEGACY.md` |

## 7. Vitest (Cockpit) — priorytety

Konfiguracja: `cockpit/vitest.config.ts` (kolejność `include` = najpierw wyższy sens regresji dla kontraktu z hubem).

**Priorytet wysoki**

- `lib/api/**/*.test.ts` — allowlista BFF (`cockpit-proxy-gate`), rozwiązywanie klucza hub (`resolve-hub-api-key`).
- `lib/chat/**/*.test.ts` — payload historii, scroll, tytuł sesji.
- `lib/store/**/*.test.ts` — transkrypt / stan klienta.

**Priorytet niższy (szerszy harness)**

- `tests/**/*.test.ts` — scenariusze z uploadem / szerszym API w Node.

**Skróty npm (ten sam runner co `npm run test`, tylko podzbiór plików):**

- `npm run test:vitest-high` — tylko `lib/api`, `lib/chat`, `lib/store`.
- `npm run test:vitest-low` — tylko `tests/`.

Pełny gate frontendu nadal: `npm run test` w `cockpit/` (albo `make release` z katalogu głównego).

---

*„Łapie placki” = ten plik. Jak chcesz oficjalną nazwę (`BACK_FRONT.md`), zmień i commit.*
