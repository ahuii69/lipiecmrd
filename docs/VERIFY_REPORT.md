# 🔍 VERIFY REPORT — AI-Hub Reality Check

**Data:** 2026-03-06
**Agent:** VS Code / Copilot (Claude Opus 4.6)
**Repo:** `/root/ai-hub`
**Metoda:** statyczna analiza kodu + śledzenie callgraph (terminal sandbox niedostępny — brak curli na żywo)

---

## 📌 SEDNO (TL;DR)

1. **Memory system DZIAŁA** — STM→L1→L2 pipeline zweryfikowany, FTS5+TF-IDF rerank + FAISS dense_hits boost (opcjonalny). Meta-memory touch_nodes wired. GC wired przez `_maybe_gc()`.
2. **Psyche system DZIAŁA** — evolve() modyfikuje mood/energy/focus/traits, reflect() generuje topic-ish summary z STM. Sentiment oparty na polskich keyword sets. Psyche modulation wpływa na scoring faktów w memory_engine.
3. **Learning Engine DZIAŁA** — Wołany w 2 miejscach: (a) `memory_engine.process_turn()` → `extract_facts_from_message()`, (b) `psyche_engine.reflect()` → `learn_from_reflection()`. Regex rules, dedup przez hash.
4. **Research Engine DZIAŁA** — Wikipedia + DuckDuckGo (httpx), wołany przez `agent_engine._execute_research()` z rate limitingiem (30s/user). Planowany przez `plan_from_text()` na keyword "wyszukaj|research|zbadaj".
5. **FAISS (vector_engine.search) NAPRAWDĘ wołany** — w `memory_engine.retrieve_context()` L335-345 jako `dense_hits` boost. Docs FLOW_DIAGRAMS_EVIDENCE.md twierdzą że "NIGDY NIE WOŁANY" — **to jest NIEPRAWDA, docs kłamią tu**.
6. **Dead code w agent_engine.py** — linie 557-585 to unreachable code (duplikat logiki `update_cursor` + `claim_next_task` po bloku try/except który zawsze returnuje). Nie szkodzi ale śmieci.
7. **start.sh czeka na `/health`** — endpoint NIE ISTNIEJE w main.py. Serwer odpala się OK bo uvicorn startuje w tle i `wait_health()` timeoutuje po 15s, ale health check jest fałszywie negatywny. Faktyczny ping to `/system/ping`.

---

## ETAP 0 — PRE-FLIGHT

### Snapshot info

> ⚠️ Terminal sandbox niedostępny (brak bwrap/rg/socat). Pre-flight zebrano z odczytu plików.

| Parametr                   | Wartość                    |
| -------------------------- | -------------------------- |
| Python config default PORT | 8000 (`aihub/config.py:9`) |
| start.sh PORT_BASE         | 8080 (`start.sh:10`)       |
| HOST (config.py)           | `0.0.0.0`                  |
| HOST (start.sh)            | `127.0.0.1`                |
| DB_PATH                    | `data/aihub.sqlite3`       |
| APP_IMPORT                 | `aihub.main:app`           |
| Workers                    | 1                          |

### Entrypoint chain

```
start.sh
  → install_deps (pip install -r requirements.txt)
  → check_app_import (importlib test)
  → start_server (uvicorn aihub.main:app --host 127.0.0.1 --port <picked>)
  → wait_health (curl /health → UWAGA: /health NIE ISTNIEJE!)
```

**Dowód:** [start.sh](../start.sh) L224-230 (wait_health czeka na `/health`), [main.py](../aihub/main.py) L114 (endpoint to `/system/ping`, nie `/health`).

---

## ETAP 1 — VERIFY REALITY

### A) RUNTIME MAP

#### Endpointy z main.py (19 sztuk)

| #   | Metoda | Path                       | Linia |
| --- | ------ | -------------------------- | ----- |
| 1   | GET    | `/system/ping`             | 114   |
| 2   | GET    | `/system/health/{user_id}` | 120   |
| 3   | POST   | `/turn`                    | 132   |
| 4   | GET    | `/psyche/{user_id}`        | 149   |
| 5   | POST   | `/psyche/update`           | 161   |
| 6   | POST   | `/psyche/reflect`          | 173   |
| 7   | POST   | `/memory/add`              | 192   |
| 8   | POST   | `/memory/search`           | 209   |
| 9   | GET    | `/sse/{user_id}`           | 256   |
| 10  | POST   | `/fs/write`                | 273   |
| 11  | POST   | `/fs/read`                 | 286   |
| 12  | POST   | `/fs/list`                 | 299   |
| 13  | POST   | `/web/fetch`               | 317   |
| 14  | POST   | `/system/snapshot/create`  | 333   |
| 15  | GET    | `/system/snapshot/list`    | 346   |
| 16  | POST   | `/system/snapshot/restore` | 358   |
| 17  | POST   | `/cognitive/decide`        | 376   |
| 18  | GET    | `/cognitive/health`        | 425   |
| 19  | GET    | `/gpt-openapi.json`        | 495   |

#### Routery (include_router)

| Router       | Prefix   | Plik           | Endpointów                                                                                                                                       |
| ------------ | -------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| admin_router | `/admin` | `admin_api.py` | 2 (`/admin/users`, `/admin/health/{user_id}`)                                                                                                    |
| agent_router | `/agent` | `agent_api.py` | 7 (`/agent/status/{user_id}`, `/agent/enable`, `/agent/enqueue`, `/agent/tasks/{user_id}`, `/agent/tick/{user_id}`, `/agent/run`, `/agent/loop`) |

**Razem:** 28 custom + 3 FastAPI auto (docs/redoc/openapi.json) = **31 routes**
(GO_NO_GO.md twierdzi 32 — drobna różnica, prawdopodobnie wynik innego sposobu liczenia / root route)

#### Startup hooks (`main.py` L90-100)

```python
@app.on_event("startup")
def _startup():
    init_db()             # tworzy tabele SQLite
    start_worker_once()   # daemon thread z agent_tick loop (co ~3.5s)
```

**Dowód:** [main.py](../aihub/main.py) L90-100.

#### ACTIVE MODULES vs PRESENT BUT NOT CALLED

| Moduł                         | Import z main.py                         | Wołany w runtime                               | Status                               |
| ----------------------------- | ---------------------------------------- | ---------------------------------------------- | ------------------------------------ |
| memory_engine                 | ✅ L49                                   | ✅ /memory/add, /memory/search, /system/health | **ACTIVE**                           |
| psyche_engine                 | ✅ L53                                   | ✅ /psyche/\*, middleware ensure_user          | **ACTIVE**                           |
| cognitive_controller          | ✅ L47                                   | ✅ /cognitive/decide, /cognitive/health        | **ACTIVE**                           |
| knowledge_graph               | ✅ L48                                   | ✅ .stats() w /cognitive/health                | **ACTIVE** (in-memory only)          |
| conflict_detector             | ✅ L48                                   | ✅ via CognitiveController                     | **ACTIVE**                           |
| metrics_engine                | ✅ L50-54                                | ✅ record_latency, get_system_health           | **ACTIVE**                           |
| sse_engine                    | ✅ L54                                   | ✅ /sse/{user_id}                              | **ACTIVE**                           |
| admin_api                     | ✅ L41                                   | ✅ include_router                              | **ACTIVE**                           |
| agent_api                     | ✅ L42                                   | ✅ include_router                              | **ACTIVE**                           |
| agent_worker                  | ✅ L43                                   | ✅ start_worker_once()                         | **ACTIVE**                           |
| agent_engine                  | via agent_api                            | ✅ agent_tick()                                | **ACTIVE**                           |
| learning_engine               | via memory_engine                        | ✅ process_turn, reflect                       | **ACTIVE** (nie bezpośrednio w main) |
| research_engine               | via agent_engine                         | ✅ \_execute_research                          | **ACTIVE** (nie bezpośrednio w main) |
| vector_engine                 | via memory_engine                        | ✅ dense_hits boost                            | **ACTIVE** (opcjonalnie)             |
| vector_index                  | via memory_engine                        | ✅ \_vector_rerank TF-IDF                      | **ACTIVE**                           |
| vector_hook                   | via memory_engine                        | ✅ remember_turn                               | **ACTIVE**                           |
| attention_controller          | via agent_engine + cognitive_controller  | ✅ rank_messages                               | **ACTIVE**                           |
| meta_memory                   | via memory_engine + cognitive_controller | ✅ touch_nodes, check_stale                    | **ACTIVE**                           |
| memory_gc                     | via agent_engine                         | ✅ \_maybe_gc                                  | **ACTIVE**                           |
| knowledge_evolution           | via memory_gc                            | ✅ evolve_all (GC trigger)                     | **ACTIVE**                           |
| prediction_engine             | via cognitive_controller                 | ✅ predict_next_action                         | **ACTIVE**                           |
| fs_tools                      | ✅ L47                                   | ✅ /fs/\*                                      | **ACTIVE**                           |
| web_tools                     | ✅ L56                                   | ✅ /web/fetch                                  | **ACTIVE**                           |
| system_ops                    | ✅ L55                                   | ✅ /system/snapshot/\*                         | **ACTIVE**                           |
| \_legacy_api/\*               | ❌ not imported                          | ❌                                             | **DEAD** (nie include'owane)         |
| attention_controller.focus_on | defined                                  | ❌ returns `[]` hardcoded                      | **PLACEHOLDER**                      |

### B) LIVE VERIFY

> ⚠️ Terminal sandbox niedostępny — testy curl niemożliwe bezpośrednio. Weryfikacja oparta na statycznej analizie kodu.

Poniżej **oczekiwane zachowanie** na podstawie analizy kodu, z odniesieniami:

#### Test 1: GET /system/ping

```bash
# LOCAL:
curl -s http://127.0.0.1:8080/system/ping
# PROD:
curl -s https://ahui69.org/system/ping
```

**Oczekiwany output:**

```json
{ "ok": true, "ts": 1741276800.0, "app": "AIHub" }
```

**Analiza:** Endpoint bezstanowy, zero zależności. Zwraca `time.time()` i APP_NAME.
**Dowód:** [main.py](../aihub/main.py) L114-117.
**Prognoza:** ✅ PASS

#### Test 2: GET /cognitive/health

```bash
# LOCAL:
curl -s http://127.0.0.1:8080/cognitive/health
# PROD:
curl -s https://ahui69.org/cognitive/health
```

**Oczekiwany output:**

```json
{
    "status": "ok",
    "health": {
        "latency_ms": 0.0,
        "error_rate": 0.0,
        "requests_per_second": 0.0
    },
    "alerts": [],
    "db_schema": {
        "ok": true,
        "alerts": [],
        "tables": {
            "memory_nodes": "table",
            "memory_facts": "view",
            "memory_meta": "table"
        }
    },
    "gc_stats": { "active": 0, "archived": 0, "deleted": 0 },
    "graph_stats": {
        "nodes": 0,
        "edges": 0,
        "relation_types": 0,
        "contradictions": 0
    }
}
```

**Analiza:** Sprawdza SQL schema, liczy GC stats, KnowledgeGraph (in-memory — 0 po restarcie).
**Dowód:** [main.py](../aihub/main.py) L425-490.
**Prognoza:** ✅ PASS (graph_stats=0/0/0/0 na fresh start to normalne)

#### Test 3: POST /memory/add → POST /memory/search

```bash
# ADD:
curl -s -X POST http://127.0.0.1:8080/memory/add \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","user_msg":"lubię pizzę","assistant_msg":"Zanotowałem!","intent":"preference","meta":{}}'

# SEARCH:
curl -s -X POST http://127.0.0.1:8080/memory/search \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","query":"pizza","limit":5}'
```

**Oczekiwany flow:**

1. `evolve("test", user_msg, "user")` — psyche update
2. `evolve("test", assistant_msg, "assistant")` — psyche update
3. `process_turn()` → `remember_turn()` (FAISS write) → 2x `add_stm()` → `add_episode(L1)` → `LearningEngine.extract_facts_from_message()` → keyword fallback → `add_fact(L2, "Użytkownik: lubię pizzę", tags=["user","preference","preference"])`
4. Search: `get_stm()` + `search_nodes_fts(L1, "pizza")` + `search_nodes_fts(L2, "pizza")` + `_vector_rerank()` (TF-IDF) + optional `vector_engine.search()` dense boost

**Dowód:**

- [memory_engine.py](../aihub/memory_engine.py) L196-265 (process_turn), L276-380 (retrieve_context)
- [main.py](../aihub/main.py) L192-250

**Prognoza:** ✅ PASS

#### Test 4: POST /psyche/update → POST /psyche/reflect

```bash
# UPDATE:
curl -s -X POST http://127.0.0.1:8080/psyche/update \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","text":"super mega dzięki, jesteś najlepszy!","role":"user"}'

# REFLECT:
curl -s -X POST http://127.0.0.1:8080/psyche/reflect \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","query":"","limit":10}'
```

**Oczekiwany flow:**

1. `evolve("test", text, "user")` → sentiment: "super"=POS, "mega"=POS+INTENS, "dzięki"=POS → s>0 → mood↑, energy↑, traits.agreeableness↑ (friendly path)
2. `reflect("test", stm_messages)` → freq count z last 20 STM → top topics → mood_desc="spoko" (jeśli mood>0.60)

**Dowód:** [psyche_engine.py](../aihub/psyche_engine.py) L99-193.
**Prognoza:** ✅ PASS

#### Test 5: Agent tick (background)

```bash
# Manual trigger:
curl -s -X POST http://127.0.0.1:8080/agent/tick/test

# Albo: agent_worker daemon robi to automatycznie co ~3.5s
```

**Oczekiwany flow:**

1. Pull nowe STM messages (since last cursor)
2. Attention filtering (jeśli >20 msg)
3. evolve() per message
4. extract_facts_from_text() per user message
5. plan_from_text() → enqueue tasks (web.fetch, fs.write, research.query, system.snapshot)
6. execute_task() → claim + execute + complete

**Dowód:**

- [agent_engine.py](../aihub/agent_engine.py) L378-543
- [agent_worker.py](../aihub/agent_worker.py) `start_worker_once()`, `_run_loop()` (daemon thread, `AGENT_INTERVAL_S=3.5`)
- [agent_api.py](../aihub/agent_api.py) L67-75

**Prognoza:** ✅ PASS (ale wymaga danych w STM żeby coś zrobić)

#### Test 6: POST /cognitive/decide

```bash
curl -s -X POST "http://127.0.0.1:8080/cognitive/decide?user_id=test" \
  -H "Content-Type: application/json" \
  -d '{"message":"sprawdź co to jest Python","context":{}}'
```

**Oczekiwany output:**

```json
{
  "action_type": "research",
  "parameters": {"query": "sprawdź co to jest Python", ...},
  "confidence": 0.7,
  "reasoning": "...",
  "duration_ms": ...
}
```

**Analiza:** `_extract_intent("sprawdź co to jest Python")` → "sprawdź" matchuje keyword → intent="research" → `_decide_research()` → action_type="research"

**Dowód:** [cognitive_controller.py](../aihub/cognitive_controller.py) L145-275.
**Prognoza:** ✅ PASS

---

### C) MEMORY — TRUTH CHECK

| Claim                                      | Zweryfikowany? | Dowód                                                                                                                                                                                                 |
| ------------------------------------------ | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| STM zapisuje i przycina (prune)            | ✅ TAK         | `add_stm()` → `insert_stm_message()` → `prune_stm(user_id, 200)`. [memory_engine.py](../aihub/memory_engine.py) L45-54.                                                                               |
| L1 (episodes) z summary U\|\|A             | ✅ TAK         | `add_episode(summary=f"U:{user_msg[:4000]} \|\| A:{assistant_msg[:4000]}")`. [memory_engine.py](../aihub/memory_engine.py) L207-210.                                                                  |
| L2 (facts) wg reguł keyword/learning/agent | ✅ TAK         | 3 ścieżki: (1) LearningEngine regex rules, (2) keyword fallback ("lubię/preferuję/zawsze/nigdy"), (3) agent_engine.extract_facts_from_text(). [memory_engine.py](../aihub/memory_engine.py) L219-257. |
| retrieve_context: FTS + TF-IDF             | ✅ TAK         | `search_nodes_fts()` → BM25/LIKE, `_vector_rerank()` → TF-IDF sparse cosine. [memory_engine.py](../aihub/memory_engine.py) L280-300.                                                                  |
| dense_hits z vector_engine                 | ✅ TAK         | `from aihub.vector_engine import search as vector_search` w `retrieve_context()`. Linie 333-345 [memory_engine.py](../aihub/memory_engine.py). Opcjonalne (try/except).                               |
| meta_memory.touch_nodes                    | ✅ TAK         | Wołany w `retrieve_context()` L350-354. [memory_engine.py](../aihub/memory_engine.py).                                                                                                                |
| GC (garbage collection)                    | ✅ TAK         | `_maybe_gc()` w `agent_tick()`. [agent_engine.py](../aihub/agent_engine.py) L363-380. Trigger: memory pressure >0.7.                                                                                  |
| \_enforce_caps (L1/L2 limits)              | ✅ TAK         | 20000 per layer. Soft-delete najstarszych/lowest-importance. [memory_engine.py](../aihub/memory_engine.py) L172-193.                                                                                  |

**Scoring pipeline:**

- Blend: `0.72*cosine + 0.18*importance + 0.10*confidence` ([memory_engine.py](../aihub/memory_engine.py) L313-316)
- Psyche modulation: `_get_psyche_modulation()` → imp_mod / conf_mod / max_facts throttle ([memory_engine.py](../aihub/memory_engine.py) L56-82)

**Verdict: ✅ PASS** — Memory system działa zgodnie z opisem, z jednym wyjątkiem: docs twierdzące że vector_engine.search() nie działa są nieprawdziwe.

---

### D) PSYCHE — TRUTH CHECK

| Claim                           | Zweryfikowany? | Dowód                                                                                                                                                                                                     |
| ------------------------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ensure_user (baseline)          | ✅ TAK         | mood=0.55, energy=0.70, focus=0.65, style="ziomek", temperature=0.65, traits: 6 cech. [psyche_engine.py](../aihub/psyche_engine.py) L56-76.                                                               |
| evolve: mood/energy/focus       | ✅ TAK         | mood += role*w * 0.18 _ s _ conf (drift →0.55). energy += role*w * 0.06 _ s _ conf - word*penalty. focus += role_w * 0.05 \_ conf - word_penalty. [psyche_engine.py](../aihub/psyche_engine.py) L119-129. |
| evolve: trait learning          | ✅ TAK         | harsh (neg>pos, neg≥2): directness↑, patience↓, swearing↑, sarcasm↑. friendly (pos>neg, pos≥2): agreeableness↑, patience↑, sarcasm↓. [psyche_engine.py](../aihub/psyche_engine.py) L131-148.              |
| temperature adapts              | ✅ TAK         | `temperature = 0.55 + 0.25*(mood-0.5)`, clamped 0.25-0.95. [psyche_engine.py](../aihub/psyche_engine.py) L150.                                                                                            |
| reflect: topics from frequency  | ✅ TAK         | Liczy freq słów (≥4 chars) z last 20 STM messages, top 12 where count≥2, max 8 topics. [psyche_engine.py](../aihub/psyche_engine.py) L166-177.                                                            |
| reflect → learn_from_reflection | ✅ TAK         | Na końcu reflect() wołany `learn_from_reflection(user_id, out)`. [psyche_engine.py](../aihub/psyche_engine.py) L218-224.                                                                                  |
| Sentiment: polskie keywords     | ✅ TAK         | \_POS: 18 słów, \_NEG: 16 słów, \_INTENSIFIERS: 6 słów. [psyche_engine.py](../aihub/psyche_engine.py) L12-51.                                                                                             |

**Verdict: ✅ PASS** — Psyche system działa dokładnie tak jak opisano.

---

### E) LEARNING — TRUTH CHECK

| Claim                                  | Zweryfikowany? | Dowód                                                                                                                                       |
| -------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| LearningEngine wołany w process_turn   | ✅ TAK         | `memory_engine.process_turn()` L219-237: `LearningEngine().extract_facts_from_message()`.                                                   |
| learn_from_reflection wołany w reflect | ✅ TAK         | `psyche_engine.reflect()` L218-224: `learn_from_reflection(user_id, out)`.                                                                  |
| Regex rules (6 kategorii)              | ✅ TAK         | user_identity, user_preference, user_work, user_goal, technical_fact, constraint. [learning_engine.py](../aihub/learning_engine.py) L31-94. |
| Dedup via hash                         | ✅ TAK         | `_hash_fact()` + `self.learned_facts` set. [learning_engine.py](../aihub/learning_engine.py) L101-106.                                      |
| Keyword fallback                       | ✅ TAK         | Jeśli LearningEngine nic nie znajdzie → keyword fallback. [memory_engine.py](../aihub/memory_engine.py) L239-257.                           |
| Agent engine extract_facts             | ✅ TAK         | agent_engine.py has own `extract_facts_from_text()` heuristic. [agent_engine.py](../aihub/agent_engine.py) L53-93.                          |

**⚠️ UWAGA:** `LearningEngine()` jest tworzony jako **nowa instancja** w `process_turn()` (L223), więc `self.learned_facts` (dedup set) jest **pusty** przy każdym wywołaniu. Dedup in-memory nie działa między wywołaniami. Ale jest też `_learning_engine = LearningEngine()` singleton na dole pliku — ten jest użyty przez public API `learning_engine.process_turn()`. Problem: `memory_engine.process_turn()` tworzy NOWĄ instancję zamiast używać singletona.

**To nie jest bug krytyczny** — fakty nadal mają deterministic ID w `add_fact()` (`_id_for()` hash z treści), więc duplikaty na poziomie DB są upsert-owane. To jest error w logice dedup, ale bezpieczny.

**Verdict: ✅ PASS** (z drobną uwagą o instancji)

---

### F) RESEARCH — TRUTH CHECK

| Claim                                      | Zweryfikowany? | Dowód                                                                                                                                                  |
| ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| research_engine jest importowany i używany | ✅ TAK         | `agent_engine._execute_research()` L344: `from aihub.research_engine import research as do_research`.                                                  |
| Wikipedia API (real)                       | ✅ TAK         | `_fetch_wikipedia()` → opensearch + query/extracts. [research_engine.py](../aihub/research_engine.py) L382-430.                                        |
| DuckDuckGo API (real)                      | ✅ TAK         | `_fetch_duckduckgo()` → instant answer API. [research_engine.py](../aihub/research_engine.py) L432-475.                                                |
| Rate limiting (30s/user)                   | ✅ TAK         | `RESEARCH_RATE_LIMIT_S = 30.0`, `_research_rate` dict. [agent_engine.py](../aihub/agent_engine.py) L314-340.                                           |
| Query dedup cache (300s)                   | ✅ TAK         | `RESEARCH_CACHE_TTL = 300`, skip if same normalized query. [research_engine.py](../aihub/research_engine.py) L251-262.                                 |
| Quality gate (filter_research_text)        | ✅ TAK         | Min 40 chars, boilerplate blacklist, max 800 chars. [research_engine.py](../aihub/research_engine.py) L79-88.                                          |
| Fingerprint dedup                          | ✅ TAK         | SHA256 z (user, backend, query, url). [research_engine.py](../aihub/research_engine.py) L66-73.                                                        |
| HTTP backoff (3 retries)                   | ✅ TAK         | `_http_get_with_backoff()` z delays (0.2, 0.6, 1.5). [research_engine.py](../aihub/research_engine.py) L95-120.                                        |
| research.query task typ                    | ✅ TAK         | `plan_from_text()` tworzy task, `execute_task()` dispatchuje do `_execute_research()`. [agent_engine.py](../aihub/agent_engine.py) L157-165, L217-218. |

**Verdict: ✅ PASS** — Research engine jest w pełni operacyjny.

---

## TABELA PODSUMOWUJĄCA

| SUBSYSTEM              | CLAIM Z DOCS                    | REALITY                                  | STATUS          | DOWÓD                                                 |
| ---------------------- | ------------------------------- | ---------------------------------------- | --------------- | ----------------------------------------------------- |
| Memory STM             | zapisuje i przycina             | ✅ Tak, prune po 200 msg                 | **PASS**        | memory_engine.py L45-54                               |
| Memory L1 (episodes)   | powstaje z summary U\|\|A       | ✅ Tak                                   | **PASS**        | memory_engine.py L207-210                             |
| Memory L2 (facts)      | keyword + learning rules        | ✅ Tak (3 ścieżki)                       | **PASS**        | memory_engine.py L219-257                             |
| Memory FTS             | BM25 + TF-IDF rerank            | ✅ Tak                                   | **PASS**        | memory_engine.py L280-300, db.py search_nodes_fts     |
| Memory FAISS dense     | "NIGDY NIE WOŁANY" (docs)       | ❌ NIEPRAWDA — JEST wołany               | **SEMI**        | memory_engine.py L333-345 (wołany w retrieve_context) |
| Memory Meta            | touch_nodes na retrieve         | ✅ Tak                                   | **PASS**        | memory_engine.py L350-354                             |
| Memory GC              | \_maybe_gc w agent_tick         | ✅ Tak, pressure >0.7                    | **PASS**        | agent_engine.py L363-380                              |
| Psyche evolve          | mood/energy/focus/traits        | ✅ Tak                                   | **PASS**        | psyche_engine.py L99-154                              |
| Psyche reflect         | topics z frequency              | ✅ Tak                                   | **PASS**        | psyche_engine.py L157-193                             |
| Psyche → Learning      | reflect → learn_from_reflection | ✅ Tak                                   | **PASS**        | psyche_engine.py L218-224                             |
| Learning Engine        | wołany z memory + psyche        | ✅ Tak                                   | **PASS**        | memory_engine.py L219-237, psyche_engine.py L218-224  |
| Research Engine        | Wikipedia + DDG real APIs       | ✅ Tak                                   | **PASS**        | research_engine.py L382-475                           |
| Research via agent     | plan → enqueue → execute        | ✅ Tak                                   | **PASS**        | agent_engine.py L157-165, L217-218, L319-361          |
| Agent Worker           | daemon thread co 3.5s           | ✅ Tak                                   | **PASS**        | agent_worker.py, start_worker_once()                  |
| Attention Controller   | rank_messages (>20 msg)         | ✅ Tak                                   | **PASS**        | agent_engine.py L441-449, attention_controller.py     |
| Attention focus_on()   | —                               | ❌ Placeholder (returns [])              | **PLACEHOLDER** | attention_controller.py L166-182                      |
| Knowledge Graph        | in-memory only                  | ⚠️ Nodes=0 po restarcie                  | **SEMI**        | knowledge_graph.py — nie persystuje do DB             |
| Cognitive decide       | intent-based routing            | ✅ Tak                                   | **PASS**        | cognitive_controller.py L145-275                      |
| KnowledgeEvolution     | evolve_all via GC               | ✅ Tak (GC trigger >2000 facts)          | **PASS**        | memory_gc.py L94, knowledge_evolution.py L364         |
| start.sh /health       | czeka na /health                | ❌ Endpoint nie istnieje                 | **FAIL**        | start.sh L224-230 vs main.py (brak /health)           |
| Port config            | 8080 w docs                     | ⚠️ config.py default=8000, start.sh=8080 | **SEMI**        | config.py L9, start.sh L10                            |
| Dead code agent_engine | —                               | ❌ Linie 557-585 unreachable             | **DEAD**        | agent_engine.py L557-585                              |
| \_legacy_api           | —                               | ❌ Nie include'owane                     | **DEAD**        | brak importu w main.py                                |
| Tests count            | 92 PASS                         | Nie zweryfikowane (sandbox)              | **UNVERIFIED**  | —                                                     |

---

## ZNALEZIONE ROZJAZDY I FIX PLAN

### 1. FLOW_DIAGRAMS_EVIDENCE.md twierdzi że vector_engine.search() "NIGDY NIE WOŁANY"

**Reality:** Jest wołany w `memory_engine.retrieve_context()` L335 jako `dense_hits` boost.
**Fix:** Poprawić sekcję w FLOW_DIAGRAMS_EVIDENCE.md — FAISS dense boost AKTYWNY.

### 2. start.sh czeka na `/health` — endpoint nie istnieje

**Reality:** Nie ma `/health` w main.py. Jest `/system/ping` i `/cognitive/health`.
**Fix:** Zmienić w start.sh `wait_health()` z `/health` na `/system/ping`. Minimalny, additive.

### 3. KnowledgeGraph — in-memory, nie persystuje

**Reality:** `KnowledgeGraph` trzyma nodes/edges w dict-ach Pythona. Po restarcie procesu — pusto.
**Impact:** `/cognitive/health` → `graph_stats: {nodes:0}` na fresh start. Nodes zasilane z `memory_engine._feed_knowledge_graph()` w runtime.
**Fix:** Opcionalne — ale docs powinny to dokumentować jasno.

### 4. attention_controller.focus_on() — placeholder

**Reality:** Zwraca `([], f"Focusing on {category}")`. Nikt tego nie woła w runtime.
**Fix:** Nic nie naprawiać (nikt nie woła), ale oznaczyć w docs jako PLACEHOLDER.

### 5. Dead code w agent_engine.py L557-585

**Reality:** Unreachable code po try/except w agent_tick(). Duplikat logiki.
**Fix:** Usunąć dead code (bezpieczne, additive — usunięcie martwego kodu nie zmienia zachowania).

### 6. LearningEngine — nowa instancja zamiast singletona

**Reality:** `memory_engine.process_turn()` tworzy `LearningEngine()` zamiast importować singletona `_learning_engine`.
**Impact:** Dedup in-memory nie działa między wywołaniami. Ale DB upsert (deterministic ID) chroni.
**Fix:** Minimalny — zaimportować singletona zamiast tworzyć nową instancję. Ryzyko: zero.

### 7. Port discrepancy: config.py=8000, start.sh=8080

**Reality:** start.sh nadpisuje port na 8080. config.py ma default 8000 (dla `uvicorn.run()` w `__main__`).
**Impact:** W produkcji start.sh steruje, więc port=8080. Ale jeśli ktoś odpali `python -m aihub.main` bezpośrednio, dostanie port 8000.
**Fix:** Udokumentować w RUNBOOK. Opcjonalnie: zsynchronizować default.

---

## ETAP 2 — DOCS CLEANUP

### Pliki do aktualizacji

| Plik                             | Co zmienione                                                                        |
| -------------------------------- | ----------------------------------------------------------------------------------- |
| `docs/FLOW_DIAGRAMS_EVIDENCE.md` | Sekcja "vector_engine.search NIGDY NIE WOŁANY" → poprawiona na AKTYWNY              |
| `docs/FINAL_STATUS.md`           | Dodano uwagę o KnowledgeGraph in-memory i attention_controller.focus_on placeholder |
| `docs/RUNBOOK.md`                | Ustrukturyzowana sekcja Caddy/HTTPS, LOCAL vs PROD curl, port info                  |
| `docs/LAUNCH.md`                 | Dodano wyraźne rozróżnienie LOCAL/PROD, port info                                   |

---

## CADDY / HTTPS (stan z docs)

Na podstawie docs (LAUNCH.md, RUNBOOK.md, FINAL_CLOSEOUT_REPORT.md):

| Parametr         | Wartość                                                     |
| ---------------- | ----------------------------------------------------------- |
| Produkcja URL    | `https://ahui69.org`                                        |
| Caddy version    | v2.11.1                                                     |
| Caddyfile        | `/etc/caddy/Caddyfile`                                      |
| Let's Encrypt    | auto (ahui69.org)                                           |
| Reverse proxy    | `127.0.0.1:8080`                                            |
| HTTP/2           | ✅                                                          |
| HSTS             | max-age=63072000, includeSubDomains, preload                |
| Security headers | X-Content-Type-Options, X-Frame-Options, Permissions-Policy |

**Sprawdzenie statusu Caddy:**

```bash
systemctl status caddy
caddy validate --config /etc/caddy/Caddyfile
curl -s https://ahui69.org/system/ping
```

> ⚠️ Nie mogę zweryfikować Caddy na żywo (sandbox). Powyższe bazuje na claimach w docs.

---

## KOMENDY WERYFIKACJI PO WSZYSTKIM

```bash
# 1. Testy jednostkowe
cd /root/ai-hub && source .venv/bin/activate
python -m pytest tests/ -v --tb=short 2>&1 | tail -30

# 2. Sanity (jeśli serwer działa)
curl -s http://127.0.0.1:8080/system/ping | python3 -m json.tool
curl -s http://127.0.0.1:8080/cognitive/health | python3 -m json.tool

# 3. Memory round-trip
curl -s -X POST http://127.0.0.1:8080/memory/add \
  -H "Content-Type: application/json" \
  -d '{"user_id":"verify","user_msg":"lubię pizzę hawajską","assistant_msg":"Zanotowałem!","intent":"preference","meta":{}}' | python3 -m json.tool

curl -s -X POST http://127.0.0.1:8080/memory/search \
  -H "Content-Type: application/json" \
  -d '{"user_id":"verify","query":"pizza","limit":5}' | python3 -m json.tool

# 4. Psyche round-trip
curl -s -X POST http://127.0.0.1:8080/psyche/update \
  -H "Content-Type: application/json" \
  -d '{"user_id":"verify","text":"super mega dzięki ziomek!","role":"user"}' | python3 -m json.tool

curl -s -X POST http://127.0.0.1:8080/psyche/reflect \
  -H "Content-Type: application/json" \
  -d '{"user_id":"verify","query":"","limit":10}' | python3 -m json.tool

# 5. Cognitive decide
curl -s -X POST "http://127.0.0.1:8080/cognitive/decide?user_id=verify" \
  -H "Content-Type: application/json" \
  -d '{"message":"wyszukaj co to jest AI","context":{}}' | python3 -m json.tool

# 6. Agent tick
curl -s -X POST http://127.0.0.1:8080/agent/tick/verify | python3 -m json.tool

# 7. HTTPS prod (jeśli Caddy działa)
curl -s https://ahui69.org/system/ping | python3 -m json.tool
curl -s https://ahui69.org/cognitive/health | python3 -m json.tool

# 8. Caddy status
systemctl status caddy
caddy validate --config /etc/caddy/Caddyfile
```

---

## Memory / Psyche / Learning / Research — jak to działa naprawdę

### Memory (pamięć)

Trzy warstwy: **STM** (short-term, max 200 msg), **L1** (epizody — summary turna U||A), **L2** (fakty — z LearningEngine regex, keyword fallback, albo agent_engine heuristic). Szukanie: FTS5 (BM25) + TF-IDF cosine rerank + opcjonalny FAISS dense boost. Każdy retrieve "dotyka" meta_memory (access_count++). GC uruchamiany przez agent_tick jeśli pressure >0.7 (stale ≥90d → delete, ≥30d → archive, >5000 → trim lowest).

### Psyche (psychika)

Każdy user ma: mood, energy, focus, style, temperature + traits (agreeableness, directness, sarcasm, swearing, patience, memory_hunger). `evolve()` liczy sentiment (polskie keyword sets → POS/NEG/INTENSIFIERS), updateuje mood/energy/focus z wagą roli (user=1.0, assistant=0.35), uczy traits-ów (harsh → directness↑, patience↓). Temperature adaptuje się do mood. `reflect()` liczy frequent words z STM → topics, generuje opis stanu po polsku.

### Learning (nauka)

Regex-owe reguły (6 kategorii: identity, preference, work, goal, technical, constraint) wyciągają fakty z wiadomości usera w `process_turn()`. Żeby nie zaśmiecać, throttle przez psyche modulację (max_facts=1-3 zależnie od energy/focus). Fallback: keyword ("lubię", "preferuję" etc). Meta-learning: `reflect()` → `learn_from_reflection()` → dodaje topics/recommendations jako L2 fakty.

### Research (badania)

Prawdziwe API: Wikipedia (opensearch + extracts) i DuckDuckGo (instant answer). Odpalany przez agent_engine gdy w tekście usera jest keyword "wyszukaj/research/zbadaj/sprawdź temat". Rate limit 30s/user. Cache 300s na normalized query. Quality gate: min 40 znaków, blacklist boilerplate, max 800 znaków. HTTP backoff: 3 retries (0.2/0.6/1.5s). Wyniki zapisywane jako L2 fakty z fingerprint dedup.

---

_Raport wygenerowany statyczną analizą kodu. Curl testy wymaga odblokowania terminala._
