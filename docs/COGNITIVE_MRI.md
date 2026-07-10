# COGNITIVE MRI — AI-Hub

**Repozytorium:** `/root/ai-hub`
**Zakres:** tylko analiza statyczna i przepływów runtime, bez zmian kodu
**Tryb:** brutalnie szczery
**Data analizy:** 2026-03-07

---

## Jak wykonano MRI (metodologia)

Ta analiza została oparta na 4 osiach, których wymagałeś:

1. **AST / callgraph (praktyczny):**
    - mapowanie `def/async def/class` + call-site'ów,
    - mapowanie dispatchu (`handler` mapy, switch if/elif, route → function).
2. **Import graph:**
    - relacje `from ... import ...` i importy wewnątrz funkcji,
    - identyfikacja faktycznych zależności runtime.
3. **Runtime flow:**
    - ścieżki endpointów FastAPI (`main.py`, `agent_api.py`) do silników.
4. **Agent loop analysis:**
    - pętla background (`agent_worker` → `agent_engine.agent_tick`),
    - pętla cognitive (`agent_loop` + `AgentExecutor`) i jej integralność.

Wszystkie wnioski mają odwołania **plik:linia**.

---

## 1) BRAIN REGIONS MAP

Poniżej podział AI-Hub na „regiony mózgu” oraz rola każdego regionu.

### 1.1 SENSORY SYSTEM

**Rola:** odbiór sygnałów wejściowych (HTTP, STM, web).

**Moduły i funkcje:**

- `aihub/main.py`:
  - `add_turn()` (`/turn`) [aihub/main.py:133]
  - `memory_add()` (`/memory/add`) [aihub/main.py:193]
  - `web_fetch()` (`/web/fetch`) [aihub/main.py:318]
- `aihub/agent_engine.py`:
  - `_pull_new_stm()` [aihub/agent_engine.py:28]
- `aihub/agent_loop.py`:
  - `get_pending_messages()` [aihub/agent_loop.py:50]
- `aihub/web_tools.py`:
  - `fetch_url()` [aihub/web_tools.py:12]

**Ocena regionu:**

- Realny i aktywny.
- To nie jest „inteligencja”, tylko sensoryka i I/O.

---

### 1.2 INPUT PROCESSING

**Rola:** normalizacja wejścia, ekstrakcja prostych sygnałów, heurystyki.

**Moduły i funkcje:**

- `aihub/attention_controller.py`:
  - `rank_messages()` [aihub/attention_controller.py:56]
  - `_calculate_urgency()` [aihub/attention_controller.py:115]
  - `_calculate_relevance()` [aihub/attention_controller.py:133]
- `aihub/psyche_engine.py`:
  - `analyze_sentiment()` [aihub/psyche_engine.py:96]
- `aihub/learning_engine.py`:
  - `extract_facts_from_message()` [aihub/learning_engine.py:132]
- `aihub/agent_engine.py`:
  - `extract_facts_from_text()` [aihub/agent_engine.py:53]
  - `plan_from_text()` [aihub/agent_engine.py:103]
- `aihub/cognitive_controller.py`:
  - `_extract_intent()` [aihub/cognitive_controller.py:279]

**Ocena regionu:**

- Bardzo silnie **rule-based**.
- Wysoka reaktywność, niska głębokość semantyczna.

---

### 1.3 CONTEXT BUILDER

**Rola:** budowa kontekstu roboczego z pamięci i sygnałów pomocniczych.

**Moduły i funkcje:**

- `aihub/memory_engine.py`:
  - `retrieve_context()` [aihub/memory_engine.py:324]
  - `_vector_rerank()` [aihub/memory_engine.py:306]
  - `touch_nodes` call [aihub/memory_engine.py:408]
  - `graph_hits` build [aihub/memory_engine.py:385]
- `aihub/cognitive_controller.py`:
  - `decision_context = {...}` [aihub/cognitive_controller.py:178]
  - `decision_context.update(request.context)` [aihub/cognitive_controller.py:193]
  - `predict_next_action(...)` [aihub/cognitive_controller.py:196]

**Ocena regionu:**

- Technicznie bogaty, ale niespójnie konsumowany downstream.

---

### 1.4 MEMORY SYSTEM

**Rola:** przechowywanie i odczyt STM/LTM + rankingi i porządki.

**Moduły i funkcje:**

- `aihub/memory_engine.py`:
  - `add_stm()` [aihub/memory_engine.py:43]
  - `add_episode()` [aihub/memory_engine.py:165]
  - `add_fact()` [aihub/memory_engine.py:182]
  - `process_turn()` [aihub/memory_engine.py:234]
  - `retrieve_context()` [aihub/memory_engine.py:324]
- `aihub/db.py`:
  - `upsert_node()` [aihub/db.py:316]
  - `search_nodes_fts()` [aihub/db.py:372]
  - `get_stm()` [aihub/db.py:281]
- `aihub/meta_memory.py`:
  - `touch_nodes()` [aihub/meta_memory.py:361]
  - `check_stale()` [aihub/meta_memory.py:140,401]
  - `rank_facts()` [aihub/meta_memory.py:219,406]
- `aihub/memory_gc.py`:
  - `collect_garbage()` [aihub/memory_gc.py:44]

**Ocena regionu:**

- Realny i istotny dla danych.
- Tylko częściowo wpływa na decyzje w pętli cognitive.

---

### 1.5 KNOWLEDGE SYSTEM

**Rola:** tworzenie i utrwalanie wiedzy, relacje, deduplikacja.

**Moduły i funkcje:**

- `aihub/learning_engine.py`:
  - `extract_facts_from_message()` [aihub/learning_engine.py:132]
  - `process_turn()` [aihub/learning_engine.py:170]
  - `learn_from_reflection()` [aihub/learning_engine.py:280]
- `aihub/research_engine.py`:
  - `research()` [aihub/research_engine.py:226]
  - `_fetch_search_results()` [aihub/research_engine.py:374]
- `aihub/knowledge_graph.py`:
  - `add_node()` [aihub/knowledge_graph.py:61]
  - `query_nodes()` [aihub/knowledge_graph.py:328]
  - `persist_node()` [aihub/knowledge_graph.py:257]
- `aihub/knowledge_evolution.py`:
  - `deduplicate()` [aihub/knowledge_evolution.py:102]
  - `evolve_all()` [aihub/knowledge_evolution.py:365]

**Ocena regionu:**

- Realnie zapisuje wiedzę.
- Część jest aktywna, część nadal dekoracyjna (szczegóły dalej).

---

### 1.6 DECISION SYSTEM

**Rola:** wybór typu akcji.

**Moduły i funkcje:**

- `aihub/cognitive_controller.py`:
  - `decide()` [aihub/cognitive_controller.py:148]
  - `_extract_intent()` [aihub/cognitive_controller.py:279]
  - `_decide_query()` [aihub/cognitive_controller.py:298]
  - `_decide_learn()` [aihub/cognitive_controller.py:340]
  - `_decide_research()` [aihub/cognitive_controller.py:378]
  - `_decide_action()` [aihub/cognitive_controller.py:416]
- `aihub/conflict_detector.py`:
  - `check_conflict()` [aihub/conflict_detector.py:62]

**Ocena regionu:**

- To jest formalny „rdzeń decyzji”, ale z krytycznym bottleneckiem mapowania action_type.

---

### 1.7 ACTION SYSTEM

**Rola:** wykonanie decyzji na realnych subsystemach.

**Moduły i funkcje:**

- `aihub/agent_executor.py`:
  - `execute()` [aihub/agent_executor.py:12]
  - `_exec_query()` [aihub/agent_executor.py:29]
  - `_exec_learn()` [aihub/agent_executor.py:42]
  - `_exec_research()` [aihub/agent_executor.py:52]
  - `_exec_action()` [aihub/agent_executor.py:59]
- `aihub/agent_engine.py`:
  - `execute_task()` [aihub/agent_engine.py:193]
  - `_execute_web_fetch()` [aihub/agent_engine.py:234]
  - `_execute_fs_write()` [aihub/agent_engine.py:265]
  - `_execute_snapshot()` [aihub/agent_engine.py:294]
  - `_execute_research()` [aihub/agent_engine.py:319]

**Ocena regionu:**

- Dwa wykonawcze „ramiona” agenta (cognitive path i tick path).
- Tick path jest bardziej przewidywalny i produkcyjny.

---

### 1.8 REFLECTION SYSTEM

**Rola:** introspekcja i meta-learning.

**Moduły i funkcje:**

- `aihub/psyche_engine.py`:
  - `reflect()` [aihub/psyche_engine.py:181]
- `aihub/learning_engine.py`:
  - `learn_from_reflection()` [aihub/learning_engine.py:280]

**Ocena regionu:**

- Istnieje i zapisuje efekty do pamięci.
- Bez silnego domknięcia pętli do planner/decision policy.

---

## 2) THOUGHT GENERATION POINT (najważniejszy punkt)

### 2.1 Gdzie naprawdę powstaje decyzja?

**Główny punkt decyzyjny:** `CognitiveController.decide()` [aihub/cognitive_controller.py:148].

To tutaj:

1. tworzony jest `decision_context` [aihub/cognitive_controller.py:178],
2. nadpisywany contextem requestu [aihub/cognitive_controller.py:193],
3. wykonywane są predykcje [aihub/cognitive_controller.py:196],
4. wybierana jest gałąź `_decide_*` [aihub/cognitive_controller.py:205-217],
5. wykonywana jest walidacja konfliktów [aihub/cognitive_controller.py:227].

### 2.2 Problem krytyczny „thought-to-action”

`CognitiveController` zwraca action types:

- `memory_search` [aihub/cognitive_controller.py:331]
- `learn` [aihub/cognitive_controller.py:371]
- `research` [aihub/cognitive_controller.py:408]
- `execute` [aihub/cognitive_controller.py:447]

Natomiast `AgentExecutor.execute()` mapuje tylko:

- `query`, `learn`, `research`, `action` [aihub/agent_executor.py:15-19]

Skutek:

- `memory_search` → **unknown action_type** [aihub/agent_executor.py:23-24]
- `execute` → **unknown action_type** [aihub/agent_executor.py:23-24]

To znaczy, że część decyzji „powstaje”, ale nie jest wykonywana przez właściwy handler.

### 2.3 Drugi punkt decyzyjny (reaktywny)

W tle działa alternatywny „mózg reaktywny”:

- `agent_engine.plan_from_text()` [aihub/agent_engine.py:103]

To decyzje typu if/keyword:

- URL + `sprawdź` → `web.fetch`
- `zapisz:` + `::` → `fs.write`
- `snapshot|backup|kopia` → `system.snapshot`
- `wyszukaj|research|znajdź info` → `research.query`

**Wniosek:** system ma 2 generatory decyzji:

1. cognitive (`cognitive_controller`),
2. reaktywny (`agent_engine.plan_from_text`).

---

## 3) CONTEXT CONSTRUCTION (pipeline)

### 3.1 Pipeline kontekstu pamięciowego (faktyczny)

`query`
→ `retrieve_context(user_id, query, limit)` [aihub/memory_engine.py:324]
→ pobranie STM [aihub/memory_engine.py:325]
→ FTS L1 [aihub/memory_engine.py:328]
→ FTS L2 [aihub/memory_engine.py:330]
→ TF-IDF rerank [aihub/memory_engine.py:333-334]
→ blend score z importance/confidence [aihub/memory_engine.py:347-352]
→ FAISS dense hits [aihub/memory_engine.py:369]
→ KG `query_nodes` → `graph_hits` [aihub/memory_engine.py:387-392]
→ meta_memory `touch_nodes` [aihub/memory_engine.py:404-408]
→ final context dict [aihub/memory_engine.py:423-430]

### 3.2 Gdzie context trafia?

- `/memory/search` endpoint: `ctx = retrieve_context(...)` [aihub/main.py:214]
- `/psyche/reflect`: `ctx = retrieve_context(...)` [aihub/main.py:178]
- `AgentExecutor._exec_query`: `retrieve_context(...)` [aihub/agent_executor.py:34]

### 3.3 Co jest tracone po drodze?

`retrieve_context` zwraca `dense_hits` i `graph_hits` [aihub/memory_engine.py:428-429],
ale `MemorySearchOut` model nie ma tych pól [aihub/models.py:40-47].

Efekt:

- przy `/memory/search` sygnały `dense_hits` i `graph_hits` są obliczane,
- ale nie przechodzą przez response model.

To jest **PARTIAL DEAD CONTEXT CHANNEL** (nie całkowity dead, bo `_exec_query` może zwrócić pełny context).

---

## 4) KNOWLEDGE CREATION

### 4.1 LearningEngine

Nowa wiedza jest tworzona przez regex extraction:

- `extract_facts_from_message()` [aihub/learning_engine.py:132]
- zapisywana `add_fact(...)` [aihub/learning_engine.py:201,228]
- `learn_from_reflection()` też dodaje fakty [aihub/learning_engine.py:298,317]

**Czy to nowa wiedza?**

- Tak, ale regułowa/heurystyczna.
- Nie ma modelowego reasoningu, to wzorce i walidacja regex.

### 4.2 ResearchEngine

Nowa wiedza jest tworzona z webu:

- `research()` [aihub/research_engine.py:226]
- źródła: Brave, Wikipedia, DuckDuckGo [aihub/research_engine.py:374]
- ekstrakcja zdań + quality gate [aihub/research_engine.py:174,79]
- zapis do pamięci przez `add_fact` [aihub/research_engine.py:290]

**Czy to nowa wiedza?**

- Tak, realna ingestia external data.

### 4.3 MemoryEngine

`process_turn()` tworzy epizody i fakty:

- `add_episode()` [aihub/memory_engine.py:165]
- `add_fact()` [aihub/memory_engine.py:182]
- oraz wzbogaca KG i persistuje node [aihub/memory_engine.py:116-118,133,135]

### 4.4 Reflection

`psyche.reflect()`:

- buduje tematy z częstotliwości słów [aihub/psyche_engine.py:186-195],
- wywołuje `learn_from_reflection` [aihub/psyche_engine.py:220].

**Ocena:**

- wiedza powstaje,
- ale pętla wpływu tej wiedzy na policy decision jest ograniczona.

---

## 5) MEMORY CONSOLIDATION

### 5.1 Czy system decyduje co pamiętać?

**Tak, częściowo.**

Mechanizmy:

- hard caps i przycinanie po importance/confidence/ts [aihub/memory_engine.py:205-231]
- GC + archiwizacja [aihub/memory_gc.py:44,138]
- stale detection (meta_memory) [aihub/meta_memory.py:140]
- deduplikacja TF-IDF (`knowledge_evolution`) [aihub/knowledge_evolution.py:102]

### 5.2 Co jest konsolidacją „aktywną”?

- `_maybe_gc()` w `agent_engine` [aihub/agent_engine.py:365]
- wywołanie `collect_garbage` gdy pressure > 0.7 [aihub/agent_engine.py:379]
- `collect_garbage()` robi: stale delete, archive, pressure relief, evolve, vacuum [aihub/memory_gc.py:63-106]

### 5.3 Co jest tylko scoringiem dekoracyjnym?

- `meta_memory.rank_facts()` [aihub/meta_memory.py:219]
- `meta_memory.generate_report()` [aihub/meta_memory.py:307]
- `meta_memory.compute_overall_priority()` [aihub/meta_memory.py:178]

Te funkcje nie są krytyczną częścią runtime decision path.

---

## 6) INTELLIGENCE PATHWAYS

### 6.1 memory → decision

- W cognitive path: pośrednio słabo.
- `CognitiveController._decide_query` zwraca `memory_search` [aihub/cognitive_controller.py:331],
  ale executor nie obsługuje `memory_search` [aihub/agent_executor.py:15-19,23-24].
- W tick path: memory używana głównie jako źródło sygnałów i zapis efektów.

**Status:** PARTIAL + BROKEN MAPPING.

### 6.2 psyche → decision

- `psyche_state` trafia do request context w `agent_loop` [aihub/agent_loop.py:175-182]
- cognitive `adjusted_confidence` używa focus/energy [aihub/cognitive_controller.py:322-324,363-365,439-441]

**Status:** REAL INFLUENCE (na confidence, nie na głębokie reasoningi).

### 6.3 research → decision

- cognitive może zwrócić action `research` [aihub/cognitive_controller.py:401-408]
- executor ma handler `_exec_research` [aihub/agent_executor.py:52]
- tick path wykonuje research.query task [aihub/agent_engine.py:319]

**Status:** REAL INFLUENCE + REAL ACTION.

### 6.4 learning → decision

- learning zapisuje fakty [aihub/learning_engine.py:201,228]
- brak bezpośredniego użycia tych faktów w `CognitiveController.decide()`

**Status:** WEAK INDIRECT INFLUENCE.

### 6.5 prediction → decision

- `predict_next_action(...)` wywoływane [aihub/cognitive_controller.py:196]
- wynik ląduje w `decision_context["predictions"]` [aihub/cognitive_controller.py:198]
- brak jawnego użycia tego pola w `_decide_*`.

**Status:** SIGNAL GENERATED, MINIMAL EXECUTION IMPACT.

---

## 7) REACTIVE VS COGNITIVE SYSTEM

### 7.1 Reactive (if → action)

1. `agent_engine.plan_from_text()` [aihub/agent_engine.py:103]
2. `attention_controller` keyword urgency/relevance [aihub/attention_controller.py:115,133]
3. `psyche_engine.analyze_sentiment` lexicon scoring [aihub/psyche_engine.py:96]
4. `learning_engine` regex extraction [aihub/learning_engine.py:132]

To wszystko głównie regułowe i heurystyczne.

### 7.2 Cognitive (context → reasoning → decision)

1. `CognitiveController.decide()` [aihub/cognitive_controller.py:148]
2. conflict validation [aihub/cognitive_controller.py:227 + conflict_detector.py:62]
3. resource gating [aihub/cognitive_controller.py:106]
4. contextual confidence modulation [aihub/cognitive_controller.py:322-324 etc.]

**Uwaga:** reasoning depth jest ograniczony przez keyword intent i mapping issues.

### 7.3 Najuczciwsza klasyfikacja

- 70%: reactive / heuristic orchestration,
- 20%: formal cognitive control shell,
- 10%: real adaptive influence (psyche + memory pressure + conflict checks).

---

## 8) COGNITIVE BOTTLENECK

### Największy hamulec inteligencji: **SEMANTIC ACTION MAPPING MISMATCH**

**Dowód:**

- `memory_search` generowane przez decision engine [aihub/cognitive_controller.py:331]
- `execute` generowane przez decision engine [aihub/cognitive_controller.py:447]
- executor przyjmuje tylko `query|learn|research|action` [aihub/agent_executor.py:15-19]
- unknown action path [aihub/agent_executor.py:23-24]

**Skutek systemowy:**

- część decyzji „rodzi się” poprawnie,
- ale nie przekłada się na wykonanie,
- więc architektura poznawcza traci sprawczość.

### Drugi hamulec

`/agent/loop` route:

- endpoint jest sync `def` [aihub/agent_api.py:88],
- zwraca `run_loop(...)` coroutine [aihub/agent_api.py:93],
- `run_loop` jest `async def` [aihub/agent_loop.py:247].

To potencjalnie uszkadza główną drogę cognitive-loop przez API.

---

## 9) KNOWLEDGE GRAPH INFLUENCE

### 9.1 Czy graph wpływa na decyzje?

**Bezpośrednio na `CognitiveController.decide()`:** prawie nie.

**Pośrednio na context retrieval:** tak, częściowo.

- `memory_engine` zapisuje node do KG + DB [aihub/memory_engine.py:116-118,133,135]
- `retrieve_context` robi `query_nodes` i buduje `graph_hits` [aihub/memory_engine.py:389-392]

### 9.2 Co jest dekoracyjne?

- `load_from_db()` jest zdefiniowane [aihub/knowledge_graph.py:289], ale nie wywoływane nigdzie.
- `persist_edge()` zdefiniowane [aihub/knowledge_graph.py:272], ale brak call-sites.

### 9.3 Werdykt dla graph

`graph = PARTIALLY OPERATIONAL REASONING AUXILIARY`
(nie czysto dekoracyjny, ale też nie centralny reasoning engine).

---

## 10) META MEMORY EFFECT

### 10.1 Czy ranking meta_memory zmienia kontekst?

- `touch_nodes()` jest wywoływane w retrieval [aihub/memory_engine.py:408].
- Ale finalne rankowanie kontekstu używa:
  - TF-IDF score +
  - `importance` + `confidence` [aihub/memory_engine.py:347-352].
- Nie używa `overall_priority` z `memory_meta`.

### 10.2 Czy meta_memory wpływa na decyzję?

- `check_stale()` używane do `memory_pressure` w cognitive [aihub/cognitive_controller.py:459].
- `check_stale()` używane też przez GC [aihub/memory_gc.py:67].

### 10.3 Werdykt

`meta_memory = HYBRID`:

- `touch/check_stale` → aktywne,
- `rank_facts/overall_priority/report` → w dużej mierze dekoracyjny scoring.

---

## 11) INTELLIGENCE CORE

### Kandydaci

1. `cognitive_controller.py` — formalny „mózg decyzyjny”.
2. `agent_engine.py` — praktyczny „mózg wykonawczy” w tle.
3. `memory_engine.py` — „kora pamięci i kontekstu”.

### Werdykt rdzenia

**Rdzeń inteligencji operacyjnej:**

- `agent_engine.agent_tick()` + `memory_engine.retrieve_context/process_turn()`.

**Rdzeń inteligencji deklarowanej:**

- `cognitive_controller.decide()`.

System ma **split-brain architecture**:

- jeden mózg decyduje formalnie,
- drugi robi większość roboty wykonawczej.

---

## 12) COGNITIVE DEPTH SCORE

Skala:
1 = API wrapper
2 = memory API
3 = RAG system
4 = reactive agent
5 = autonomous agent
6 = cognitive architecture
7 = proto-general intelligence

### Ocena: **4.3 / 7**

**Dlaczego nie 5+?**

- keyword-first intent extraction [aihub/cognitive_controller.py:279]
- action mapping mismatch [aihub/cognitive_controller.py:331,447 vs agent_executor.py:15-19]
- route loop instability (`/agent/loop`) [aihub/agent_api.py:88,93]

**Dlaczego nie 3?**

- ma działające pętle agenta i wykonanie akcji (`agent_engine`) [aihub/agent_engine.py:389]
- ma żywy mechanizm research ingestion [aihub/research_engine.py:226]
- ma pamięć wielowarstwową + GC + meta sygnały [aihub/memory_engine.py, aihub/memory_gc.py]

---

## 13) SYSTEM BRAIN DIAGRAM

```text
INPUT
  │
  ├─ HTTP /turn, /memory/add, /agent/* (main.py, agent_api.py)
  ├─ STM pull (_pull_new_stm / get_pending_messages)
  ▼
MEMORY
  ├─ add_stm / add_episode / add_fact (memory_engine.py)
  ├─ FTS + TF-IDF + dense + graph_hits (retrieve_context)
  └─ meta touch + stale flags (meta_memory)
  ▼
PSYCHE
  ├─ analyze_sentiment / evolve (psyche_engine.py)
  └─ confidence modulation inputs to cognitive controller
  ▼
CONTEXT BUILDER
  ├─ decision_context = {...}
  ├─ request context merge
  └─ predictions appended
  ▼
DECISION ENGINE
  ├─ _extract_intent (keyword)
  ├─ _decide_query/learn/research/action
  ├─ conflict detector
  └─ decision result (action_type, params)
  ▼
AGENT ACTION
  ├─ path A: agent_loop -> AgentExecutor.execute
  │    └─ mismatch for memory_search/execute
  └─ path B: agent_tick -> execute_task (web/fs/snapshot/research)
  ▼
REFLECTION
  ├─ psyche.reflect
  └─ learning.learn_from_reflection
  ▼
MEMORY UPDATE
  ├─ add_fact/add_episode
  ├─ KG persist_node
  └─ GC / dedup / archive
```

---

## 14) COGNITIVE MRI SUMMARY

### Gdzie system „myśli”

- formalnie: `CognitiveController.decide()` [aihub/cognitive_controller.py:148]
- praktycznie: `agent_engine.plan_from_text()` + `agent_tick()` [aihub/agent_engine.py:103,389]

### Gdzie system reaguje

- wszędzie gdzie są reguły if/keyword:
  - intent extraction,
  - attention scoring,
  - sentiment lexicon,
  - learning regex.

### Gdzie system zapisuje wiedzę

- `memory_engine.add_fact/add_episode` [aihub/memory_engine.py:165,182]
- `learning_engine.process_turn` [aihub/learning_engine.py:170]
- `research_engine.research` [aihub/research_engine.py:226]
- `knowledge_graph.persist_node` via memory_engine [aihub/memory_engine.py:133,135]

### Gdzie system symuluje inteligencję

1. predykcje bez silnego wpływu na action routing,
2. meta_memory scoring bez użycia `overall_priority` w retrieval ranking,
3. cognitive action types niespójne z executor mappingiem.

---

# BRUTAL TRUTH

1. **System ma prawdziwe komponenty inteligentne, ale nie ma spójnej osi wykonawczej.**
2. **Największe marnowanie potencjału jest na styku decyzja → wykonanie.**
3. **Tick-path (`agent_engine`) robi realną robotę częściej niż cognitive-loop przez API.**
4. **Knowledge graph już nie jest tylko atrapą, ale jeszcze nie jest centralnym reasoning graph.**
5. **Meta-memory działa bardziej jako housekeeping niż jako inteligentny selector kontekstu.**

Największy potencjał:

- połączyć split-brain,
- ujednolicić action_type contract,
- domknąć pętlę retrieval → decision → execution → reflection.

---

## APPENDIX A — INTELLIGENCE PATH MATRIX

Legenda:

- **R** = read (czyta sygnał)
- **W** = write (zapisuje)
- **D** = wpływa na decyzję
- **A** = wpływa na akcję
- **L** = głównie logi/metryki

| Subsystem                             |   R |   W |   D |   A |   L | Uwagi                                        |
| ------------------------------------- | --: | --: | --: | --: | --: | -------------------------------------------- |
| memory_engine.process_turn            |  ✅ |  ✅ |  ⚠️ |  ⚠️ |  ✅ | Tworzy fakty/epizody, pośredni wpływ         |
| memory_engine.retrieve_context        |  ✅ |  ✅ |  ⚠️ |  ✅ |  ✅ | Context realny, ale nie wszędzie konsumowany |
| meta_memory.touch_nodes               |  ✅ |  ✅ |  ⚠️ |  ❌ |  ✅ | Aktualizuje usage/freshness                  |
| meta_memory.rank_facts                |  ✅ |  ✅ |  ❌ |  ❌ |  ❌ | Brak użycia w głównym rankingu contextu      |
| psyche_engine.evolve                  |  ✅ |  ✅ |  ✅ |  ⚠️ |  ✅ | Modyfikuje confidence pathway                |
| learning_engine.extract_facts         |  ✅ |  ✅ |  ⚠️ |  ⚠️ |  ✅ | Passive/indirect learning                    |
| research_engine.research              |  ✅ |  ✅ |  ✅ |  ✅ |  ✅ | Realny wpływ (research action)               |
| prediction_engine.predict_next_action |  ✅ |  ✅ |  ⚠️ |  ❌ |  ✅ | Predictions dodane do contextu, słabe użycie |
| attention_controller.rank_messages    |  ✅ |  ❌ |  ✅ |  ✅ |  ✅ | Selektor wiadomości                          |
| knowledge_graph.query_nodes           |  ✅ |  ❌ |  ⚠️ |  ⚠️ |  ❌ | Wchodzi do graph_hits                        |
| knowledge_graph.load_from_db          |  ❌ |  ✅ |  ❌ |  ❌ |  ❌ | Nieużywany runtime                           |
| agent_engine.agent_tick               |  ✅ |  ✅ |  ✅ |  ✅ |  ✅ | Produkcyjny loop reaktywny                   |
| cognitive_controller.decide           |  ✅ |  ✅ |  ✅ |  ⚠️ |  ✅ | Rdzeń decyzji, ale mapping mismatch          |
| agent_executor.execute                |  ✅ |  ❌ |  ❌ |  ✅ |  ✅ | Real execution dispatch                      |
| agent_api.agent_loop route            |  ✅ |  ❌ |  ❌ |  ❌ |  ❌ | Sync route zwraca coroutine                  |

---

## APPENDIX B — COGNITIVE CONTRACT CHECK (kluczowe niespójności)

### B.1 Decision → Executor action map

- Cognitive emits:
  - `memory_search` [aihub/cognitive_controller.py:331]
  - `learn` [aihub/cognitive_controller.py:371]
  - `research` [aihub/cognitive_controller.py:408]
  - `execute` [aihub/cognitive_controller.py:447]

- Executor accepts:
  - `query` [aihub/agent_executor.py:16]
  - `learn` [aihub/agent_executor.py:17]
  - `research` [aihub/agent_executor.py:18]
  - `action` [aihub/agent_executor.py:19]

- Unknown handling:
  - logger warning + error return [aihub/agent_executor.py:23-24]

### B.2 Route async contract

- Route:
  - `def agent_loop(data: dict)` [aihub/agent_api.py:88]
- Return:
  - `return run_loop(...)` [aihub/agent_api.py:93]
- Target:
  - `async def run_loop(...)` [aihub/agent_loop.py:247]

Contract risk: sync endpoint zwracający coroutine.

### B.3 Task completion contract

- Call:
  - `complete_task(user_id, t["id"])` [aihub/agent_engine.py:513]
- Signature:
  - `complete_task(task_id: int, ok: bool, error: str="")` [aihub/agent_db.py:141]

To jest semantycznie odwrócony argument order.

---

## APPENDIX C — RUNTIME FLOWS (przekrojowo)

### C.1 Flow: `/memory/add`

1. `main.memory_add` [aihub/main.py:193]
2. `ensure_user` [aihub/main.py:197]
3. `evolve(user_msg)` [aihub/main.py:198]
4. `evolve(assistant_msg)` [aihub/main.py:199]
5. `process_turn` [aihub/main.py:200]
6. `remember_turn` [aihub/memory_engine.py:237]
7. `add_stm user` [aihub/memory_engine.py:238]
8. `add_stm assistant` [aihub/memory_engine.py:239]
9. `add_episode` [aihub/memory_engine.py:244]
10. `learning extract` [aihub/memory_engine.py:256]
11. `add_fact loop` [aihub/memory_engine.py:262-269]
12. fallback keywords [aihub/memory_engine.py:277-301]
13. response IDs [aihub/memory_engine.py:303-307]

### C.2 Flow: `/cognitive/decide`

1. `main.cognitive_decide` [aihub/main.py:377]
2. build `DecisionRequest` [aihub/main.py:386]
3. `cognitive_controller.decide` [aihub/main.py:395]
4. `decision_context` build [aihub/cognitive_controller.py:178]
5. `decision_context.update` [aihub/cognitive_controller.py:193]
6. predictions [aihub/cognitive_controller.py:196]
7. `_extract_intent` [aihub/cognitive_controller.py:279]
8. branch `_decide_*` [aihub/cognitive_controller.py:205-217]
9. conflict check [aihub/cognitive_controller.py:227]
10. return action_type/params [aihub/main.py:409-414]

### C.3 Flow: `agent_worker` background

1. startup hook `start_worker_once` [aihub/main.py:93]
2. worker loop `_run_loop` [aihub/agent_worker.py:30]
3. periodic `asyncio.run(agent_tick(...))` [aihub/agent_worker.py:78-79]
4. `agent_tick` pull stm [aihub/agent_engine.py:416]
5. attention cutoff [aihub/agent_engine.py:431-437]
6. evolve+fact extraction [aihub/agent_engine.py:455-472]
7. enqueue tasks [aihub/agent_engine.py:475]
8. claim + execute [aihub/agent_engine.py:508-511]
9. complete_task call [aihub/agent_engine.py:513]
10. maybe_gc [aihub/agent_engine.py:527]

### C.4 Flow: `agent_loop` cognitive path

1. `agent_loop.agent_cycle` [aihub/agent_loop.py:144]
2. `rank_messages` [aihub/agent_loop.py:161]
3. top3 messages [aihub/agent_loop.py:166]
4. build `DecisionRequest` [aihub/agent_loop.py:175]
5. `cognitive_controller.decide` [aihub/agent_loop.py:192]
6. `process_decision` [aihub/agent_loop.py:197]
7. conflict check [aihub/agent_loop.py:76]
8. `_execute_action` [aihub/agent_loop.py:100,130]
9. `_executor.execute` [aihub/agent_loop.py:135]
10. event `cycle.decision` [aihub/agent_loop.py:204]

---

## APPENDIX D — SIGNAL AUDIT (DEAD / LIVE / PARTIAL)

### D.1 Signal: `predictions`

- generated: yes [aihub/cognitive_controller.py:196]
- stored in context: yes [aihub/cognitive_controller.py:198]
- consumed downstream in `_decide_*`: no explicit read
- status: **PARTIAL DEAD SIGNAL**

### D.2 Signal: `overall_priority` (meta_memory)

- computed: yes [aihub/meta_memory.py:178]
- ranking api: yes [aihub/meta_memory.py:219]
- used in `retrieve_context` ranking: no
- status: **DEAD SIGNAL (for context ranking)**

### D.3 Signal: `graph_hits`

- built: yes [aihub/memory_engine.py:385-392]
- returned in raw ctx: yes [aihub/memory_engine.py:429]
- exposed by `/memory/search` model: no [aihub/models.py:40-47]
- status: **PARTIAL DEAD OUTPUT CHANNEL**

### D.4 Signal: `dense_hits`

- built: yes [aihub/memory_engine.py:367-383]
- returned in raw ctx: yes [aihub/memory_engine.py:428]
- exposed by `/memory/search`: no [aihub/models.py:40-47]
- status: **PARTIAL DEAD OUTPUT CHANNEL**

### D.5 Signal: `confidence` in decisions

- computed: yes (`adjusted_confidence`) [aihub/cognitive_controller.py:325,366,400,442]
- logged: yes [aihub/cognitive_controller.py:241]
- influences action execution path: pośrednio, ale mapping issues osłabiają wpływ
- status: **LIVE BUT DEGRADED BY CONTRACT MISMATCH**

### D.6 Signal: `memory_pressure`

- computed from stale count [aihub/cognitive_controller.py:455-463]
- used in research_type selection [aihub/cognitive_controller.py:398-399]
- status: **LIVE SIGNAL**

### D.7 Signal: `usage_score` in meta_memory

- updated by touch [aihub/meta_memory.py:361-386]
- not used in retrieve ranking blend [aihub/memory_engine.py:347-352]
- status: **DEAD IN RETRIEVAL POLICY**

---

## APPENDIX E — IMPORT GRAPH SNAPSHOT (selected edges)

Format: `MODULE -> IMPORT`.

- `main.py -> aihub.config(APP_NAME,HOST,PORT)` [aihub/main.py:15]
- `main.py -> aihub.db(append_event,init_db)` [aihub/main.py:16]
- `main.py -> .agent_worker(start_worker_once)` [aihub/main.py:43]
- `main.py -> .cognitive_controller(CognitiveController,DecisionRequest)` [aihub/main.py:45]
- `main.py -> .memory_engine(add_stm,health,process_turn,retrieve_context)` [aihub/main.py:49]
- `main.py -> .metrics_engine(...)` [aihub/main.py:50]
- `main.py -> .psyche_engine(ensure_user,evolve,reflect)` [aihub/main.py:56]
- `main.py -> .system_ops(...)` [aihub/main.py:58]
- `main.py -> .web_tools(fetch_url)` [aihub/main.py:59]

- `agent_loop.py -> aihub.agent_executor(AgentExecutor)` [aihub/agent_loop.py:18]
- `agent_loop.py -> aihub.attention_controller(rank_messages)` [aihub/agent_loop.py:19]
- `agent_loop.py -> aihub.cognitive_controller(...)` [aihub/agent_loop.py:20]
- `agent_loop.py -> aihub.conflict_detector(check_conflict)` [aihub/agent_loop.py:21]

- `cognitive_controller.py -> aihub.attention_controller(AttentionController)` [aihub/cognitive_controller.py:20]
- `cognitive_controller.py -> aihub.conflict_detector(...)` [aihub/cognitive_controller.py:21]
- `cognitive_controller.py -> aihub.meta_memory(check_stale)` [aihub/cognitive_controller.py:24]
- `cognitive_controller.py -> aihub.prediction_engine(predict_next_action)` [aihub/cognitive_controller.py:26]

- `memory_engine.py -> aihub.db(...)` [aihub/memory_engine.py:10]
- `memory_engine.py -> aihub.vector_hook(remember_turn)` [aihub/memory_engine.py:21]
- `memory_engine.py -> aihub.vector_index(...)` [aihub/memory_engine.py:22]
- `memory_engine.py -> aihub.knowledge_graph(add_node/add_edge/persist/query)` [aihub/memory_engine.py:116-118,133,387]
- `memory_engine.py -> aihub.meta_memory(touch_nodes)` [aihub/memory_engine.py:404]

- `agent_engine.py -> .agent_db(...)` [aihub/agent_engine.py:9]
- `agent_engine.py -> .memory_engine(add_episode,add_fact)` [aihub/agent_engine.py:18]
- `agent_engine.py -> .psyche_engine(ensure_user,evolve)` [aihub/agent_engine.py:19]
- `agent_engine.py -> aihub.research_engine(research as do_research)` [aihub/agent_engine.py:344]
- `agent_engine.py -> aihub.memory_gc(collect_garbage)` [aihub/agent_engine.py:379]

- `research_engine.py -> aihub.memory_engine(add_fact)` [aihub/research_engine.py:16]
- `research_engine.py -> aihub.psyche_engine(ensure_user)` [aihub/research_engine.py:17]

- `learning_engine.py -> aihub.memory_engine(add_fact)` [aihub/learning_engine.py:10]
- `learning_engine.py -> aihub.psyche_engine(ensure_user)` [aihub/learning_engine.py:11]

- `meta_memory.py -> aihub.db(...)` [aihub/meta_memory.py:7]
- `memory_gc.py -> aihub.meta_memory(check_stale)` [aihub/memory_gc.py:20]
- `memory_gc.py -> aihub.knowledge_evolution(KnowledgeEvolution)` [aihub/memory_gc.py:19]

- `agent_executor.py -> aihub.memory_engine(retrieve_context)` [aihub/agent_executor.py:30]
- `agent_executor.py -> aihub.memory_engine(add_fact)` [aihub/agent_executor.py:43]
- `agent_executor.py -> aihub.research_engine(research)` [aihub/agent_executor.py:53]
- `agent_executor.py -> aihub.web_tools(fetch_url)` [aihub/agent_executor.py:64]
- `agent_executor.py -> aihub.fs_tools(write_file)` [aihub/agent_executor.py:70]
- `agent_executor.py -> aihub.system_ops(create_snapshot)` [aihub/agent_executor.py:78]

---

## APPENDIX F — FUNCTION INVENTORY (core MRI set)

> Poniżej skondensowany indeks funkcji (region mózgu → funkcja).

### F.1 Decision region

- `cognitive_controller.CognitiveController.decide` [aihub/cognitive_controller.py:148]
- `cognitive_controller._extract_intent` [aihub/cognitive_controller.py:279]
- `cognitive_controller._decide_query` [aihub/cognitive_controller.py:298]
- `cognitive_controller._decide_learn` [aihub/cognitive_controller.py:340]
- `cognitive_controller._decide_research` [aihub/cognitive_controller.py:378]
- `cognitive_controller._decide_action` [aihub/cognitive_controller.py:416]
- `conflict_detector.ConflictDetector.check_conflict` [aihub/conflict_detector.py:62]

### F.2 Action region

- `agent_executor.execute` [aihub/agent_executor.py:12]
- `agent_executor._exec_query` [aihub/agent_executor.py:29]
- `agent_executor._exec_learn` [aihub/agent_executor.py:42]
- `agent_executor._exec_research` [aihub/agent_executor.py:52]
- `agent_executor._exec_action` [aihub/agent_executor.py:59]
- `agent_engine.execute_task` [aihub/agent_engine.py:193]
- `agent_engine._execute_web_fetch` [aihub/agent_engine.py:234]
- `agent_engine._execute_fs_write` [aihub/agent_engine.py:265]
- `agent_engine._execute_snapshot` [aihub/agent_engine.py:294]
- `agent_engine._execute_research` [aihub/agent_engine.py:319]

### F.3 Memory region

- `memory_engine.add_stm` [aihub/memory_engine.py:43]
- `memory_engine.add_episode` [aihub/memory_engine.py:165]
- `memory_engine.add_fact` [aihub/memory_engine.py:182]
- `memory_engine.process_turn` [aihub/memory_engine.py:234]
- `memory_engine.retrieve_context` [aihub/memory_engine.py:324]
- `db.search_nodes_fts` [aihub/db.py:372]
- `meta_memory.touch_nodes` [aihub/meta_memory.py:361]
- `memory_gc.collect_garbage` [aihub/memory_gc.py:44]

### F.4 Knowledge region

- `learning_engine.extract_facts_from_message` [aihub/learning_engine.py:132]
- `learning_engine.process_turn` [aihub/learning_engine.py:170]
- `learning_engine.learn_from_reflection` [aihub/learning_engine.py:280]
- `research_engine.research` [aihub/research_engine.py:226]
- `knowledge_graph.add_node` [aihub/knowledge_graph.py:61]
- `knowledge_graph.query_nodes` [aihub/knowledge_graph.py:328]
- `knowledge_graph.persist_node` [aihub/knowledge_graph.py:257]
- `knowledge_evolution.deduplicate` [aihub/knowledge_evolution.py:102]

### F.5 Sensory/Input region

- `main.add_turn` [aihub/main.py:133]
- `main.memory_add` [aihub/main.py:193]
- `main.web_fetch` [aihub/main.py:318]
- `agent_engine._pull_new_stm` [aihub/agent_engine.py:28]
- `agent_loop.get_pending_messages` [aihub/agent_loop.py:50]

### F.6 Psyche/Reflection region

- `psyche_engine.ensure_user` [aihub/psyche_engine.py:77]
- `psyche_engine.analyze_sentiment` [aihub/psyche_engine.py:96]
- `psyche_engine.evolve` [aihub/psyche_engine.py:115]
- `psyche_engine.reflect` [aihub/psyche_engine.py:181]

---

## APPENDIX G — EVIDENCE LEDGER (raw, condensed)

Format: `ID | file:line | obserwacja`

E001 | aihub/main.py:86 | startup hook aktywny
E002 | aihub/main.py:93 | worker startowany automatycznie
E003 | aihub/main.py:193 | `/memory/add` to główna ścieżka zapisu wiedzy
E004 | aihub/main.py:377 | `/cognitive/decide` wywołuje controller.decide
E005 | aihub/main.py:214 | `/memory/search` używa retrieve_context
E006 | aihub/main.py:217 | response model ogranicza wyjście
E007 | aihub/models.py:40 | `MemorySearchOut` nie ma dense/graph fields
E008 | aihub/models.py:44 | only episodic
E009 | aihub/models.py:45 | only semantic
E010 | aihub/models.py:47 | total

E011 | aihub/cognitive_controller.py:148 | główny generator decyzji
E012 | aihub/cognitive_controller.py:178 | build decision_context
E013 | aihub/cognitive_controller.py:193 | merge external context
E014 | aihub/cognitive_controller.py:196 | predictions generated
E015 | aihub/cognitive_controller.py:205 | query branch
E016 | aihub/cognitive_controller.py:209 | learn branch
E017 | aihub/cognitive_controller.py:213 | research branch
E018 | aihub/cognitive_controller.py:217 | action branch
E019 | aihub/cognitive_controller.py:279 | intent = keyword rules
E020 | aihub/cognitive_controller.py:331 | emits `memory_search`
E021 | aihub/cognitive_controller.py:447 | emits `execute`
E022 | aihub/cognitive_controller.py:459 | memory pressure from check_stale

E023 | aihub/agent_executor.py:15 | dispatch map starts
E024 | aihub/agent_executor.py:16 | supports `query`
E025 | aihub/agent_executor.py:17 | supports `learn`
E026 | aihub/agent_executor.py:18 | supports `research`
E027 | aihub/agent_executor.py:19 | supports `action`
E028 | aihub/agent_executor.py:23 | unknown action warning
E029 | aihub/agent_executor.py:24 | unknown action error return
E030 | aihub/agent_executor.py:29 | query executor uses retrieve_context
E031 | aihub/agent_executor.py:42 | learn executor uses add_fact
E032 | aihub/agent_executor.py:52 | research executor uses research()

E033 | aihub/agent_loop.py:18 | imports AgentExecutor
E034 | aihub/agent_loop.py:30 | singleton executor created
E035 | aihub/agent_loop.py:66 | process_decision exists
E036 | aihub/agent_loop.py:130 | \_execute_action uses executor
E037 | aihub/agent_loop.py:135 | await \_executor.execute(...)
E038 | aihub/agent_loop.py:144 | agent_cycle loop
E039 | aihub/agent_loop.py:161 | attention ranking call
E040 | aihub/agent_loop.py:166 | processes top-3 messages
E041 | aihub/agent_loop.py:192 | await cognitive decide
E042 | aihub/agent_loop.py:197 | then process_decision

E043 | aihub/agent_api.py:88 | `/agent/loop` route is sync def
E044 | aihub/agent_api.py:93 | returns async run_loop coroutine
E045 | aihub/agent_api.py:68 | `/agent/tick` is async and direct

E046 | aihub/agent_worker.py:30 | background while True loop
E047 | aihub/agent_worker.py:78 | asyncio.run(agent_tick(...))
E048 | aihub/agent_worker.py:79 | tick max_stm=200 max_tasks=6
E049 | aihub/agent_worker.py:137 | periodic sleep

E050 | aihub/agent_engine.py:103 | plan_from_text (rule planner)
E051 | aihub/agent_engine.py:389 | agent_tick core runtime loop
E052 | aihub/agent_engine.py:431 | ATTENTION_THRESHOLD=20
E053 | aihub/agent_engine.py:437 | top-20 message filter
E054 | aihub/agent_engine.py:455 | per-message processing
E055 | aihub/agent_engine.py:463 | user-only fact extraction
E056 | aihub/agent_engine.py:475 | enqueue_task
E057 | aihub/agent_engine.py:508 | claim_next_task
E058 | aihub/agent_engine.py:513 | complete_task(user_id, t["id"])
E059 | aihub/agent_engine.py:319 | research task execution
E060 | aihub/agent_engine.py:365 | \_maybe_gc hook

E061 | aihub/agent_db.py:141 | complete_task(task_id, ok, error)
E062 | aihub/agent_db.py:111 | claim_next_task returns dict with id

E063 | aihub/memory_engine.py:234 | process_turn starts
E064 | aihub/memory_engine.py:237 | remember_turn vector hook
E065 | aihub/memory_engine.py:238 | add_stm user
E066 | aihub/memory_engine.py:239 | add_stm assistant
E067 | aihub/memory_engine.py:244 | add_episode
E068 | aihub/memory_engine.py:251 | psyche modulation active
E069 | aihub/memory_engine.py:256 | learning extraction used
E070 | aihub/memory_engine.py:277 | keyword fallback
E071 | aihub/memory_engine.py:324 | retrieve_context core
E072 | aihub/memory_engine.py:328 | L1 FTS query
E073 | aihub/memory_engine.py:330 | L2 FTS query
E074 | aihub/memory_engine.py:333 | TF-IDF rerank
E075 | aihub/memory_engine.py:347 | blend score uses importance/confidence
E076 | aihub/memory_engine.py:369 | dense vector search
E077 | aihub/memory_engine.py:385 | graph_hits list
E078 | aihub/memory_engine.py:389 | kg query
E079 | aihub/memory_engine.py:404 | meta touch_nodes
E080 | aihub/memory_engine.py:428 | dense_hits in output
E081 | aihub/memory_engine.py:429 | graph_hits in output

E082 | aihub/memory_engine.py:116 | KG add_node imported
E083 | aihub/memory_engine.py:117 | KG add_edge imported
E084 | aihub/memory_engine.py:133 | persist_node imported
E085 | aihub/memory_engine.py:135 | persist_node called

E086 | aihub/meta_memory.py:361 | touch_nodes updates usage
E087 | aihub/meta_memory.py:140 | stale detection
E088 | aihub/meta_memory.py:219 | rank_facts exists
E089 | aihub/meta_memory.py:178 | overall_priority computed
E090 | aihub/meta_memory.py:307 | generate_report exists

E091 | aihub/memory_gc.py:44 | GC pipeline entry
E092 | aihub/memory_gc.py:63 | delete stale phase
E093 | aihub/memory_gc.py:73 | archive phase
E094 | aihub/memory_gc.py:79 | pressure relief
E095 | aihub/memory_gc.py:88 | evolve phase
E096 | aihub/memory_gc.py:98 | storage optimize

E097 | aihub/research_engine.py:226 | research() async entry
E098 | aihub/research_engine.py:245 | query cache read
E099 | aihub/research_engine.py:265 | search backends called
E100 | aihub/research_engine.py:290 | add_fact write
E101 | aihub/research_engine.py:343 | query cache write
E102 | aihub/research_engine.py:374 | backend multiplexer
E103 | aihub/research_engine.py:398 | Brave backend
E104 | aihub/research_engine.py:433 | Wikipedia backend
E105 | aihub/research_engine.py:488 | DuckDuckGo backend
E106 | aihub/research_engine.py:560 | asyncio.run in detailed research

E107 | aihub/learning_engine.py:132 | regex fact extraction
E108 | aihub/learning_engine.py:170 | turn-level learning
E109 | aihub/learning_engine.py:201 | user facts add_fact
E110 | aihub/learning_engine.py:228 | assistant facts add_fact
E111 | aihub/learning_engine.py:280 | reflection learning

E112 | aihub/psyche_engine.py:96 | lexical sentiment analysis
E113 | aihub/psyche_engine.py:115 | evolve state
E114 | aihub/psyche_engine.py:181 | reflect text synthesis
E115 | aihub/psyche_engine.py:220 | reflect -> learn_from_reflection

E116 | aihub/prediction_engine.py:40 | predict_next_action function
E117 | aihub/prediction_engine.py:113 | cache write predictions
E118 | aihub/prediction_engine.py:129 | event prediction.generated
E119 | aihub/prediction_engine.py:144 | predict_context_needs
E120 | aihub/prediction_engine.py:178 | predict_conflicts

E121 | aihub/attention_controller.py:56 | rank_messages
E122 | aihub/attention_controller.py:115 | urgency keywords
E123 | aihub/attention_controller.py:133 | relevance patterns
E124 | aihub/attention_controller.py:166 | focus_on placeholder

E125 | aihub/knowledge_graph.py:61 | add_node runtime
E126 | aihub/knowledge_graph.py:69 | add_edge runtime
E127 | aihub/knowledge_graph.py:204 | stats
E128 | aihub/knowledge_graph.py:257 | persist_node DB bridge
E129 | aihub/knowledge_graph.py:272 | persist_edge DB bridge
E130 | aihub/knowledge_graph.py:289 | load_from_db helper
E131 | aihub/knowledge_graph.py:328 | query_nodes search

E132 | aihub/db.py:27 | init_db creates core tables
E133 | aihub/db.py:139 | creates knowledge_nodes table
E134 | aihub/db.py:148 | creates knowledge_edges table
E135 | aihub/db.py:372 | FTS search with fallback

E136 | aihub/vector_engine.py:130 | add_memory(text,user_id)
E137 | aihub/vector_engine.py:174 | search(query,k,user_id)
E138 | aihub/vector_engine.py:200 | user isolation filter exists
E139 | aihub/vector_hook.py:6 | add_memory(user_msg) without user_id
E140 | aihub/vector_hook.py:9 | add_memory(assistant_msg) without user_id
E141 | aihub/agent_runner.py:28 | add_memory("agent run ...") no user_id
E142 | aihub/agent_runner.py:32 | search(text) no user_id

E143 | aihub/conflict_detector.py:49 | forbidden_actions list
E144 | aihub/conflict_detector.py:62 | check_conflict entry
E145 | aihub/conflict_detector.py:130 | security violation check
E146 | aihub/conflict_detector.py:208 | resource constraints check

E147 | aihub/metrics_engine.py:83 | record_latency
E148 | aihub/metrics_engine.py:98 | record_error
E149 | aihub/metrics_engine.py:157 | get_system_health
E150 | aihub/metrics_engine.py:184 | get_alert_status

E151 | aihub/main.py:426 | cognitive_health endpoint
E152 | aihub/main.py:492 | returns graph_stats only there

---

## APPENDIX H — REACTIVE RULE INDEX (explicit)

R001 | intent research keyword set [aihub/cognitive_controller.py:283]
R002 | intent action keyword set [aihub/cognitive_controller.py:287]
R003 | intent learn keyword set [aihub/cognitive_controller.py:291]
R004 | default intent query [aihub/cognitive_controller.py:295]

R005 | attention urgent keywords [aihub/attention_controller.py:45]
R006 | attention important keywords [aihub/attention_controller.py:46]
R007 | attention routine keywords [aihub/attention_controller.py:47]
R008 | relevance self_reference [aihub/attention_controller.py:51]
R009 | relevance query words [aihub/attention_controller.py:52]
R010 | relevance action words [aihub/attention_controller.py:53]

R011 | agent plan: web trigger if `sprawdź|ściągnij|fetch` [aihub/agent_engine.py:114]
R012 | agent plan: fs write format `zapisz: ... :: ...` [aihub/agent_engine.py:123]
R013 | agent plan: snapshot keywords [aihub/agent_engine.py:145]
R014 | agent plan: research keywords [aihub/agent_engine.py:155]

R015 | psyche positive lexicon [aihub/psyche_engine.py:12]
R016 | psyche negative lexicon [aihub/psyche_engine.py:32]
R017 | psyche intensifiers [aihub/psyche_engine.py:50]
R018 | sentiment score denominator max(3, pos+neg) [aihub/psyche_engine.py:104]

R019 | learning regex identity rule [aihub/learning_engine.py:30]
R020 | learning regex preference rule [aihub/learning_engine.py:41]
R021 | learning regex work rule [aihub/learning_engine.py:52]
R022 | learning regex goal rule [aihub/learning_engine.py:63]
R023 | learning regex technical rule [aihub/learning_engine.py:74]
R024 | learning regex constraint rule [aihub/learning_engine.py:85]

R025 | research fact extraction regex types: definition [aihub/research_engine.py:157]
R026 | research fact extraction regex types: statistics [aihub/research_engine.py:161]
R027 | research fact extraction regex types: date [aihub/research_engine.py:165]
R028 | research fact extraction regex types: claim [aihub/research_engine.py:169]

---

## APPENDIX I — COGNITIVE MRI VERDICT (one-page)

1. **Gdzie system naprawdę myśli:**
    - `cognitive_controller.decide` (formalnie),
    - `agent_engine.agent_tick + plan_from_text` (operacyjnie).
2. **Gdzie system naprawdę tworzy wiedzę:**
    - memory, learning, research.
3. **Gdzie system tylko reaguje:**
    - większość parserów intencji/sentymentu/uwagi.
4. **Gdzie system symuluje inteligencję:**
    - predykcje bez silnego domknięcia,
    - ranking meta bez wpływu na final blend,
    - rozjazdy kontraktów action-type.
5. **Największa luka:**
    - niespójność kontraktu między decision engine i executor.

---

## APPENDIX J — EXTENDED CALLGRAPH CHAINS (deep traces)

### J.1 Chain: `agent_loop` decision execution

J1-01 `agent_api.agent_loop` [aihub/agent_api.py:88]
J1-02 returns `run_loop` coroutine [aihub/agent_api.py:93]
J1-03 `agent_loop.run_loop` [aihub/agent_loop.py:247]
J1-04 calls `agent_cycle` [aihub/agent_loop.py:263]
J1-05 `agent_cycle` fetches messages [aihub/agent_loop.py:156]
J1-06 `rank_messages` [aihub/agent_loop.py:161]
J1-07 DecisionRequest built [aihub/agent_loop.py:170]
J1-08 `cognitive_controller.decide` [aihub/agent_loop.py:192]
J1-09 `process_decision` [aihub/agent_loop.py:197]
J1-10 `check_conflict` [aihub/agent_loop.py:76]
J1-11 `_execute_action` [aihub/agent_loop.py:100,130]
J1-12 `_executor.execute` [aihub/agent_loop.py:135]
J1-13 dispatch map [aihub/agent_executor.py:15-19]
J1-14 unknown guard [aihub/agent_executor.py:23-24]
J1-15 success path logs `cycle.decision` [aihub/agent_loop.py:204]

### J.2 Chain: `agent_tick` task execution

J2-01 `main.startup` starts worker [aihub/main.py:93]
J2-02 worker loop [aihub/agent_worker.py:30]
J2-03 periodic `asyncio.run(agent_tick)` [aihub/agent_worker.py:78]
J2-04 `agent_tick` begin [aihub/agent_engine.py:389]
J2-05 get state [aihub/agent_engine.py:404]
J2-06 pull stm [aihub/agent_engine.py:416]
J2-07 attention prune [aihub/agent_engine.py:431-437]
J2-08 evolve psyche [aihub/agent_engine.py:455]
J2-09 extract facts [aihub/agent_engine.py:463]
J2-10 plan tasks [aihub/agent_engine.py:474]
J2-11 enqueue task [aihub/agent_engine.py:475]
J2-12 add episode [aihub/agent_engine.py:489]
J2-13 update cursor [aihub/agent_engine.py:501]
J2-14 claim next task [aihub/agent_engine.py:508]
J2-15 execute_task [aihub/agent_engine.py:511]
J2-16 complete_task call [aihub/agent_engine.py:513]
J2-17 append event [aihub/agent_engine.py:521]
J2-18 maybe GC [aihub/agent_engine.py:527]

### J.3 Chain: memory retrieval

J3-01 `retrieve_context` start [aihub/memory_engine.py:324]
J3-02 stm fetch [aihub/memory_engine.py:325]
J3-03 l1 FTS [aihub/memory_engine.py:328]
J3-04 l2 FTS [aihub/memory_engine.py:330]
J3-05 TF-IDF scores [aihub/memory_engine.py:333-334]
J3-06 blend ranking [aihub/memory_engine.py:347-352]
J3-07 dense hits [aihub/memory_engine.py:369]
J3-08 graph query [aihub/memory_engine.py:389]
J3-09 touch nodes [aihub/memory_engine.py:408]
J3-10 return context [aihub/memory_engine.py:423-430]

### J.4 Chain: research ingestion

J4-01 `research` entry [aihub/research_engine.py:226]
J4-02 query cache check [aihub/research_engine.py:245]
J4-03 gather search results [aihub/research_engine.py:265]
J4-04 backend multiplexer [aihub/research_engine.py:374]
J4-05 brave fetch [aihub/research_engine.py:398]
J4-06 wikipedia fetch [aihub/research_engine.py:433]
J4-07 duckduckgo fetch [aihub/research_engine.py:488]
J4-08 extract facts [aihub/research_engine.py:174]
J4-09 quality filter [aihub/research_engine.py:79]
J4-10 add_fact write [aihub/research_engine.py:290]
J4-11 cache write [aihub/research_engine.py:343]
J4-12 return metrics [aihub/research_engine.py:351]

---

## APPENDIX K — LINEAR CHECKLIST AGAINST 14 REQUIREMENTS

K01 ✅ Brain regions map — sekcja 1
K02 ✅ Thought generation point — sekcja 2
K03 ✅ Context construction pipeline — sekcja 3
K04 ✅ Knowledge creation — sekcja 4
K05 ✅ Memory consolidation — sekcja 5
K06 ✅ Intelligence pathways — sekcja 6
K07 ✅ Reactive vs cognitive — sekcja 7
K08 ✅ Cognitive bottleneck — sekcja 8
K09 ✅ Knowledge graph influence — sekcja 9
K10 ✅ Meta memory effect — sekcja 10
K11 ✅ Intelligence core — sekcja 11
K12 ✅ Cognitive depth score — sekcja 12
K13 ✅ System brain diagram — sekcja 13
K14 ✅ MRI summary + brutal truth — sekcje 14 + BRUTAL TRUTH

K15 ✅ AST/callgraph evidence — Appendix F/J
K16 ✅ Import graph evidence — Appendix E
K17 ✅ Runtime flow evidence — Appendix C/J
K18 ✅ Agent loop analysis — sekcje 2,7,8 + Appendix C/J

---

## FINAL ONE-LINER

AI-Hub ma realne moduły pamięci, research i wykonania akcji, ale jego „warstwa poznawcza” traci moc przez niespójne kontrakty decyzji i fragmentację dwóch równoległych mózgów (cognitive loop vs tick loop).
