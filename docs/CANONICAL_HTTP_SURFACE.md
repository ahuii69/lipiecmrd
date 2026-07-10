# Kanoniczna powierzchnia HTTP (`aihub.main:app`)

Źródło prawdy w kodzie: **`aihub/canonical_http_surface.py`** — krotka `CANONICAL_HTTP_ROUTES` (method, path, moduł źródłowy, użycie z cockpit `ApiClient`, wskazówka testów).

**Bramka CI:** `tests/test_canonical_http_surface.py` porównuje manifest z introspekcją `app.routes`. Dodanie lub usunięcie trasy na `app` bez aktualizacji manifestu **psuje test**.

Dodatkowo ten test wymaga niepustego `tests_hint` (różnego od `BRAK DANYCH`) dla wybranych tras o wysokiej wartości: m.in. `/system/ping`, `/chat/turn`, `/web/fetch`, `/agent/run` oraz po jednym endpoincie Memory V2 i Psyche V2 (`/memory/v2/summary/{user_id}`, `/psyche/v2/{user_id}`).

Legacy **`aihub/api/*`** nie wchodzi w ten manifest (nie jest montowane w `main`).
