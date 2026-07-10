# Warstwa `aihub/api/*` — LEGACY / UNMOUNTED

## Granica względem kanonu

- **Aktywny runtime HTTP** to aplikacja FastAPI z **`aihub.main:app`**: trasy na `app` oraz routery z `app.include_router(...)` — pełna lista tras w **`aihub/canonical_http_surface.py`** (test: `tests/test_canonical_http_surface.py`).
- **Plik z `APIRouter` w `aihub/api/` ≠ automatycznie endpoint HTTP.** Dopóki nie ma `include_router` w `aihub/main.py`, router **nie** wchodzi do produkcyjnej powierzchni.
- Konfiguracja procesu uvicorn czyta **`aihub.config`** (oraz env); część plików w tym katalogu importuje **`aihub.core.config`** — adapter od kanonu, opis w `aihub/core/config.py`.

## Wyjątek: co z `aihub/api/` jest montowane w `aihub.main`

Jawny import w `aihub/main.py` (`include_router`):

| Moduł | Prefiks HTTP | Uwagi |
|-------|----------------|--------|
| `aihub.api.security_router` | `/system/security` | ACTIVE_CONFIRMED |
| `aihub.api.self_heal_status_router` | `/system/self-heal-db` | ACTIVE_CONFIRMED |

Wszystkie **pozostałe** pliki `aihub/api/*.py` z routerami są w tym dokumencie traktowane jako **UNMOUNTED** (chyba że ktoś świadomie doda `include_router` w `main`).

## Import-smoke (retained), nie montaż

Test `tests/test_aihub_api_legacy_import_smoke.py` wymaga, by poniższe moduły **dały się zaimportować** (zależności jak `requirements.txt`) — to **nie** znaczy, że są na `app`:

- `admin_router`, `anomaly_router`, `fs_router`, `memory_router`, `memory_stats_router`, `memory_train_router`, `ops_router`, `psyche_brain_router`, `psyche_brain_live_router`, `psyche_predict_router`, `psyche_router`, `security_router`, `self_heal_status_router`, `sse_router`

## `ai_compat_router.py` — USUNIĘTY z `aihub/api/` (06.07 naprawa, P0 bezpieczeństwo)

Ten moduł **nie istnieje już** w `aihub/api/`. Zawierał `POST /ai/python/run` — wykonanie dowolnego kodu Python przez HTTP (`subprocess.run([python, "-c", req.code])`) oraz `/ai/docker/info`, `/ai/docker/ps` (shell do `docker`). Fakt, że był `UNMOUNTED`, dawał złudne poczucie bezpieczeństwa — jedna przypadkowa linia `include_router` w `aihub/main.py` wystarczyłaby, by to RCE stało się aktywnym endpointem.

**Decyzja:** przeniesiony do `archive/legacy_routers/ai_compat_router.py` — poza pakietem `aihub`, bez `__init__.py`, nieimportowalny jako `aihub.api.ai_compat_router`. Zob. `archive/legacy_routers/README.md`. Nie przywracać do `aihub/` bez świadomej decyzji bezpieczeństwa.

## `ops_router.py` — USUNIĘTY z `aihub/api/` (06.07 naprawa, P1 bezpieczeństwo)

Ten moduł **nie istnieje już** w `aihub/api/`. Zawierał hardcoded `ROOT_DIR = Path("/root/ai-hub")` (nie zgadza się z realną ścieżką tego wdrożenia, `/home/ubuntu/mrd`) oraz `POST /system/ops/rollback`, które po rozpakowaniu tarballa wywoływało `systemctl restart aihub` przez `subprocess.Popen(["bash", "-lc", ...])`. Miał jawną flagę `AIHUB_ALLOW_OPS` i walidację ścieżek przy rozpakowywaniu tar — lepszą higienę niż `ai_compat_router.py` — ale hardcoded host path i zgadywana nazwa jednostki systemd czynią go niebezpiecznym do zamontowania bez przepisania.

**Decyzja:** przeniesiony do `archive/legacy_routers/ops_router.py`. Nie przywracać bez usunięcia hardcoded ścieżki i przeglądu mechanizmu restartu dla docelowego hosta.

## Kolizje prefiksów `/memory` i `/psyche` (06.07 naprawa, P2 — decyzja: pozostają UNMOUNTED, nie archiwizowane)

`memory_router.py`, `memory_stats_router.py`, `memory_train_router.py` (wszystkie `prefix="/memory"`)
oraz `psyche_router.py`, `psyche_predict_router.py`, `psyche_brain_router.py`,
`psyche_brain_live_router.py`, `anomaly_router.py` (wszystkie `prefix="/psyche"`) kolidują nazwami
prefiksów między sobą i z kanonem (`/memory/v2/*`, `/psyche/v2/*` w `*_api.py`).

**Decyzja:** w odróżnieniu od `ai_compat_router.py` (RCE), `ops_router.py` (systemctl + hardcoded
path) i `admin_router.py` (wyciek body), te 8 plików **nie** wykonują dowolnego kodu, nie robią
`subprocess`/`systemctl`, i nie zwracają nieredagowanych danych — ryzyko jest ograniczone do
"przypadkowego montażu bez przeglądu nadpisze/zduplikuje trasy", nie do bezpośredniego
bezpieczeństwa. Pozostają **UNMOUNTED** i objęte tym dokumentem (sekcja "Import-smoke" wyżej) —
to już jest formalne oznaczenie jako legacy na poziomie całego katalogu. Nie archiwizowano ich
fizycznie, żeby nie rozrastać zakresu tego sprintu naprawczego poza to, co wymaga bezpieczeństwo;
jeśli ktoś zdecyduje się je kiedyś zamontować, musi to zrobić po przeczytaniu tego dokumentu i
świadomie rozstrzygnąć kolizję nazw z kanonem.

## `admin_router.py` — USUNIĘTY z `aihub/api/` (06.07 naprawa, P1 kolizja admin + wyciek body)

Ten moduł **nie istnieje już** w `aihub/api/`. Miał ten sam prefiks `/admin` co kanoniczny, montowany `aihub/admin_api.py` (`GET /admin/ping`) — kolizja nazw, która zachęcała do zamontowania obu naraz. `GET /admin/events/body?id=...` serwowałby wtedy nieredagowane base64 request/response body z tabeli wypełnianej przez (również niezarejestrowany) `aihub/middleware/recorder.py`.

**Decyzja:** jeden kanoniczny router admina to `aihub/admin_api.py`. Stary router przeniesiony do `archive/legacy_routers/admin_router.py`. Nie przywracać bez scalenia z `admin_api.py` i dodania redakcji sekretów/PII dla jakiegokolwiek endpointu zwracającego body żądania/odpowiedzi.

**Uwaga:** `security_router` i `self_heal_status_router` są **jednocześnie** montowane w `main` (powyżej) *oraz* na liście import-smoke — import-smoke nie zastępuje manifestu tras.

## Wyłączone z import-smoke

- `aihub.api.web_router` — wymaga `beautifulsoup4` (`bs4`), **nie** w kanonicznym `requirements.txt`; kanon HTTP dla pobierania URL to `POST /web/fetch` → `aihub.web_tools` na `aihub.main`.

## Cockpit a backend

Cockpit nie woła dowolnej ścieżki backendu: BFF `/api/aihub/...` + allowlista `cockpit/lib/api/cockpit-proxy-allowlist.json` (testy: `tests/test_cockpit_proxy_allowlist.py`).

---

## Stary akapit (nadal ważny)

- **Routery produkcyjne poza `aihub/api/`** są w **`aihub/*_api.py`** (`admin_api`, `agent_api`, `chat_api`, `cockpit_api`, `memory_v2_api`, `psyche_v2_api`).
- **Moduły w `aihub/api/` (poza dwoma montowanymi)** — domyślnie **UNMOUNTED**; nie stanowią domyślnej powierzchni API bez jawnego montażu.

## Dlaczego to istnieje

Historyczna / równoległa warstwa routerów (eksperymenty, narzędzia, alternatywne ścieżki). **Nie usuwać ani nie montować automatycznie** bez osobnej decyzji architektonicznej.

## Nakładanie na aktywny stos (skrót)

- **`web_router.py`:** osobna sekcja poniżej (P3B) — jedna prawda dla warstwy `/web` legacy.
- **Nakładanie prefiksów** (inne podścieżki lub metody HTTP): `/admin`, `/fs`, `/memory`, `/psyche`, `/sse`, `/system/*` względem tras z `main.py` i prefiksów `*_api.py`.

## `web_router.py` — jedna prawda (P3B)

> **06.07 naprawa — korekta:** plik `aihub/api/web_router.py`, opisany poniżej, **nie istnieje**
> w bieżącym spisie plików tego repo (sprawdzone: `ls aihub/api/`). Sekcja poniżej opisuje
> historyczną decyzję/porównanie z czasu, gdy plik istniał; zostawiona jako dokumentacja
> architektoniczna (NEAR_DUPLICATE vs kanon), ale **nie odnosi się do pliku obecnego na dysku**.
> Nic w gate'ach importowych nie odwołuje się do tego pliku (`_LEGACY_API_ROUTER_IMPORT_SMOKE_EXCLUDED`
> w `tests/test_aihub_api_legacy_import_smoke.py` dokumentuje wykluczenie, nie zakłada istnienia pliku).

- **Status:** LEGACY / UNMOUNTED (brak `include_router` w `aihub.main`).
- **Kanon runtime:** `POST /web/fetch` na `app` w `aihub/main.py`, implementacja przez `aihub.web_tools.fetch_url`.
- **Porównanie względem kanonu:** **NEAR_DUPLICATE** (nie `EXACT_DUPLICATE`, nie `DELETE_CANDIDATE` w sensie „pusty duplikat”).
- **Dlaczego NEAR_DUPLICATE:** ten sam nominalny cel (pobranie URL), ale inny kontrakt żądania/odpowiedzi, inna implementacja i model bezpieczeństwa (`aihub.core.config` vs ścieżka `aihub.config` w `web_tools`), oraz logika wyłącznie w legacy (m.in. BeautifulSoup, `body_base64`, pola `timeout_sec` / `max_bytes` / `extract_text`).
- **Rola pliku:** wyłącznie **referencja / historia** — **nie** traktować jako aktywnej ścieżki produkcyjnej przy obecnym repo.
- **Operacyjnie:** **nie montować** obok kanonu na tej samej ścieżce `POST /web/fetch` bez osobnej decyzji i merge kontraktu.
- **Następny krok (poza P3C):** ewentualna decyzja **archiwum / usunięcie** — osobny, świadomy krok; **nie** w zakresie obecnej dokumentacji.
- **Brak tej samej powierzchni w active** (przykłady): prefiks **`/ai/*`** (dawny `ai_compat_router`, od 06.07 zarchiwizowany poza `aihub/`, zob. wyżej); ścieżki **`/agent/*`**, **`/chat/*`**, **`/cockpit/*`**, **`/memory/v2/*`**, **`/psyche/v2/*`** są tylko w `*_api.py`.

Szczegółowa macierz endpointów: audyt P1 / dokumentacja projektu.

## Importy

Moduły powinny dać się **importować** (`import aihub.api.<nazwa_modułu>`) bez uruchamiania aplikacji — służy to smoke testom i narzędziom. **Montaż w `main.py` wymaga jawnej zmiany** i nie jest częścią tej warstwy domyślnie.

**Gate importowy:** `tests/test_aihub_api_legacy_import_smoke.py` obejmuje **świadomie wybrany podzbiór** routerów — te, które importują się wyłącznie z zależnościami z kanonicznego `requirements.txt` (ten sam zestaw co aktywny runtime). **`web_router.py` jest wyłączony z tego gate’u**, bo wymaga `beautifulsoup4` (`bs4`), którego **nie** ma w `requirements.txt`; dodawanie pakietu tylko po to, by importować nieużywany router, sztucznie rozszerzałoby powierzchnię instalacji bez korzyści dla kanonu (`POST /web/fetch` jest na `aihub.main`). Import `web_router` pozostaje możliwy lokalnie po `pip install beautifulsoup4`.
