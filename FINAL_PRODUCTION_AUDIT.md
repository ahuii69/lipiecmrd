# FINAL PRODUCTION AUDIT

**Data:** 2026-07-18  
**Repo:** `/home/ubuntu/mrd`  
**Werdykt:** **PRODUCTION READY**

---

## 1. Pierwszy błąd (znany) — przyczyna źródłowa

### Objaw

```
tests/conftest.py → isolated_db → importlib.reload(aihub.config)
→ aihub/config.py → _validate_production_secrets()
→ RuntimeError: missing AIHUB_API_KEY / HUB_API_KEY / API_KEY / AIHUB_PROXY_TOKEN
```

### Łańcuch przyczyny (nie „winny jest tylko config.py”)

1. Plik `.env` zawiera `ENV=production` (środowisko produkcyjne VPS).
2. `tests/conftest.py` **nie wymuszał** `ENV=test` przed importem `aihub.*` (używał tylko `setdefault` dla workerów/DB).
3. Przy pierwszym imporcie `aihub.config`:
   - `_load_local_env_file()` widzi brak / non-production `ENV` → ładuje `.env`,
   - `os.environ.setdefault("ENV", …)` ustawia `ENV=production` z `.env`,
   - `_validate_production_secrets()` przechodzi, bo sekrety z `.env` są obecne.
4. Fixture `isolated_db` (autouse) celowo **usuwa** hub auth keys (`HUB_KEY_ENV_NAMES`, `API_KEY=""`) dla izolacji testów auth/DB.
5. Następnie `importlib.reload(aihub.config)` przy **nadal** `ENV=production` → walidacja produkcyjna bez sekretów → crash całej suity.

### Naprawa źródłowa

`tests/conftest.py`:

- `os.environ["ENV"] = "test"` **przed** jakimkolwiek importem `aihub` (force, nie `setdefault`).
- W `isolated_db`: `monkeypatch.setenv("ENV", "test")` **przed** `importlib.reload(cfg)`.
- Dodatkowo domyślne wyłączenie live probe’ów health/embeddings w harnessie.

Kontrakt udokumentowany w docstringu `_validate_production_secrets()` (`aihub/config.py`).  
Testy produkcyjnych sekretów (`tests/test_config_truth.py`) nadal wywołują walidację **bezpośrednio** z `ENV=production` — bez regressji fail-fast.

---

## 2. Wyniki bramek

| Gate | Wynik |
|------|-------|
| Full pytest (bez `ENV=test` w shellu; nawet z `ENV=production` w shellu) | **1180 passed, 2 skipped** |
| `tests/test_config_truth.py` | **21 passed** |
| `scripts/release_audit.py` | **RESULT: OK** (module_collisions, duplicates, unfinished_markers, import_failures, route_duplicates) |
| Frontend `npm test` | **93 passed** (15 files) |
| Frontend `npm run typecheck` | **PASS** |
| Frontend `npm run lint` | **PASS** |
| Backend systemd | **active** |
| Frontend systemd | **active** |
| `GET /system/ping` | **200 / ok** |
| `GET /ops/ready` | **ready=true**, blocking=[], degraded=[] |
| `GET /openapi.json` | **200** |
| `GET /gpt-openapi.json` | **200** (live schema, nie pusty stub) |
| `GET /login` (frontend) | **200** |

Skipped (świadome, nie regresje):

1. `tests/test_port_contract.py` — brak `ENV_STATUS_CHECK.sh` w drzewie.
2. `tests/test_runtime_chat_fix_20.py` — wymaga `AIHUB_RUNTIME_PG_TEST=1` + Postgres.

---

## 3. Audyt atrap / martwego kodu / niewpiętych elementów

### Critical — naprawione w tej iteracji

| Problem | Status |
|---------|--------|
| Pytest crash na `_validate_production_secrets` przez dziedziczenie `ENV=production` z `.env` + reload bez sekretów | **FIXED** (`tests/conftest.py`) |

### Critical — skan AST / release audit (bieżący stan)

| Check | Wynik |
|-------|-------|
| `NotImplementedError` w `aihub/` | **0** |
| Bare `except:` w `aihub/` | **0** |
| Unfinished markers (release_audit) | **OK** |
| Module collisions / exact duplicates | **OK** |
| Import failures | **OK** |
| Route duplicates | **OK** |
| GPT OpenAPI empty-paths stub | **usunięty wcześniej** (live fallback; pokryte testami) |

### Important — obserwacje operacyjne (nie atrapy kodu)

| Obserwacja | Ocena |
|------------|--------|
| Brave Search zwraca **402 Payment Required** w logach health/tick | Problem **billing/klucza API**, nie atrapa. Warstwa `web` w `/ops/ready` pozostaje OK dzięki fallbackom (Wikipedia/DDG/specjaliści). Należy odnowić plan Brave lub zaakceptować degraded search quality. |
| Tick `system:maintenance` (executive loop) | **Działa** — planner/goal/memory write-back widoczne w journalu. |
| Broad `except Exception:` w pipeline/selector | Obecne jako **degraded-path** z logowaniem / flagami trace (nie puste `pass`). Nie maskują fail-open auth/config. |

### Acceptable / intentional

- Puste listy `return []` w research/memory przy zero results — poprawne zachowanie runtime.
- Soft reinforcement Memory V2 (bez „Memory-guided response” pollution) — celowa polityka 26.07.
- Plan-only agentic pozostaje na chat path (nie executive stub „suchy meldunek”) — celowa poprawka profili.

### Niewpięte / martwe — nie znaleziono krytycznych

Release audit + import_count=223 + ready mandatory layers (`app`, `database`, `memory_v1`, `memory_v2`, `embeddings`, `vector`, `llm`, `psyche`, `web`) potwierdzają, że kanoniczne silniki są podpięte do runtime. Nie znaleziono kompletnego silnika z zerowymi call site’ami, który „powinien” być w ścieżce turn/agent.

---

## 4. Zmodyfikowane pliki (ta sesja)

| Plik | Zmiana |
|------|--------|
| `tests/conftest.py` | Force `ENV=test`; probe flags; `isolated_db` trzyma ENV=test przed reload |
| `aihub/config.py` | Docstring kontraktu testowego dla `_validate_production_secrets` |
| `FINAL_PRODUCTION_AUDIT.md` | Ten raport |

---

## 5. Potwierdzenie spójności produkcyjnej

- **Backend** działa (systemd active, ping/ready zielone, journal bez crashy).
- **Frontend** działa (active, login 200, test/typecheck/lint PASS).
- **API** odpowiada (openapi, gpt-openapi, ops).
- **Testy** przechodzą w pełnej suicie bez ręcznego `ENV=test` w komendzie (harness jest samowystarczalny).
- **Sekrety produkcyjne** nadal są wymagane przy prawdziwym `ENV=production` poza pytest.
- **Brak** wykrytych atrap `NotImplemented`, pustych stubów OpenAPI, unfinished markers, bare except.

### Pozostałe ryzyka operacyjne (poza kodem)

1. Brave API 402 — odnowić klucz/plan.
2. Skip testów PG / ENV_STATUS_CHECK — opcjonalne rozszerzenie CI, nie blokuje production ready aplikacji.

---

## 6. Werdykt

Projekt jest w stanie **COMPLETED / PRODUCTION READY** względem wymagań tej audytacji:

- pełny pytest zielony,
- backend + frontend live,
- mandatory runtime layers ready,
- znany crash bootstrapu testów usunięty u źródła,
- brak krytycznych atrap / niewpiętych silników w skanie.

**PRODUCTION READY**
