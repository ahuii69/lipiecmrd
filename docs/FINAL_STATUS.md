# AI-Hub — FINAL STATUS

> **DOKUMENT HISTORYCZNY (06.07 naprawa).** Opisuje stan projektu z 2026-03-06, inną architekturę
> deploymentu (`ahui69.org`, Caddy, `add_episode()`/`add_fact()`) niż aktualne repo. **Nieaktualne
> względem obecnego kodu**, potwierdzone w `06.07audyt.md`:
> - "Martwe moduły: Zero" (linia 73) — **nieprawda dla obecnego stanu**: `06.07audyt.md` §13
>   dokumentuje wiele martwych/niewpiętych modułów (`aihub/memory/*`, `aihub/psyche/*`,
>   `aihub/memory_v2_decay.py` przed 06.07, cała warstwa `aihub/api/*` poza dwoma routerami).
> - "`focus_on()` — placeholder (zwraca `[]`)" (linia 34) — **nieprawda dla obecnego kodu**:
>   `aihub/attention_controller.py::focus_on()` ma pełną implementację (nie jest placeholderem),
>   ale wciąż nie jest wołany w runtime — patrz `06.07audyt.md`.
> - Aktualny, techniczny stan projektu: `06.07audyt.md` i `06.07naprawa.md`.

**Data:** 2026-03-06
**URL:** https://ahui69.org (historyczny — nie potwierdzone dla obecnego wdrożenia)

## Co działa

### Memory System ✅

- **STM** — short-term memory (200 msg/user)
- **Episodes** (L1) — automatyczne z `add_episode()`
- **Facts** (L2) — automatyczne z `add_fact()`, extraction z LLM
- **Retrieve** — FTS5 + psyche scoring + vector dense boost (FAISS)
- **Meta Memory** — touch_nodes tracking
- **GC** — garbage collection z pressure threshold, archiwizacja, dedup

### Psyche System ✅

- **Evolve** — dynamiczny stan (mood/energy/focus/style)
- **Reflect** — generowanie refleksji
- **Modulation** — importance/confidence scoring z psyche state
- **Learning throttle** — fakty ograniczone wg energy/focus

### Knowledge Graph ✅

- **Feed** — zasilany z `add_fact()` + `add_episode()` (node_type: fact/episode)
- **Graph** — in-memory nodes + edges + relation_index
- ⚠️ **Nie persystuje** — po restarcie procesu graf jest pusty (zasilany na bieżąco w runtime)

### Attention Controller ✅

- **Filtering** — batche >20 msg filtrowane przez `rank_messages`
- **Graceful fallback** — błąd = przetwarza wszystkie
- ⚠️ `focus_on()` — placeholder (zwraca `[]`), nikt nie woła w runtime

### Vector Engine ✅

- **Dense boost** — FAISS search w `retrieve_context`
- **Similarity filter** — >0.3 threshold

### Research Engine ✅

- **Query task** — `research.query` task type
- **Rate limiting** — per-user
- **Backoff + quality gate** — fingerprint dedup

### Agent System ✅

- **Tick loop** — agent_tick z attention filtering
- **Task planning** — plan_from_text
- **Execution** — task runner z research, memory, psyche

### Learning Engine ✅

- **learn_from_reflection** — wywoływany z psyche.reflect()
- **Knowledge evolution** — `evolve_all` (dedup + archive_stale)

### Infrastructure ✅

- **Caddy HTTPS** — `ahui69.org`, auto Let's Encrypt, HTTP/2
- **systemd** — `aihub.service` + `caddy.service`
- **start.sh / stop.sh / sanity.sh** — pełny lifecycle
- **Log rotation** — 10MB × 10

## Testy

```
92 passed, 0 warnings, 0 failures
```

## Martwe moduły

**Zero** — wszystkie 24 moduły ACTIVE po wireup sprint.
