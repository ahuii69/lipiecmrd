# Web i research

## Możliwości

- **`research.query`** — wyszukiwanie z agregacją wyników (zależnie od konfiguracji, np. Brave).
- **`web.fetch_url`** — pobranie treści URL z limitami rozmiaru i timeoutu (`HTTP_MAX_BYTES`, `HTTP_TIMEOUT_S`).

## Użycie w czacie

- Model może wywołać narzędzia w pętli (limit iteracji `CHAT_MAX_TOOL_ITERATIONS`).
- Istnieje orchestracja „controlled web” (prefetch, wymóg grounding) — szczegóły w `chat_runtime.py` i trace (`web_verified_grounding_in_prompt`, itd.).

## Wymagania środowiskowe

- Klucze API wyszukiwarki (np. `BRAVE_API_KEY`) — patrz `.env.example` / `docs/ENV.md`.
- Poprawna konfiguracja TLS (`HTTP_CA_BUNDLE` w środowiskach z własnym CA).

## Ograniczenia

- Nie gwarantujemy dostępności stron trzecich ani braku CAPTCHA.
- Treść pobrana jest przycinana — nie zastępuje archiwum prawnego ani pełnego crawlowania.
- Odpowiedzi modelu muszą być zgodne z rzeczywistym wywołaniem narzędzia (polityka prawdomówności w promptach).
