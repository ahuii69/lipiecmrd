# API — AI-Hub

## Dokumentacja interaktywna

Po uruchomieniu backendu: **`GET /docs`** (Swagger UI) oraz **`GET /openapi.json`**.

## Powierzchnia kanoniczna

Źródło prawdy dla listy tras: test `tests/test_canonical_http_surface.py` oraz moduł `aihub/canonical_http_surface.py`.

### Czat (produkt)

| Metoda | Ścieżka | Opis |
|--------|---------|------|
| `POST` | `/chat/turn` | Główna tura konwersacji (`ChatTurnInput`: m.in. `message`, `history`, `mode`, `stream`) |
| `GET` | `/chat/capabilities` | Lista capability dla trybu |
| `POST` | `/chat/capabilities/execute` | Wykonanie pojedynczej capability |
| `POST` | `/chat/upload` | Upload plików do tury czatu (limit wg kontraktu) |

Sesje czatu (SQLite): listowanie, historia, rename, delete — patrz router `chat_sessions_api`.

### Pamięć

- **V1** (legacy): m.in. `/memory/search`, `/memory/add` — mogą być wyłączone flagą `AIHUB_DISABLE_LEGACY_MEMORY_V1_HTTP` (410).
- **V2**: `/memory/v2/*` — zalecana ścieżka dla nowych integracji.

### System i zdrowie

- `GET /system/ping` — szybki ping.
- `GET /cockpit/health`, `GET /cockpit/schema-health` — używane przez smoke i Cockpit.

### Agent

- `POST /agent/run`, `POST /agent/loop`, statusy — patrz `agent_api.py`.
- `POST /agent/tick/{user_id}` — opcjonalne (`AIHUB_ENABLE_AGENT_TICK_HTTP`).

## Uwierzytelnianie

Nagłówki / token zgodnie z `auth_patch.py` i dokumentacją env: [docs/ENV.md](docs/ENV.md), aliasy klucza w `config/hub_key_env_names.json`.

## Ograniczenia

- Limity rozmiaru payloadu i timeouty LLM/narzędzi w `aihub.config`.
- Część endpointów zwraca szczegóły tylko przy `include_debug` / trybie `debug` — nie ujawniaj ich publicznie w produkcji.
