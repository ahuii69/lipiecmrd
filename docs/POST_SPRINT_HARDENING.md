# POST-SPRINT HARDENING — Raport

**Data:** 2026-03-06
**Scope:** `aihub/research_engine.py`, `aihub/agent_engine.py`, `tests/test_hardening.py`

---

## 1. Recon — ścieżka "Wejście → research → zapis"

```
Użytkownik (tekst)
  ↓ plan_from_text()          [agent_engine.py:156-165]
  ↓ keyword match → task type: "research.query"
  ↓ enqueue_task() → agent_tick() → claim_next_task()
  ↓ execute_task()             [agent_engine.py:208-216]
  ↓ _execute_research()        [agent_engine.py:322-361]
      ↓ rate limit check (NEW: 30s per-user)
      ↓ research()             [research_engine.py → singleton _research_engine]
          ↓ query cache check (NEW: 300s TTL, normalized query)
          ↓ _generate_placeholder_results()
              ↓ _fetch_wikipedia() → _http_get_with_backoff() (NEW: 3 retries)
              ↓ _fetch_duckduckgo() → _http_get_with_backoff() (NEW: 3 retries)
          ↓ _extract_facts_from_text() — regex patterns
          ↓ filter_research_text() (NEW: quality gate)
          ↓ add_fact() → upsert_node() [memory_engine.py]
              ↓ SHA256(layer + user_id + text) → deterministic node_id
              ↓ INSERT … ON CONFLICT DO UPDATE
```

**Format faktu w DB:**

- `layer`: `L2`
- `tags`: `["research", "<pattern_type>", "<normalized_query>"]`
- `meta`: `{"source_url", "source_title", "backend", "research_query", "research_type", "confidence", "research_fingerprint"}`

---

## 2. Co zmieniłem

### A) Idempotencja research

| Element                   | Szczegóły                                                                            |
| ------------------------- | ------------------------------------------------------------------------------------ |
| `normalize_query()`       | lowercase + collapse whitespace                                                      |
| `normalize_url()`         | lowercase host, strip UTM/tracking params, trim trailing `/`                         |
| `_research_fingerprint()` | SHA256 z `(user_id, "research", backend, normalized_query, normalized_url)` → 32 hex |
| Query cache               | `_query_cache[user_id + normalized_query]` → TTL 300s, skip cały research            |
| Fakt-level dedup          | Deterministic `node_id` via `_id_for()` + whitespace normalization w quality gate    |

**Efekt:** Ten sam query (z dowolnym case/spacing) w ciągu 5 minut → 0 nowych zapytań do Wiki/DDG, 0 nowych faktów.

### B) Rate limit + backoff

| Element          | Szczegóły                                                                           |
| ---------------- | ----------------------------------------------------------------------------------- |
| Per-user limiter | `_research_rate[user_id]` → min 30s między research (w `_execute_research`)         |
| HTTP backoff     | `_http_get_with_backoff()`: 3 retries z delay 0.2s, 0.6s, 1.5s na HTTP 429/5xx      |
| Soft-fail        | `_generate_placeholder_results()` łapie wyjątki per-backend, zwraca partial results |
| Event log        | `agent.research.rate_limited` event przy blokadzie                                  |

**Efekt:** DDG/Wiki nie dostaną flood requestów. Agent nie crashuje na 429/503.

### C) Quality gate

| Element                  | Szczegóły                                                                                                                                                      |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `filter_research_text()` | Normalizacja whitespace → check min 40 znaków → check boilerplate regex → truncate 800 znaków                                                                  |
| Blacklist                | cookies, privacy policy, javascript required, sign in, log in, accept all, terms of service, subscribe, newsletter, advertisement, click here, cookie settings |
| Wymagane meta            | `source_url`, `source_title`, `backend`, `research_query` — dodane do każdego faktu                                                                            |
| `stored` count           | Facts_extracted liczy tylko te co przeszły filtr (nie raw regex matches)                                                                                       |

**Efekt:** Śmieci z web scrape'u nie lądują w pamięci długoterminowej.

---

## 3. Zmienione pliki

| Plik                          | Co zmienione                                                                                                                                                                                                    |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `aihub/research_engine.py`    | +normalize_query/url, +\_research_fingerprint, +filter_research_text, +\_http_get_with_backoff, +query cache, +quality gate w fact loop, +per-backend soft-fail, backoff w \_fetch_wikipedia/\_fetch_duckduckgo |
| `aihub/agent_engine.py`       | +import time, +RESEARCH_RATE_LIMIT_S, +\_research_rate dict, rate limit w \_execute_research                                                                                                                    |
| `tests/test_repair_sprint.py` | Wydłużony content w test_agent_engine_executes_research_task (borderline 38→85 znaków)                                                                                                                          |
| `tests/test_hardening.py`     | **NOWY** — 19 testów w 5 klasach                                                                                                                                                                                |

---

## 4. Nowe testy (19)

| Klasa                     | Test                                           | Co sprawdza                                            |
| ------------------------- | ---------------------------------------------- | ------------------------------------------------------ |
| `TestResearchIdempotency` | `test_same_query_no_duplicates`                | 2× ten sam query → cache hit, brak wzrostu faktów      |
|                           | `test_case_space_normalization`                | "Rust Programming" vs " rust programming " → cache hit |
|                           | `test_different_query_grows`                   | Różne zapytania → nowe unikalne fakty                  |
| `TestResearchBackoff`     | `test_429_retry_then_raise`                    | HTTP 429 → 4 próby (1+3 retries) → raise               |
|                           | `test_5xx_retry_then_success`                  | 2×503 + 1×200 → sukces po 3 próbach                    |
|                           | `test_rate_limiter_skips_second`               | 2 research w 1s → event rate_limited                   |
|                           | `test_research_soft_fail_no_crash`             | Oba backendy padają → ok=True, 0 results               |
| `TestQualityGate`         | `test_filter_short_text`                       | <40 znaków → None                                      |
|                           | `test_filter_boilerplate`                      | cookies/sign in/JS required → None                     |
|                           | `test_filter_good_text_passes`                 | Dobry tekst → przechodzi                               |
|                           | `test_filter_truncates_long_text`              | >800 znaków → obcięte do 800                           |
|                           | `test_filter_normalizes_whitespace`            | Wielokrotne spacje → single space                      |
|                           | `test_boilerplate_research_zero_facts`         | Research z boilerplate content → 0 faktów w DB         |
|                           | `test_good_content_saves_facts`                | Research z dobrym content → fakty z kompletem meta     |
| `TestNormalization`       | `test_normalize_query`                         | Lowercase + collapse                                   |
|                           | `test_normalize_url_strips_tracking`           | UTM params usunięte                                    |
|                           | `test_normalize_url_trims_slash`               | Trailing slash obcięty                                 |
|                           | `test_fingerprint_stable`                      | Warianty case/spacing → ten sam hash                   |
|                           | `test_fingerprint_differs_for_different_query` | Inne query → inny hash                                 |

---

## 5. Wyniki testów

```
$ python -m pytest tests/ -q
68 passed, 4 warnings in 66.36s

Breakdown:
  36 — oryginalne testy (baseline, zero regressions)
  13 — sprint repair testy (FAZA 1-5)
  19 — hardening testy (idempotencja + backoff + quality gate)
```

---

## 6. Komendy weryfikacji

```bash
# Pełny suite
python -m pytest tests/ -q

# Tylko hardening
python -m pytest tests/test_hardening.py -v

# Tylko sprint repair
python -m pytest tests/test_repair_sprint.py -v

# Tylko oryginalne (regresja)
python -m pytest tests/test_p2p8_regression.py tests/test_memory_facts_risk.py -q
```

---

## 7. Before / After

| Metryka                | Before (post-sprint)                                  | After (hardening)                                                               |
| ---------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------- |
| **Duplikaty research** | Każde wywołanie → nowe fakty (nawet identyczne query) | Cache 300s + normalized query → 0 dupli w oknie TTL                             |
| **Stabilność (retry)** | Jeden HTTP fail → cały research pada                  | 3 retries z backoff 0.2/0.6/1.5s + per-backend soft-fail                        |
| **Rate limit**         | Brak — flood requestów możliwy                        | 30s per-user cooldown + event log                                               |
| **Jakość faktów**      | Wszystko co regex match → do DB                       | Quality gate: min 40 znaków, blacklist boilerplate, truncate 800, wymagane meta |
| **Testy**              | 49                                                    | 68 (+19 hardening)                                                              |

---

## 8. Ryzyka resztkowe

- **In-memory cache/rate**: restart serwera kasuje cache i rate limiter. Nie jest to problem produkcyjny (cold start = clean slate), ale dla HA z wieloma worker'ami cache nie jest współdzielony.
- **Regex extraction**: `_extract_facts_from_text()` operuje na regexach — jakość zależy od dopasowania wzorców. Nie jest to problem hardening'u, ale inherentne ograniczenie MVP.
- **HTTP_TIMEOUT_S**: Backoff opóźnia response — worst case 0.2+0.6+1.5 = 2.3s dodatkowego czekania. Przy timeout 12s to akceptowalne.
- **Boilerplate regex**: Pokrywa typowe wzorce angielskie. Polskie boilerplate (np. "Polityka prywatności") nie jest w blackliście — do rozszerzenia w przyszłości.
