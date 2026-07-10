# AI-Hub — Pełna Analiza Architektury

> **Data analizy:** 2025
> **Zakres:** Każdy aktywny moduł w `aihub/`, pełna analiza przepływów danych, decyzji, pamięci, psyche, agenta.
> **Zasada:** Żero zmian w kodzie. Tylko analiza oparta na odczycie każdego pliku źródłowego.

---

## SPIS TREŚCI

1. [Podsumowanie Kluczowych Odkryć](#1-podsumowanie-kluczowych-odkryć)
2. [Przegląd Systemu](#2-przegląd-systemu)
3. [Mapa Modułów](#3-mapa-modułów)
4. [Przepływ Danych: Przetwarzanie Tury (Turn)](#4-przepływ-danych-przetwarzanie-tury)
5. [Dwa Równoległe Systemy Agenta](#5-dwa-równoległe-systemy-agenta)
6. [System Pamięci (3-warstwowy)](#6-system-pamięci-3-warstwowy)
7. [Pipeline Decyzyjny (Cognitive Controller)](#7-pipeline-decyzyjny-cognitive-controller)
8. [System Psyche](#8-system-psyche)
9. [System Uczenia się (Learning Engine)](#9-system-uczenia-się-learning-engine)
10. [Research Engine](#10-research-engine)
11. [Knowledge Graph i Knowledge Evolution](#11-knowledge-graph-i-knowledge-evolution)
12. [Warstwa Wektorowa (Vector Engine)](#12-warstwa-wektorowa-vector-engine)
13. [Baza Danych](#13-baza-danych)
14. [API — 32 Endpointy](#14-api--32-endpointy)
15. [Bezpieczeństwo](#15-bezpieczeństwo)
16. [Analiza: Co Jest Realne vs Dekoracja](#16-analiza-co-jest-realne-vs-dekoracja)
17. [Wąskie Gardła i Problemy Architektoniczne](#17-wąskie-gardła-i-problemy-architektoniczne)
18. [Czy To Jest Prawdziwy Agent AI?](#18-czy-to-jest-prawdziwy-agent-ai)
19. [Podsumowanie Architektury — Verdict](#19-podsumowanie-architektury--verdict)

---

## 1. Podsumowanie Kluczowych Odkryć

| #   | Odkrycie                                                                                                      | Krytyczność    |
| --- | ------------------------------------------------------------------------------------------------------------- | -------------- |
| 1   | **DWA RÓWNOLEGŁE SYSTEMY AGENTA** które nie koordynują się ze sobą                                            | 🔴 Krytyczna   |
| 2   | **agent_loop.\_execute_action() zwraca STUBY** — nie wykonuje żadnych realnych akcji                          | 🔴 Krytyczna   |
| 3   | **Zero wywołań LLM** — cała "inteligencja" to keyword matching, regex, TF-IDF                                 | 🟡 Ważna       |
| 4   | **SQLite z globalnym lockiem wątków** — single-writer bottleneck                                              | 🟡 Ważna       |
| 5   | **Knowledge Graph jest in-memory i nigdy nie jest persystowany** — ginie przy restarcie                       | 🟡 Ważna       |
| 6   | **PredictionEngine generuje predykcje bez realnego wpływu downstream**                                        | 🟠 Umiarkowana |
| 7   | **Cognitive Controller.\_extract_intent() to 4 sprawdzenia keyword**                                          | 🟠 Umiarkowana |
| 8   | **Research backends wykonują się sekwencyjnie** (Brave → Wikipedia → DuckDuckGo)                              | 🟠 Umiarkowana |
| 9   | **MetricsEngine jest in-memory z 1h TTL** — brak persystencji metryk                                          | 🟠 Umiarkowana |
| 10  | **research_detailed() wywołuje asyncio.run() wewnątrz sync metody** — niebezpieczne jeśli już jest event loop | 🟡 Ważna       |

---

## 2. Przegląd Systemu

AI-Hub to **monolit FastAPI** z SQLite jako jedynym backendem bazodanowym. Architektura opiera się na wzorcu **singletonów** — prawie każdy silnik (psyche, learning, research, metrics, knowledge_evolution, knowledge_graph, memory_gc, conflict_detector) jest instancjonowany jednokrotnie na poziomie modułu.

```
┌──────────────────────────────────────────────────────────────┐
│                        FastAPI App                           │
│                       (main.py, 525 linii)                   │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ /turn    │  │ /memory  │  │ /psyche  │  │ /agent   │    │
│  │ /fs/*    │  │ /web     │  │ /sse     │  │ /cognitive│    │
│  │ /system  │  │ /admin   │  │          │  │          │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘    │
│       │              │             │              │          │
│  ┌────▼──────────────▼─────────────▼──────────────▼─────┐   │
│  │              WARSTWA SILNIKÓW (Engines)                │   │
│  │                                                       │   │
│  │  memory_engine   psyche_engine   cognitive_controller │   │
│  │  learning_engine research_engine prediction_engine    │   │
│  │  attention_ctrl  conflict_detector knowledge_graph    │   │
│  │  knowledge_evolution  metrics_engine  memory_gc       │   │
│  │  vector_engine   vector_index   meta_memory           │   │
│  └─────────────────────────┬─────────────────────────────┘   │
│                            │                                 │
│  ┌─────────────────────────▼─────────────────────────────┐   │
│  │          SQLite (WAL mode, jeden plik .sqlite3)        │   │
│  │  8 tabel: memory_nodes, stm_messages, psyche_state,   │   │
│  │  event_log, snapshots, memory_meta, memory_fts,       │   │
│  │  agent_tasks + widok memory_facts                      │   │
│  └────────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌────────────────────────┐  ┌───────────────────────────┐   │
│  │  agent_worker (Thread) │  │  FAISS + sentence-transf. │   │
│  │  co 3.5s → agent_tick()│  │  (opcjonalny, lazy load)  │   │
│  └────────────────────────┘  └───────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

**Stack technologiczny:**

- Python 3 + FastAPI 0.135.1
- SQLite z WAL mode (jeden plik: `data/aihub.sqlite3`)
- sentence-transformers + FAISS (lazy loaded, opcjonalny)
- httpx (HTTP client dla research i web_tools)
- Brak LLM API (OpenAI, Anthropic, etc.) — NIGDZIE w kodzie

---

## 3. Mapa Modułów

### 3.1 Infrastruktura Rdzeniowa

| Moduł              | Linie | Rola                                                               |
| ------------------ | ----- | ------------------------------------------------------------------ |
| `main.py`          | 525   | Entrypoint FastAPI, 32 route'ów, middleware auth, startup/shutdown |
| `config.py`        | 59    | Env vars, ścieżki (BASE_DIR, DATA_DIR, DB_PATH), limity pamięci    |
| `db.py`            | 429   | SQLite layer — tabele, indexy, FTS5, CRUD                          |
| `models.py`        | 91    | Pydantic modele dla API (TurnIn/Out, Memory*, FSWrite*, etc.)      |
| `logs.py`          | ~     | Konfiguracja logowania                                             |
| `auth_patch.py`    | 22    | Sprawdzenie API key, allowlist                                     |
| `core/security.py` | 35    | NO_AUTH_PATHS, ALWAYS_ALLOW_PREFIXES                               |

### 3.2 Pipeline Agenta

| Moduł             | Linie | Rola                                                                   |
| ----------------- | ----- | ---------------------------------------------------------------------- |
| `agent_worker.py` | 206   | Daemon thread, polling co 3.5s, wywołuje `agent_tick()`                |
| `agent_engine.py` | 555   | **System Agenta #1** — tick-based, realne wykonanie zadań              |
| `agent_loop.py`   | 363   | **System Agenta #2** — HTTP-triggered, cognitive controller, **STUBY** |
| `agent_runner.py` | 34    | Minimalny wrapper — `plan()` + vector memory                           |
| `agent_db.py`     | 152   | Tabela `agent_state` (kursor), `agent_tasks` (kolejka priorytetowa)    |
| `agent_api.py`    | 94    | Router `/agent/*` — status, enable, enqueue, tasks, tick, run, loop    |

### 3.3 Pipeline Pamięci

| Moduł              | Linie | Rola                                                      |
| ------------------ | ----- | --------------------------------------------------------- |
| `memory_engine.py` | 399   | Rdzeń pamięci: process_turn, retrieve_context, 3 warstwy  |
| `meta_memory.py`   | ~414  | Śledzenie ważności faktów, usage scoring, staleness       |
| `memory_gc.py`     | 234   | Garbage collection: archiwizacja, pressure relief, VACUUM |
| `vector_engine.py` | 270   | FAISS + sentence-transformers (lazy)                      |
| `vector_index.py`  | 88    | Pure Python TF-IDF: tokenize, build_df, cosine_sparse     |
| `vector_hook.py`   | 10    | Hook: `remember_turn()` → `vector_engine.add_memory()`    |

### 3.4 Pipeline Kognitywny

| Moduł                     | Linie | Rola                                                         |
| ------------------------- | ----- | ------------------------------------------------------------ |
| `cognitive_controller.py` | 488   | Centralny kontroler decyzji — keyword intent routing         |
| `attention_controller.py` | 201   | Ranking wiadomości po urgency × relevance                    |
| `conflict_detector.py`    | 246   | Walidacja: bezpieczeństwo, spójność logiczna, limity zasobów |
| `prediction_engine.py`    | 233   | Przewidywanie następnej akcji — pure heuristic               |

### 3.5 Psyche + Uczenie + Research

| Moduł                | Linie | Rola                                                         |
| -------------------- | ----- | ------------------------------------------------------------ |
| `psyche_engine.py`   | 227   | Analiza sentymentu (keyword), ewolucja nastroju/energii/cech |
| `learning_engine.py` | ~355  | Ekstrakcja faktów z dialogu — 6 reguł regex                  |
| `research_engine.py` | ~595  | Web search: Brave → Wikipedia → DuckDuckGo                   |

### 3.6 Knowledge + Metryki

| Moduł                    | Linie | Rola                                                              |
| ------------------------ | ----- | ----------------------------------------------------------------- |
| `knowledge_graph.py`     | 250   | Graf wiedzy in-memory (nodes + edges), BFS, detect contradictions |
| `knowledge_evolution.py` | 439   | Deduplikacja TF-IDF, merge, archiwizacja starych faktów           |
| `metrics_engine.py`      | 249   | Zbieranie metryk in-memory (TTL 1h), alerty                       |

### 3.7 Narzędzia

| Moduł               | Linie | Rola                                                                  |
| ------------------- | ----- | --------------------------------------------------------------------- |
| `fs_tools.py`       | 67    | Zapis/odczyt plików w sandboxie FS_ROOT                               |
| `web_tools.py`      | 34    | `fetch_url()` — async httpx GET z limitem bajtów                      |
| `sse_engine.py`     | 38    | Server-Sent Events — polling DB, keepalive                            |
| `system_ops.py`     | 99    | Snapshoty SQLite (create/list/restore)                                |
| `planner_engine.py` | 32    | Minimalny planner: keyword "sprawdź" → web.fetch, "zapisz" → fs.write |
| `admin_api.py`      | 24    | `/admin/users`, `/admin/health/{user_id}`                             |

---

## 4. Przepływ Danych: Przetwarzanie Tury

Najważniejszy flow w systemie — co się dzieje gdy użytkownik wysyła wiadomość:

```
HTTP POST /memory/add
  { user_id, user_msg, assistant_msg, intent, meta }
        │
        ▼
┌─ main.py: memory_add() ──────────────────────────────────┐
│  1. ensure_user(user_id)  → upsert psyche_state          │
│  2. evolve(user_id, user_msg, "user")                     │
│     → psyche_engine: analyze_sentiment() → keyword match  │
│     → update mood/energy/focus/traits                     │
│  3. evolve(user_id, assistant_msg, "assistant")           │
│     → j.w. ale z role_weight=0.35                         │
│  4. process_turn(user_id, user_msg, assistant_msg, ...)   │
│     │                                                     │
│     ▼                                                     │
│  ┌─ memory_engine.process_turn() ──────────────────┐      │
│  │  a) vector_hook.remember_turn(user, assistant)   │      │
│  │     → vector_engine.add_memory(user_msg)         │      │
│  │     → vector_engine.add_memory(assistant_msg)    │      │
│  │     → encode via sentence-transformers           │      │
│  │     → FAISS index.add()                          │      │
│  │     → save to disk                               │      │
│  │                                                  │      │
│  │  b) add_stm(user_id, "user", user_msg)           │      │
│  │     → INSERT INTO stm_messages                   │      │
│  │  c) add_stm(user_id, "assistant", assistant_msg)  │      │
│  │     → INSERT INTO stm_messages                   │      │
│  │  d) prune_stm() jeśli > STM_MAX_MESSAGES (200)   │      │
│  │                                                  │      │
│  │  e) add_episode (L1)                             │      │
│  │     → upsert_node(layer="L1", content=summary)   │      │
│  │                                                  │      │
│  │  f) LearningEngine.extract_facts_from_message()   │      │
│  │     → 6 reguł regex (identity, preference, ...)  │      │
│  │     → dedup SHA256                               │      │
│  │     → upsert_node(layer="L2", tags, importance)   │      │
│  │                                                  │      │
│  │  g) jeśli nic nie znaleziono → keyword fallback   │      │
│  │     → extract_facts_from_text() z agent_engine   │      │
│  │                                                  │      │
│  │  h) _get_psyche_modulation()                     │      │
│  │     → energy < 0.3 → throttle max_facts to 1    │      │
│  │     → focus > 0.7 → importance boost             │      │
│  │                                                  │      │
│  │  RETURN: { ids: [stm_user, stm_assistant,        │      │
│  │            episode, ...facts] }                   │      │
│  └──────────────────────────────────────────────────┘      │
└───────────────────────────────────────────────────────────┘
```

### Tymczasem, w tle — co 3.5 sekundy:

```
agent_worker._run_loop() [daemon Thread]
        │
        ▼
  asyncio.run(agent_tick())
        │
        ▼
┌─ agent_engine.agent_tick() ─────────────────────────────┐
│  1. get_agent_state() → kursor last_stm_ts              │
│  2. get_stm(since=cursor) → nowe wiadomości             │
│  3. jeśli > 20 wiadomości:                              │
│     → AttentionController.rank_messages() → top 20       │
│  4. DLA KAŻDEJ wiadomości:                              │
│     a) evolve(user_id, msg.content, msg.role)            │
│     b) extract_facts_from_text() – keyword matching:     │
│        "lubię" → preference                              │
│        "nazywam się" → identity                          │
│        "pracuję" → bio                                   │
│        "hasło"/"tajne" → safety                          │
│     c) plan_from_text() – keyword matching:              │
│        URL + "sprawdź" → web.fetch                       │
│        "zapisz:" → fs.write                              │
│        "snapshot" → system.snapshot                       │
│        "wyszukaj"/"research" → research.query            │
│     d) enqueue tasks (INSERT INTO agent_tasks)           │
│  5. update_agent_state(cursor=last_ts)                   │
│  6. Claim & execute tasks:                               │
│     - web.fetch → web_tools.fetch_url() [REALNE]         │
│     - fs.write → fs_tools.write_file() [REALNE]          │
│     - system.snapshot → system_ops [REALNE]               │
│     - research.query → research_engine [REALNE]           │
│  7. GC jeśli memory_pressure > 0.7                       │
└──────────────────────────────────────────────────────────┘
```

---

## 5. Dwa Równoległe Systemy Agenta

### 🔴 KRYTYCZNE ODKRYCIE

Istnieją **DWA ODRĘBNE systemy agenta** działające niezależnie, bez koordynacji:

### System #1: `agent_engine.agent_tick()` — Background Daemon

- **Trigger:** `agent_worker` budzi go co 3.5s
- **Wejście:** Nowe wiadomości STM (od ostatniego kursora)
- **Logika:** Keyword extraction → task planning → task execution
- **Wykonanie:** ✅ **REALNE** — web.fetch, fs.write, snapshot, research
- **Plik:** `agent_engine.py` (555 linii)
- **Wywołanie:** `asyncio.run()` wewnątrz `threading.Thread`

### System #2: `agent_loop.agent_cycle()` — HTTP-triggered

- **Trigger:** HTTP POST `/agent/loop`
- **Wejście:** Ostatnie wiadomości STM
- **Logika:** AttentionController → CognitiveController → ConflictDetector
- **Wykonanie:** ❌ **STUBY** — zwraca fake results bez realnych akcji
- **Plik:** `agent_loop.py` (363 linii)
- **Wywołanie:** `async/await` natywny

### Dowód — Stuby w agent_loop.py

```python
# agent_loop.py, _execute_action():

async def _execute_action(action: str, params: dict, user_id: str):
    if action == "query":
        return {"query": params.get("query", ""), "context": "memory_search_executed"}
    elif action == "learn":
        return {"topic": params.get("topic", ""), "stored": True}
    elif action == "research":
        return {"topic": params.get("topic", ""), "researched": True}
    elif action == "action":
        return {"action": params.get("action_type", ""), "executed": True}
    # ^^^ Żaden z tych bloków nie wywołuje faktycznej logiki!
    # "memory_search_executed" — bez wywołania retrieve_context()
    # "stored": True — bez wywołania add_fact()
    # "researched": True — bez wywołania research_engine.research()
```

### Problem koordynacji

Te dwa systemy:

- **Nie dzielą stanu** (osobne kursory, osobna logika)
- **Mogą przetwarzać te same wiadomości** dwukrotnie
- **Mają sprzeczne modele decyzyjne** (keyword vs cognitive controller)
- System #1 realnie działa, System #2 zwraca stuby

**Wniosek:** System #2 (`agent_loop`) jest niedokończoną implementacją — bardziej zaawansowany architektonicznie (CognitiveController, AttentionController), ale z pustymi wykonawcami.

---

## 6. System Pamięci (3-warstwowy)

```
┌────────────────────────────────────────────────────────────┐
│                    SYSTEM PAMIĘCI                           │
│                                                            │
│  L0 / STM (Short-Term Memory)                              │
│  ┌──────────────────────────────────────────────────┐      │
│  │  Tabela: stm_messages                             │      │
│  │  Rolling window: max 200 wiadomości               │      │
│  │  Pola: id, user_id, role, content, meta, ts       │      │
│  │  Odczyt: get_stm(user_id, since, limit)           │      │
│  │  Zapis: add_stm() + prune_stm() > 200            │      │
│  └──────────────────────────────────────────────────┘      │
│                           │                                │
│                           ▼                                │
│  L1 / Episodic Memory                                      │
│  ┌──────────────────────────────────────────────────┐      │
│  │  Tabela: memory_nodes WHERE layer='L1'            │      │
│  │  Zawartość: Podsumowania całych tur dialogu       │      │
│  │  Tworzenie: memory_engine.process_turn()          │      │
│  │  Format: "User: {msg} | Assistant: {msg}"         │      │
│  │  Importance: 0.4-0.6 (modulated by psyche)        │      │
│  └──────────────────────────────────────────────────┘      │
│                           │                                │
│                           ▼                                │
│  L2 / Semantic Memory (Facts)                              │
│  ┌──────────────────────────────────────────────────┐      │
│  │  Tabela: memory_nodes WHERE layer='L2'            │      │
│  │  Zawartość: Wyekstrahowane fakty o użytkowniku    │      │
│  │  Tworzenie: learning_engine (6 reguł regex)       │      │
│  │             + agent_engine keyword fallback        │      │
│  │             + research_engine (fakty z webu)       │      │
│  │  Limit: LTM_MAX_FACTS_PER_USER = 20,000          │      │
│  └──────────────────────────────────────────────────┘      │
│                           │                                │
│                           ▼                                │
│  L3 / Archive                                              │
│  ┌──────────────────────────────────────────────────┐      │
│  │  Tabela: memory_nodes WHERE layer IN ('L3',       │      │
│  │          'L3_archive')                             │      │
│  │  Zawartość: Stare fakty przeniesione przez GC     │      │
│  │  Trigger: > 30 dni + niska ważność                │      │
│  │  Cel: Zwalnianie presji pamięci                   │      │
│  └──────────────────────────────────────────────────┘      │
│                                                            │
│  ┌────────────────────────────────────────┐                │
│  │  Meta-Memory (memory_meta tabela)      │                │
│  │  - usage_score per node                │                │
│  │  - access_count                        │                │
│  │  - last_access_ts                      │                │
│  │  - freshness scoring (decay over time) │                │
│  │  overall_priority = importance × 0.3   │                │
│  │    + confidence × 0.2                  │                │
│  │    + usage × 0.3                       │                │
│  │    + freshness × 0.3                   │                │
│  └────────────────────────────────────────┘                │
│                                                            │
│  ┌────────────────────────────────────────┐                │
│  │  Vector Layer (opcjonalny)             │                │
│  │  - FAISS IndexFlatL2 (dim=384)         │                │
│  │  - sentence-transformers embedding     │                │
│  │  - Dane: data/vector.index + meta.json │                │
│  │  - Używany do dense boost w retrieval  │                │
│  └────────────────────────────────────────┘                │
└────────────────────────────────────────────────────────────┘
```

### Retrieval Pipeline

```
retrieve_context(user_id, query, limit)
    │
    ├─ 1. FTS5 search (memory_fts) → L1 + L2 nodes
    │     → ORDER BY rank (BM25)
    │     → fallback LIKE '%query%' jeśli FTS empty
    │
    ├─ 2. TF-IDF rerank
    │     → tokenize query + all results
    │     → build_df → prune_vocab → tfidf_vector
    │     → cosine_sparse ranking
    │
    ├─ 3. FAISS dense boost (opcjonalny)
    │     → encode query via sentence-transformers
    │     → FAISS search → top-k
    │     → boost scores wyników które pojawiły się w obu
    │
    ├─ 4. meta_memory.touch_nodes()
    │     → aktualizacja access_count + usage_score
    │
    └─ RETURN: { stm: [...], episodic: [...],
                  semantic: [...], dense_hits: [...] }
```

### Memory GC (Garbage Collector)

Wywoływany automatycznie przez `agent_tick()` gdy `memory_pressure > 0.7`:

1. **Delete stale:** > 90 dni i nisko oceniane, max 100 na cykl
2. **Archive old:** > 30 dni → przeniesienie do L3_archive
3. **Pressure relief:** jeśli > 5000 faktów → usuń najniższe priority
4. **Knowledge evolution:** jeśli > 2000 faktów → deduplikacja TF-IDF (knowledge_evolution.py)
5. **VACUUM:** kompakcja bazy SQLite

---

## 7. Pipeline Decyzyjny (Cognitive Controller)

### Flow

```
DecisionRequest(user_id, message, context, tools, constraints)
        │
        ▼
CognitiveController.decide()
    │
    ├─ 1. ensure_user(user_id)
    ├─ 2. _build_context()
    │     → psyche state (mood, energy, focus, temperature)
    │     → memory_pressure (count / LTM_MAX)
    │     → urgency score (keyword: "pilnie"→0.95, "ważne"→0.7)
    │
    ├─ 3. prediction_engine.predict_next_action()
    │     → Pure heuristic:
    │       high focus + relevance → continue_task
    │       high urgency → urgent_response
    │       low energy → disengage_risk
    │       high memory_pressure → memory_cleanup
    │       research intent → research_followup
    │     → Wynik LOGOWANY ale MINIMALNY wpływ na dalszą decyzję
    │
    ├─ 4. _extract_intent(message)  ← 🔴 KLUCZOWE
    │     → "sprawdź"|"wyszukaj"|"szukaj" → "research"
    │     → "stwórz"|"zrób"|"napisz"|"zapisz" → "action"
    │     → "nauczę"|"zapamiętaj"|"zapiszesz" → "learn"
    │     → default → "query"
    │
    ├─ 5. Route to _decide_{intent}()
    │     _decide_query:
    │       → FTS context search → build response
    │       → confidence = 0.7 × (0.5 + energy × 0.3 + focus × 0.2)
    │     _decide_learn:
    │       → check resource (max 10 learning ops per 5min)
    │       → confidence based on topic length
    │     _decide_research:
    │       → check resource (max 3 web ops per 5min)
    │       → requires energy > 0.2
    │     _decide_action:
    │       → check resource (max 5 memory ops per 5min)
    │       → conflict_detector.check_conflict()
    │
    ├─ 6. ConflictDetector.check_conflict()
    │     → Security: blacklisted actions (delete_all_memory, factory_reset)
    │     → Logic: write + delete same file
    │     → Resources: max 100 web requests, 1000 memory ops
    │     → jeśli severity > 0.7 → BLOCK
    │
    └─ RETURN: DecisionResult(action_type, parameters, confidence, reasoning)
```

### Ocena

- `_extract_intent()` to **4 proste sprawdzenia keyword** — ZERO analizy semantycznej
- Confidence jest obliczany z wieloma czynnikami (psyche, energy, focus) ale **bazowy mechanizm jest trywialny**
- PredictionEngine wylicza `next_likely_action` ale nie zmienia routingu decyzji
- Resource limits (max 3 web / 5 memory / 10 learning per 5min) to **jedyny realny limiter** w pipeline
- ConflictDetector **działa poprawnie** — jedyny komponent z realną wartością ochronną

---

## 8. System Psyche

```
psyche_engine.py — Singleton PsycheEngine

Stan per user (tabela psyche_state):
  - mood: 0.0-1.0 (0.55 baseline, drift toward 0.55)
  - energy: 0.0-1.0 (drains over time)
  - focus: 0.0-1.0
  - temperature: 0.3-1.2 (adapts with mood)
  - traits: { directness, patience, agreeableness, swearing_tolerance }
```

### Analiza Sentymentu

```python
_POS = {"dobrze", "super", "świetnie", "dzięki", "kocham", "lubię", "podoba",
        "fajnie", "pięknie", "idealnie", "brawo", "rewelacja", "ok", "okej",
        "tak", "yep", "yes", "cool"}  # 18 słów

_NEG = {"źle", "kurwa", "nie", "problem", "błąd", "zły", "trudne", "słabe",
        "głupi", "beznadziejne", "nuda", "denerwuje", "smutek", "wkurza",
        "chujowo", "pierdolisz"}  # 16 słów

_INTENSIFIERS = {"bardzo", "mega", "ultra", "strasznie", "cholernie", "totalnie"}

# Algorytm:
score = (pos_count - neg_count) / max(3, pos_count + neg_count)
# Zakres: -1.0 do +1.0
```

### Wpływ na System

Psyche moduluje:

1. **Memory importance scoring** (`memory_engine._get_psyche_modulation()`):
    - `energy < 0.3` → `max_facts_per_turn = 1` (throttle)
    - `focus > 0.7` → importance boost 10%
2. **Cognitive confidence** (`cognitive_controller._decide_*()`):
    - confidence = base × (0.5 + energy×0.3 + focus×0.2)
3. **Trait learning** (`psyche_engine.evolve()`):
    - Harsh input → `directness ↑`, `swearing_tolerance ↑`, `patience ↓`
    - Friendly input → `agreeableness ↑`, `patience ↑`

### Ocena

- Analiza sentymentu oparta na **34 polskich słowach** — primitywna ale funkcjonalna
- Modulacja jest **subtelna** — score confidence 0.7 × (0.5 + 0.8×0.3 + 0.6×0.2) ≈ 0.57 vs baseline 0.49
- Trait learning jest interesujący konceptualnie — system „uczy się" stylu komunikacji użytkownika
- **Problem:** Brak persystencji cech (traits) — reset per restart (przechowywane tylko w psyche_state jako str)

**Poprawka:** Traits SĄ persystowane — `upsert_psyche()` w db.py zapisuje cały stan psyche, włącznie z traits jako część JSON w kolumnie `data`. Sprawdzone w kodzie.

---

## 9. System Uczenia się (Learning Engine)

### Reguły Ekstrakcji (6 reguł regex)

| ID  | Typ               | Regex Pattern       | Przykład                             |
| --- | ----------------- | ------------------- | ------------------------------------ | -------------- | --------------------- | --------------- |
| 1   | `user_identity`   | `r"mam na imię\s+"` | "mam na imię Jan" → fact: "Jan"      |
| 2   | `user_preference` | `r"lubię\s+"`       | "lubię Pythona" → fact: preference   |
| 3   | `user_work`       | `r"pracuję\s+"`     | "pracuję w Google" → fact: work      |
| 4   | `user_goal`       | `r"chcę\s+"`        | "chcę nauczyć się Rust" → fact: goal |
| 5   | `technical_fact`  | `r"(?:python        | javascript                           | ...).\*(?:to   | jest)\s+"`            | tech extraction |
| 6   | `constraint`      | `r"(?:nie wolno     | nie mogę                             | zabrania)\s+"` | constraint extraction |

### Walidacja i Dedup

- Min 3 znaki, musi zawierać spację
- Odrzuca email/URL (regex filter)
- SHA256 hash contentu → dedup via `learned_facts: Set[str]`
- Max 3 fakty per turn (modulowane przez psyche energy)

### Ocena

- **Regex jest kruchy** — "Lubię Pythona" zadziała, "Python jest moim ulubionym" nie
- **Tylko polski język** — zero obsługi angielskiego
- **Dedup jest solidny** — SHA256 hash zapobiega zduplikowanym faktom
- **Fallback w agent_engine** dodaje bazowe keyword extraction jeśli learning engine nic nie znalazł

---

## 10. Research Engine

### Architektura

```
research(user_id, query, research_type)
    │
    ├─ 1. Query cache check (in-memory dict, TTL=300s)
    │     → jeśli cached → return empty result
    │
    ├─ 2. _fetch_search_results(query)
    │     → SEKWENCYJNIE:
    │       a) Brave API (jeśli BRAVE_API_KEY) → 5 wyników
    │       b) Wikipedia API (opensearch + extracts) → 3 wyniki
    │       c) DuckDuckGo API (instant answer) → abstrakt + related
    │     → HTTP z backoff retry (3 retries: 0.2s, 0.6s, 1.5s)
    │
    ├─ 3. Dla każdego wyniku:
    │     a) _extract_facts_from_text() → regex patterns
    │        (definition, statistics, date, claim)
    │     b) filter_research_text() → quality gate:
    │        min 40 znaków, odrzuć boilerplate
    │     c) _research_fingerprint() → dedup per source
    │     d) add_fact() → upsert_node(layer="L2", tags=["research",...])
    │     e) _calculate_relevance() → word overlap score
    │
    └─ RETURN: { ok, results: [{title, url, relevance, facts_extracted, source}] }
```

### Ocena

- **Brave API jest realny i działa** — prawdziwe wyniki wyszukiwania
- **Wikipedia extraction jest solidna** — opensearch + extracts API
- **DuckDuckGo instant answer** jest ograniczone (nie pełne wyniki wyszukiwania)
- **Backends sekwencyjne** — mogłyby być uruchomione równolegle dla 3× szybszy research
- **Quality gate** jest prosty ale skuteczny — eliminuje krótkie/boilerplate wyniki
- **research_detailed()** używa `asyncio.run()` wewnątrz sync metody — **niebezpieczne** jeśli wywoływane z istniejącego event loopa

---

## 11. Knowledge Graph i Knowledge Evolution

### Knowledge Graph (`knowledge_graph.py`)

- **In-memory** — `Dict[str, KnowledgeNode]` + `List[KnowledgeEdge]`
- Operacje: add_node, add_edge, get_related_nodes, find_path (BFS), detect_contradictions, merge_nodes
- **NIGDY nie jest persystowany** — ginie przy restarcie!
- Używany przez `cognitive_controller` do `stats()` w `/cognitive/health`
- **Ale NIGDY nie ma danych wchodzących** — nikt nie dodaje nodes/edges z pipeline agenta lub memory
- **Wniosek:** Graf wiedzy jest pustą strukturą — zadeklarowany, ale nie podłączony do przepływów danych

### Knowledge Evolution (`knowledge_evolution.py`)

- Deduplikacja semantyczna faktów via TF-IDF similarity
- `deduplicate()`: porównaj O(n²) pary → merge jeśli similarity > 0.75
- `reinforce()`: zwiększ importance + confidence faktu
- `archive_stale()`: przenieś stare fakty do L3
- `evolve_all()`: dedup L1 + dedup L2 + archive
- **Jest realnie wywołany** z `memory_gc.py` → `_compress_knowledge()` → `deduplicate()`

### Ocena

| Komponent                          | Podłączony?                                     | Realny?   |
| ---------------------------------- | ----------------------------------------------- | --------- |
| KnowledgeGraph                     | ❌ (import + stats(), ale zero danych)          | Dekoracja |
| KnowledgeEvolution.deduplicate()   | ✅ (via memory_gc)                              | Realny    |
| KnowledgeEvolution.reinforce()     | ❌ (eksportowane, nigdy wywołane automatycznie) | Dekoracja |
| KnowledgeEvolution.archive_stale() | ✅ (via evolve_all → memory_gc)                 | Realny    |

---

## 12. Warstwa Wektorowa (Vector Engine)

### Architektura

```
vector_engine.py — FAISS + sentence-transformers
  │
  ├─ Model: all-MiniLM-L6-v2 (384 dim)
  ├─ Index: FAISS IndexFlatL2 (brute force, exact search)
  ├─ Persist: data/vector.index + data/vector_meta.json
  ├─ Lazy loaded — nie inicjalizuje się dopóki nie jest potrzebny
  │
  ├─ add_memory(text): encode → index.add() → save
  ├─ search(query, k=5): encode → index.search() → distance→similarity
  ├─ health(): stats
  └─ clear(): reset index

vector_index.py — Pure Python TF-IDF
  │
  ├─ tokenize(): regex [a-zA-Z0-9_]+ → lowercase
  ├─ build_df(): document frequency
  ├─ prune_vocab(): min_df, max_df, cap VEC_MAX_VOCAB=60000
  ├─ tfidf_vector(): sublinear TF × smooth IDF → L2 norm
  └─ cosine_sparse(): sparse dict dot product

vector_hook.py — Łącznik
  │
  └─ remember_turn(user_msg, assistant_msg):
       add_memory(user_msg) + add_memory(assistant_msg)
```

### Ocena

- **FAISS IndexFlatL2** to brute-force — O(n) per query, ale dla < 100K wektorów akceptowalne
- **sentence-transformers** jest heavy na import (~2s cold start) — dlatego lazy loading
- **TF-IDF (vector_index.py)** jest używany do reranku w `memory_engine.retrieve_context()` — dodaje wartość
- **Dwa oddzielne systemy wektorowe** (FAISS + TF-IDF) — mogą dawać sprzeczne rankingi
- **Brak user_id w vector_engine** — wszystkie wektory są globalne, jeden indeks dla wszystkich użytkowników
- **meta.json** przechowuje raw text — rośnie z każdą turą bez ograniczenia

---

## 13. Baza Danych

### Schema (8 tabel + 1 widok + 1 FTS)

```sql
-- Rdzeń pamięci
CREATE TABLE memory_nodes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    layer TEXT DEFAULT 'L1',     -- L0, L1, L2, L3, L3_archive
    content TEXT,
    tags TEXT DEFAULT '[]',       -- JSON array
    meta TEXT DEFAULT '{}',       -- JSON object
    importance REAL DEFAULT 0.5,
    confidence REAL DEFAULT 0.5,
    ts REAL,
    deleted INTEGER DEFAULT 0
);

-- FTS5 pełnotekstowy
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id, content);

-- Widok "fakty" = niezarchiwizowane L2
CREATE VIEW IF NOT EXISTS memory_facts AS
    SELECT * FROM memory_nodes WHERE layer='L2' AND deleted=0;

-- STM (Short-Term Memory)
CREATE TABLE stm_messages (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,           -- "user" | "assistant"
    content TEXT NOT NULL,
    meta TEXT DEFAULT '{}',
    ts REAL NOT NULL
);

-- Stan psychologiczny
CREATE TABLE psyche_state (
    user_id TEXT PRIMARY KEY,
    mood REAL, energy REAL, focus REAL, temperature REAL,
    data TEXT                     -- JSON z traits
);

-- Logi zdarzeń
CREATE TABLE event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT, type TEXT, ts REAL,
    data TEXT                     -- JSON
);

-- Snapshoty bazy
CREATE TABLE snapshots (
    id TEXT PRIMARY KEY,
    reason TEXT, ts REAL, db_path TEXT
);

-- Meta-memory (śledzenie użycia)
CREATE TABLE memory_meta (
    node_id TEXT PRIMARY KEY,
    access_count INTEGER DEFAULT 0,
    last_access_ts REAL,
    usage_score REAL DEFAULT 0.0,
    FOREIGN KEY(node_id) REFERENCES memory_nodes(id)
);

-- Stan agenta
CREATE TABLE agent_state (
    user_id TEXT PRIMARY KEY,
    last_stm_ts REAL DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    data TEXT DEFAULT '{}'
);

-- Kolejka zadań agenta
CREATE TABLE agent_tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,           -- "web.fetch", "fs.write", "system.snapshot", "research.query"
    priority INTEGER DEFAULT 5,
    status TEXT DEFAULT 'queued', -- queued → running → done | failed
    payload TEXT DEFAULT '{}',
    result TEXT DEFAULT '{}',
    created_ts REAL,
    updated_ts REAL
);
```

### Wzorce dostępu

- **Thread lock:** `_DB_LOCK = threading.Lock()` — każda operacja DB jest serializowana
- **WAL mode:** Pozwala na równoległy odczyt, ale zapis jest nadal single-writer
- **FTS5:** Używane w `search_nodes_fts()` z fallback do LIKE jeśli empty
- **Brak connection pooling** — każda operacja otwiera/zamyka connection

### Ocena

- **Global thread lock** jest wąskim gardłem — serializuje ALL DB operations
- **WAL mode** pomaga z read concurrency ale write jest single-threaded
- **Brak migracji** — schema jest hardcoded w `init_db()`
- **Brak indeksów na user_id** w memory_nodes (potencjalnie wolne przy wielu użytkownikach)
- **FTS5** jest dobrą implementacją dla full-text search

---

## 14. API — 32 Endpointy

| Grupa         | Endpoint                   | Metoda | Realny?                             |
| ------------- | -------------------------- | ------ | ----------------------------------- |
| **System**    | `/system/ping`             | GET    | ✅                                  |
|               | `/system/health/{user_id}` | GET    | ✅                                  |
| **Turn**      | `/turn`                    | POST   | ✅ Dodaje do STM                    |
| **Psyche**    | `/psyche/{user_id}`        | GET    | ✅                                  |
|               | `/psyche/update`           | POST   | ✅ Evolves psyche                   |
|               | `/psyche/reflect`          | POST   | ✅ Word frequency analysis          |
| **Memory**    | `/memory/add`              | POST   | ✅ Full turn processing             |
|               | `/memory/search`           | POST   | ✅ FTS5 + TF-IDF + FAISS            |
| **SSE**       | `/sse/{user_id}`           | GET    | ✅ Real-time event stream           |
| **FS**        | `/fs/write`                | POST   | ✅ Real file write                  |
|               | `/fs/read`                 | POST   | ✅ Real file read                   |
|               | `/fs/list`                 | POST   | ✅ Real dir listing                 |
| **Web**       | `/web/fetch`               | POST   | ✅ Real HTTP fetch                  |
| **Snapshots** | `/system/snapshot/create`  | POST   | ✅                                  |
|               | `/system/snapshot/list`    | GET    | ✅                                  |
|               | `/system/snapshot/restore` | POST   | ✅                                  |
| **Cognitive** | `/cognitive/decide`        | POST   | ✅ Returns decision (keyword-based) |
|               | `/cognitive/health`        | GET    | ✅ System health + GC stats         |
| **Agent**     | `/agent/status/{user_id}`  | GET    | ✅                                  |
|               | `/agent/enable`            | POST   | ✅                                  |
|               | `/agent/enqueue`           | POST   | ✅                                  |
|               | `/agent/tasks/{user_id}`   | GET    | ✅                                  |
|               | `/agent/tick`              | POST   | ✅ Forces agent tick                |
|               | `/agent/run`               | POST   | ✅ Minimal planner                  |
|               | `/agent/loop`              | POST   | ⚠️ Cognitive pipeline → **STUBY**   |
| **Admin**     | `/admin/users`             | GET    | ✅                                  |
|               | `/admin/health/{user_id}`  | GET    | ✅                                  |
| **OpenAPI**   | `/gpt-openapi.json`        | GET    | ✅                                  |

---

## 15. Bezpieczeństwo

### Aktualny stan

| Aspekt            | Implementacja                                              | Ocena       |
| ----------------- | ---------------------------------------------------------- | ----------- |
| API key auth      | `x-api-key` header, env `API_KEY`                          | ✅ Działa   |
| Auth allowlist    | `/system/ping`, `/docs`, `/redoc`, `/openapi.json`         | ✅ Poprawny |
| FS sandbox        | `safe_join(FS_ROOT, path)` — blokuje path traversal        | ✅ Ważne    |
| Conflict detector | Blacklist destrukcyjnych akcji (delete_all, factory_reset) | ✅ Ochronny |
| Input validation  | Pydantic models na endpointach                             | ✅ Bazowy   |
| SQL injection     | Parametryzowane queries (`?` placeholders)                 | ✅ Poprawny |

### Braki

| Aspekt                         | Status                                                                            |
| ------------------------------ | --------------------------------------------------------------------------------- |
| Rate limiting                  | ❌ Brak na poziomie API (tylko wewnętrzne resource limits w cognitive_controller) |
| CORS                           | ❌ Nie skonfigurowane                                                             |
| Request size limits            | ❌ Brak explicit limits na body size                                              |
| User isolation w vector_engine | ❌ Jeden globalny indeks FAISS                                                    |
| Snapshot access control        | ❌ Każdy uwierzytelniony user może restore dowolny snapshot                       |
| Event log bez retencji         | ⚠️ event_log rośnie bez limitu                                                    |

---

## 16. Analiza: Co Jest Realne vs Dekoracja

### ✅ REALNE — działające komponenty

| Komponent                             | Uzasadnienie                                                                  |
| ------------------------------------- | ----------------------------------------------------------------------------- |
| **memory_engine.process_turn()**      | Realnie dodaje do STM, tworzy epizody, ekstrahuje fakty                       |
| **memory_engine.retrieve_context()**  | FTS5 + TF-IDF rerank + FAISS boost — wielowarstwowy retrieval                 |
| **psyche_engine**                     | Realnie analizuje sentiment (keyword), modyfikuje stan, moduluje inne systemy |
| **agent_engine.agent_tick()**         | Realnie wykonuje web.fetch, fs.write, snapshot, research                      |
| **research_engine**                   | Brave + Wikipedia + DuckDuckGo — realne wyniki z internetu                    |
| **conflict_detector**                 | Realnie blokuje niebezpieczne akcje                                           |
| **knowledge_evolution.deduplicate()** | Realnie deduplikuje fakty via TF-IDF similarity                               |
| **memory_gc**                         | Realnie czyści stare fakty, archiwizuje, VACUUM                               |
| **fs_tools, web_tools, system_ops**   | Realne operacje na plikach, HTTP, snapshoty                                   |
| **sse_engine**                        | Realny stream zdarzeń via Server-Sent Events                                  |
| **meta_memory**                       | Realnie śledzi usage i freshness faktów                                       |

### ❌ DEKORACJA — wygląda na coś, czym nie jest

| Komponent                                   | Uzasadnienie                                                                                                                                             |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **agent_loop.\_execute_action()**           | Zwraca stuby: `{"researched": True}` bez wywołania research_engine                                                                                       |
| **knowledge_graph**                         | In-memory, nigdy nie ma danych — nikt nie dodaje nodes z pipeline                                                                                        |
| **knowledge_evolution.reinforce()**         | Wyeksportowane jako public API, ale nigdy nie wywołane automatycznie                                                                                     |
| **prediction_engine**                       | Generuje predykcje ale nie wpływają na routing decyzji                                                                                                   |
| **cognitive_controller.\_extract_intent()** | Opisany jako "cognitive" — w rzeczywistości 4 keyword checks                                                                                             |
| **metrics_engine**                          | In-memory z 1h TTL — brak persystencji, ginie na restart                                                                                                 |
| **attention_controller.rank_messages()**    | Ranking jest poprawny, ale w System #1 (agent_tick) jest uproszczony do top-20 cutoff, a w System #2 (agent_loop) wyniki idą do execute_action() → stuby |

### ⚠️ CZĘŚCIOWO REALNE — działają, ale ograniczenie

| Komponent                        | Uzasadnienie                                                                                                                        |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **learning_engine**              | Ekstrahuje fakty — ale tylko 6 reguł regex, tylko polski, kruchość                                                                  |
| **CognitiveController.decide()** | Pipeline jest kompletny i działa — ale \_extract_intent to keyword matching, a wynik idzie do agent_loop.\_execute_action() → stuby |
| **planner_engine**               | Plan() działa — ale to 2 keyword checks w 32 liniach kodu                                                                           |

---

## 17. Wąskie Gardła i Problemy Architektoniczne

### 🔴 Krytyczne

**1. Dwa równoległe systemy agenta bez koordynacji**

- `agent_engine.agent_tick()` (daemon, realne wykonanie, keyword-based)
- `agent_loop.agent_cycle()` (HTTP, CognitiveController, stuby)
- Oba przetwarzają te same wiadomości STM
- Oba ewoluują psyche (podwójna mutacja stanu!)
- Brak mutex/koordynacji między nimi

**2. agent_loop.\_execute_action() jest martwa**

- Cały sophisticated pipeline (Attention → Cognitive → Conflict → Decision) kończy się na stubbach
- Jedyny system który realnie wykonuje akcje to agent_engine — z prostym keyword matchingiem

**3. asyncio.run() w threading.Thread**

- `agent_worker.py` uruchamia `asyncio.run(agent_tick())` w daemon thread
- Tworzy nowy event loop per tick — overhead
- Jeśli agent_tick wywołuje async code, ten tworzy KOLEJNY asyncio.run() — potencjał nested loop error

### 🟡 Ważne

**4. SQLite global thread lock**

- `_DB_LOCK = threading.Lock()` serializuje WSZYSTKIE operacje DB
- Agent tick co 3.5s + API requests + GC = contention
- WAL mode pomaga z reads, ale writes są zserializowane

**5. O(n²) w knowledge_evolution.deduplicate()**

- `_compute_semantic_similarity()` porównuje KAŻDĄ parę faktów
- Dla 5000 faktów = 12.5M porównań
- Każde porównanie: TF-IDF vector build + cosine similarity
- **Time complexity explosion** przy dużej ilości faktów

**6. Brak user isolation w vector_engine**

- Jeden globalny FAISS index dla wszystkich użytkowników
- `vector_engine.search()` nie filtruje po user_id
- User A może znaleźć embeddingi User B

### 🟠 Umiarkowane

**7. Research backends sekwencyjne**

- Brave → Wikipedia → DuckDuckGo wykonują się jeden po drugim
- Mogłyby być uruchomione równolegle (asyncio.gather) dla 3× przyspieszenia

**8. TF-IDF rebuild per query**

- `memory_engine.retrieve_context()` wykonuje `build_df()` + `tfidf_vector()` na ALL wynikach per query
- Brak cachingu TF-IDF vocabulary

**9. vector_meta.json rośnie bez limitu**

- Każda tura dodaje 2 wpisy (user + assistant) do meta.json
- Brak rotation/pruning — plik rośnie w nieskończoność

**10. research_detailed() z asyncio.run() wewnątrz sync**

- `research_detailed()` jest metodą synchroniczną
- Wywołuje `asyncio.run(self.research(...))` w pętli
- Jeśli jest wywoływana z async contextu → RuntimeError: event loop already running

---

## 18. Czy To Jest Prawdziwy Agent AI?

### Definicja prawdziwego agenta AI

Agent AI powinien:

1. **Percypować** otoczenie (obserwacja)
2. **Rozumować** nad obserwacjami (reasoning)
3. **Planować** sekwencję działań (planning)
4. **Działać** autonomicznie (action)
5. **Uczyć się** z doświadczenia (learning)

### Ocena AI-Hub względem tych kryteriów

| Kryterium       | Implementacja w AI-Hub                                                                             | ✅/❌         |
| --------------- | -------------------------------------------------------------------------------------------------- | ------------- |
| **Percepcja**   | STM polling, attention ranking (keyword)                                                           | ⚠️ Częściowa  |
| **Rozumowanie** | Brak. \_extract_intent = 4 keyword checks. Brak LLM, brak chain-of-thought, brak inference.        | ❌            |
| **Planowanie**  | plan_from_text = 2 keyword checks. planner_engine = 32 linie.                                      | ❌            |
| **Działanie**   | agent_engine.agent_tick() realnie wykonuje web.fetch, fs.write, research. ALE: agent_loop = stuby. | ⚠️ Częściowe  |
| **Uczenie**     | learning_engine = 6 regex rules. psyche trait learning (keyword). TF-IDF dedup.                    | ⚠️ Prymitywne |

### Verdict

**AI-Hub NIE JEST prawdziwym agentem AI.** Jest to **heurystyczna pętla automatyzacji z budżetowym systemem pamięci**.

Co naprawdę robi:

- **Keyword matching** zamiast rozumowania
- **Regex extraction** zamiast NLU
- **TF-IDF** zamiast semantic understanding
- **Hardcoded rules** zamiast planowania
- **Polling loop** zamiast event-driven decision making

Co by musiało się zmienić, żeby TO BYŁ prawdziwy agent:

1. Podłączenie LLM (GPT-4, Claude, Llama) do `_extract_intent()`, `plan_from_text()`, `_execute_action()`
2. Połączenie dwóch systemów agenta w jeden
3. Implementacja chain-of-thought reasoning w CognitiveController
4. Zastąpienie stubów w agent_loop realnymi executorami
5. Dodanie planera multi-step z backtracking

---

## 19. Podsumowanie Architektury — Verdict

### Co system robi dobrze

1. **Architektura pamięci (3 warstwy + meta + GC)** — przemyślana, functional, z retrieval pipeline
2. **Research engine** — realny web search z Brave/Wikipedia/DuckDuckGo, quality gate, dedup
3. **Conflict detector** — realny safety net z blacklisted actions i resource limits
4. **Event log + SSE** — solidny audit trail z real-time streaming
5. **SQLite snapshot/restore** — prosty ale skuteczny mechanizm backup
6. **Psyche modulacja** — ciekawy koncept trait learning mimo prostoty implementacji

### Co system robi źle

1. **Dwa systemy agenta zamiast jednego** — architekturalny chaos
2. **Stuby w agent_loop** — cały CognitiveController pipeline jest martwy
3. **Zero LLM** — "inteligencja" to keyword matching
4. **Knowledge Graph jest pustą skorupą** — nigdy nie dostaje danych
5. **PredictionEngine generuje predykcje w próżnię** — nie wpływają na nic
6. **O(n²) dedup w knowledge_evolution** — time bomb przy skalowaniu

### Końcowa ocena architektoniczna

```
Komponent                    | Dojrzałość  | Wartość
-----------------------------|-------------|--------
Warstwa API (32 routes)      | ████████░░  | Wysoka
System pamięci (3-tier)      | ███████░░░  | Wysoka
Research engine              | ██████░░░░  | Średnia-Wysoka
Psyche + Learning            | █████░░░░░  | Średnia
Agent System #1 (engine)     | █████░░░░░  | Średnia (keyword-based ale realny)
Cognitive Controller         | ████░░░░░░  | Niska (keyword za fasadą cognitive)
Agent System #2 (loop)       | ██░░░░░░░░  | Bardzo niska (stuby)
Knowledge Graph              | █░░░░░░░░░  | Dekoracja
PredictionEngine             | ██░░░░░░░░  | Dekoracja
MetricsEngine                | ███░░░░░░░  | Niska (in-memory, no persist)
```

### Rekomendacja architektoniczna (bez implementacji)

**Priorytet 1 — Naprawienie fundamentów:**

- Połączyć dwa systemy agenta w jeden
- Podłączyć LLM do intent extraction i decision making
- Usunąć stuby z agent_loop lub je zaimplementować

**Priorytet 2 — Knowledge Graph:**

- Podłączyć do memory pipeline (dodawanie nodes z extracted facts)
- Persystować do SQLite

**Priorytet 3 — Skalowanie:**

- Wymienić O(n²) dedup na approximate nearest neighbor
- Dodać user_id filtering do FAISS
- Rozważyć PostgreSQL przy > 1 user

---

_Dokument wygenerowany na podstawie analizy każdego aktywnego modułu w `aihub/`. Zero spekulacji — każde twierdzenie oparte na odczycie kodu źródłowego._
