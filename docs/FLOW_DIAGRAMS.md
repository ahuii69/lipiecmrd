# AI-Hub — Flow Diagrams v2 (Mermaid) — NO FANFIC

> **Regen v2** — Każdy element ma dowód: plik + linie kodu.
> Elementy bez dowodu zostały USUNIĘTE lub oznaczone [BRAK CALLERÓW].
> Companion: `docs/FLOW_DIAGRAMS_EVIDENCE.md` z surowymi cytatami.
> Źródło: audit kodu `aihub/`, 2025-01

---

## CO BYŁO BŁĘDNE W POPRZEDNIEJ WERSJI

1. **FAISS / SentenceTransformer w diagramie /memory/search** — W v1 diagram sugerował że vector_engine (FAISS) jest używany w pipeline wyszukiwania. **FAŁSZ.** `retrieve_context()` w `memory_engine.py:193-250` używa TYLKO `vector_index.py` (TF-IDF in-process), NIGDY `vector_engine.py`. FAISS jest używany wyłącznie przy ZAPISIE (`process_turn → vector_hook → vector_engine.add_memory`).
2. **learning_engine oznaczony jako "MARTWY KOD"** — Poprzedni diagram mówił że nikt nie woła learning_engine. **PRAWDA ALE NIEKOMPLETNA.** `cognitive_controller.py:27` importuje i instancjuje `LearningEngine()`, ale **nigdy nie woła żadnej jej metody** (`.process_turn()` ani `.learn_from_reflection()`). Również `agent_api.py:84` importuje `agent_loop.run_loop` który NIE woła learning_engine.
3. **research_engine — brak informacji o placeholder** — W v1 nie zaznaczono że `_generate_placeholder_results()` zwraca **pustą listę** (linia 179-183). Research engine istnieje w kodzie ale zawsze zwraca 0 wyników.
4. **agent_worker / agent_tick pipeline POMINIĘTY** — v1 pokazywał tylko `agent_loop.agent_cycle` ale pominął główną pętlę runtime: `start_worker_once() → _run_loop() → agent_tick()` z `agent_engine.py`. To jest PRAWDZIWY aktywny agent, nie `agent_loop`.
5. **Memory GC "schedule_gc to no-op"** — To było poprawne, ale brakowało informacji KTO woła `collect_garbage()`. Odpowiedź: **NIKT w runtime**. Jedynym callerem jest potencjalny ręczny call.
6. **Diagram 8 (architektura) pokazywał strzałki "U → L1" i "A → L1"** — Sugerował że user/assistant msg idą bezpośrednio do L1. **FAŁSZ.** Idą przez `process_turn → add_episode(summary)` gdzie summary = `"U:{user_msg} || A:{assistant_msg}"`.

---

## 1. POST /memory/add — Pipeline zapisu

### DOWODY:

- Endpoint: `aihub/main.py:196-210`
- ensure_user: `aihub/psyche_engine.py:71-82`
- evolve: `aihub/psyche_engine.py:99-154`
- process_turn: `aihub/memory_engine.py:127-166`
- remember_turn: `aihub/vector_hook.py:3-10`
- add_memory (FAISS): `aihub/vector_engine.py:133-164`
- add_stm: `aihub/memory_engine.py:44-55`
- add_episode: `aihub/memory_engine.py:75-90`
- add_fact (warunkowy): `aihub/memory_engine.py:93-110`
- \_enforce_caps: `aihub/memory_engine.py:113-125`

```mermaid
sequenceDiagram
    participant Client
    participant main as main.py:196
    participant psyche as psyche_engine
    participant mem as memory_engine
    participant vh as vector_hook
    participant ve as vector_engine
    participant db as SQLite

    Client->>main: POST /memory/add {user_id, user_msg, assistant_msg, intent}
    main->>psyche: ensure_user(user_id) [L82]
    main->>psyche: evolve(user_id, user_msg, "user") [L99]
    psyche->>psyche: analyze_sentiment → s, conf [L86]
    psyche->>psyche: mood += 1.0*0.18*s*conf [L126]
    psyche->>psyche: trait learning harsh/friendly [L133-145]
    psyche->>psyche: temperature = 0.55+0.25*(mood-0.5) [L148]
    psyche->>db: upsert_psyche() [L150]
    psyche->>db: append_event("psyche.update") [L157]

    main->>psyche: evolve(user_id, assistant_msg, "assistant") [L99, role_w=0.35]

    main->>mem: process_turn(user_id, user_msg, assistant_msg, intent, meta) [L127]

    Note over mem,ve: 1. Vector hook (fire-and-forget zapis)
    mem->>vh: remember_turn(user_msg, assistant_msg) [L128]
    vh->>ve: add_memory(user_msg) [L5-6]
    ve->>ve: SentenceTransformer.encode() [L147]
    ve->>ve: FAISS index.add() [L150]
    ve->>ve: _save() → data/vector.index [L152]
    vh->>ve: add_memory(assistant_msg) [L8-9]

    Note over mem,db: 2. STM x2
    mem->>db: add_stm("user", user_msg) → insert_stm_message [L44]
    mem->>db: prune_stm(keep=STM_MAX_MESSAGES) [L46]
    mem->>db: add_stm("assistant", assistant_msg) [L44]

    Note over mem,db: 3. Episodic L1
    mem->>mem: summary = "U:{msg} || A:{msg}" [L140]
    mem->>mem: _id_for(summary, user_id, "L1") → SHA256 [L33]
    mem->>db: upsert_node(layer="L1") + FTS update [L142]
    mem->>mem: _enforce_caps(user_id) [L113-125]

    Note over mem,db: 4. Auto-fact (warunkowy)
    alt user_msg zawiera: lubię|nie lubię|preferuję|zawsze|nigdy|ważne|zakaz|nakaz
        mem->>db: add_fact(layer="L2", tags=[user,preference,intent]) [L155-164]
    end

    mem-->>main: {stm_ids, episode_id, fact_ids, ts}
    main-->>Client: 200 OK
```

---

## 2. POST /memory/search — Pipeline odczytu

### DOWODY:

- Endpoint: `aihub/main.py:214-250`
- retrieve_context: `aihub/memory_engine.py:193-260`
- get_stm: `aihub/db.py:274-292`
- search_nodes_fts: `aihub/db.py:353-396` (FTS5 MATCH → fallback LIKE)
- \_vector_rerank (TF-IDF): `aihub/memory_engine.py:172-190`
- vector_index funkcje: `aihub/vector_index.py:13-95`
- FAISS/vector_engine: **NIE UŻYWANY** w search pipeline

```mermaid
sequenceDiagram
    participant Client
    participant main as main.py:214
    participant mem as memory_engine
    participant db as SQLite
    participant vi as vector_index.py<br>(TF-IDF, NIE FAISS)

    Client->>main: POST /memory/search {user_id, query, limit}
    main->>mem: retrieve_context(user_id, query, limit) [L193]

    Note over mem,db: 1. STM
    mem->>db: get_stm(user_id, min(20, STM_MAX)) [L194]

    Note over mem,db: 2. FTS5 kandydaci L1
    mem->>db: search_nodes_fts("L1", query, limit*20) [L197]
    Note over db: FTS5 MATCH "content:{query}"<br>fallback: LIKE '%query%'

    Note over mem,db: 3. FTS5 kandydaci L2
    mem->>db: search_nodes_fts("L2", query, limit*40) [L199]

    Note over mem,vi: 4. TF-IDF reranking (vector_index.py)
    mem->>vi: _vector_rerank(query, l1, topk=limit) [L172]
    vi->>vi: tokenize() → regex [a-zA-Z0-9_]+ [L13]
    vi->>vi: build_df() → document frequency [L24]
    vi->>vi: prune_vocab(min_df, max_df, max_vocab) [L34]
    vi->>vi: tfidf_vector() → sublinear TF * IDF, L2 norm [L51]
    vi->>vi: topk_cosine() → sparse cosine similarity [L78]
    vi-->>mem: ranked L1 scores

    mem->>vi: _vector_rerank(query, l2, topk=limit)
    vi-->>mem: ranked L2 scores

    Note over mem: 5. Blend score
    mem->>mem: score = 0.72*cosine + 0.18*importance + 0.10*confidence [L218]
    mem->>mem: sort desc, trim to limit [L225]
    mem->>db: append_event("memory.retrieve") [L253]

    mem-->>main: {stm, episodic, semantic, total}
    main-->>Client: 200 OK
```

---

## 3. POST /psyche/update — Ewolucja psychiki

### DOWODY:

- Endpoint: `aihub/main.py:160-168`
- evolve: `aihub/psyche_engine.py:99-154`
- analyze_sentiment: `aihub/psyche_engine.py:86-97`
- \_POS/\_NEG/\_INTENSIFIERS: `aihub/psyche_engine.py:9-36`
- \_baseline: `aihub/psyche_engine.py:43-58`
- upsert_psyche: `aihub/db.py:181-199`

```mermaid
flowchart TD
    A["POST /psyche/update<br>main.py:160"] --> B["ensure_user(user_id)<br>psyche_engine.py:71"]
    B --> C["analyze_sentiment(text)<br>psyche_engine.py:86"]

    C --> D{Zlicz słowa w tekście}
    D --> D1["_POS: 18 słów PL<br>dobrze,super,kocham,git,dzięki...<br>psyche_engine.py:9-22"]
    D --> D2["_NEG: 16 słów PL<br>źle,problem,kurwa,chuj...<br>psyche_engine.py:23-32"]
    D --> D3["_INTENSIFIERS: 6 słów<br>bardzo,mega,strasznie...<br>psyche_engine.py:33-36"]

    D1 --> E["sentiment = (pos-neg) / max(3, pos+neg)<br>clamp -1..1"]
    D2 --> E
    D3 --> F["confidence = 0.45 + 0.12*(pos+neg) + 0.05*intens<br>clamp 0..0.95"]
    E --> G["evolve()<br>psyche_engine.py:99"]
    F --> G

    G --> H{role?}
    H -->|user| H1[role_w = 1.0]
    H -->|assistant| H2[role_w = 0.35]

    H1 --> I["mood += role_w * 0.18 * s * conf<br>drift → 0.55<br>psyche_engine.py:126"]
    H2 --> I
    I --> J["energy += role_w*0.06*s*conf - 0.01*(words/80)<br>psyche_engine.py:128"]
    J --> K["focus += role_w*0.05*conf - 0.02*(words/200)<br>psyche_engine.py:129"]

    K --> L{Trait learning}
    L -->|"harsh: neg>pos AND neg>=2"| L1["directness +0.03*conf<br>patience -0.03*conf<br>swearing +0.02*conf<br>sarcasm +0.02*conf<br>style='ziomek'<br>psyche_engine.py:133-140"]
    L -->|"friendly: pos>neg AND pos>=2"| L2["agreeableness +0.02*conf<br>patience +0.02*conf<br>sarcasm -0.01*conf<br>psyche_engine.py:141-145"]

    L1 --> M["temperature = 0.55 + 0.25*(mood-0.5)<br>clamp 0.25..0.95<br>psyche_engine.py:148"]
    L2 --> M

    M --> N["upsert_psyche → DB<br>psyche_engine.py:150"]
    N --> O["append_event('psyche.update')<br>psyche_engine.py:157"]
```

---

## 4. POST /psyche/reflect — Refleksja

### DOWODY:

- Endpoint: `aihub/main.py:172-190`
- retrieve_context: `aihub/memory_engine.py:193`
- reflect: `aihub/psyche_engine.py:157-193`

```mermaid
sequenceDiagram
    participant Client
    participant main as main.py:172
    participant psyche as psyche_engine
    participant mem as memory_engine
    participant db as SQLite

    Client->>main: POST /psyche/reflect {user_id, query, limit}
    main->>psyche: ensure_user(user_id) [L71]
    main->>mem: retrieve_context(user_id, query, limit=min(limit,20)) [L193]
    mem-->>main: ctx {stm, episodic, semantic}
    main->>psyche: reflect(user_id, ctx["stm"]) [L157]

    Note over psyche: Częstotliwość słów z ostatnich 20 wiadomości
    psyche->>psyche: freq count words len>=4, top 12 where count>=2 [L165-170]
    psyche->>psyche: mood_desc: spoko/wkurwiony/neutralny [L173-177]
    psyche->>psyche: energy_desc: wysoka/niska/średnia [L178-180]
    psyche->>db: append_event("psyche.reflect") [L191]
    psyche-->>main: {reflection, topics, state, ts}
    main-->>Client: 200 OK
```

---

## 5. POST /cognitive/decide — System decyzyjny

### DOWODY:

- Endpoint: `aihub/main.py:365-410`
- CognitiveController: `aihub/cognitive_controller.py:71-83`
- decide(): `aihub/cognitive_controller.py:131-232`
- \_extract_intent: `aihub/cognitive_controller.py:234-250`
- predict_next_action: `aihub/prediction_engine.py:47-131`
- \_estimate_memory_pressure → meta_memory.check_stale: `aihub/cognitive_controller.py:409-420`
- ConflictDetector.check_conflict: `aihub/conflict_detector.py:70-118`
- \_check_resources (TTL 300s reset): `aihub/cognitive_controller.py:99-118`
- LearningEngine: importowany `cognitive_controller.py:27`, instancjowany w `__init__` L82, ale **NIGDY wołany**

```mermaid
flowchart TD
    A["POST /cognitive/decide<br>main.py:365"] --> B["ensure_user<br>cognitive_controller.py:143"]
    B --> C["Build decision_context<br>cognitive_controller.py:150-165"]

    C --> C1["urgency = psyche.energy"]
    C --> C2["memory_pressure = meta_memory.check_stale(30d) / 500<br>cognitive_controller.py:409-420"]
    C --> C3["_extract_intent(message)<br>cognitive_controller.py:234-250"]
    C --> C4["predict_next_action(user_id, ctx)<br>prediction_engine.py:47-131"]

    C3 --> D{Intent? L236-L249}
    D -->|"sprawdź,wyszukaj,research,find"| E[_decide_research L321]
    D -->|"stwórz,napisz,execute,make"| F[_decide_action L376]
    D -->|"nauczę,learn,teach,explain"| G[_decide_learn L279]
    D -->|default: query| H[_decide_query L254]

    E --> E1["_check_resources('web_request')<br>limit=3, TTL=300s<br>cognitive_controller.py:99-118"]
    E1 -->|LIMIT| SKIP[DecisionResult skip]
    E1 -->|OK| E2["research_type=deep if urgency>0.7<br>confidence=0.7+urgency*0.2"]

    F --> F1["_check_resources('web_request')"]
    F1 -->|OK| F2["confidence=0.55+urgency*0.2+focus*0.15"]

    G --> G1["_check_resources('learning_sample')<br>limit=10"]
    G1 -->|OK| G2["confidence=0.65+energy*0.1+focus*0.15"]

    H --> H1["_check_resources('memory_operation')<br>limit=5"]
    H1 -->|OK| H2["confidence=0.7+relevance*0.2+focus*0.1"]

    E2 --> I["_detect_conflicts<br>conflict_detector.py:70"]
    F2 --> I
    G2 --> I
    H2 --> I

    I --> I1{"has_conflict AND severity >= 0.8?"}
    I1 -->|yes| SKIP
    I1 -->|no| J[DecisionResult]

    J --> K["append_event('cognitive.decision')<br>cognitive_controller.py:222"]
    K --> L["Return: action_type, params, confidence, reasoning"]
```

> **UWAGA:** `cognitive_controller.__init__` tworzy instancje `LearningEngine`, `KnowledgeGraph`, `AttentionController`, `ConflictDetector` — ale w `decide()` faktycznie wołane są TYLKO: `ensure_user`, `predict_next_action`, `_check_resources`, `_detect_conflicts`. **LearningEngine i KnowledgeGraph nie mają żadnych wywołań metod w decide().**

---

## 6. Agent Worker — Prawdziwa aktywna pętla runtime

### DOWODY:

- start_worker_once: `aihub/agent_worker.py:162-178`
- \_run_loop: `aihub/agent_worker.py:30-157`
- agent_tick: `aihub/agent_engine.py:296-400`
- Startup call: `aihub/main.py:96` → `start_worker_once()`
- extract_facts_from_text: `aihub/agent_engine.py:55-99`
- plan_from_text: `aihub/agent_engine.py:102-149`
- execute_task: `aihub/agent_engine.py:167-190`

```mermaid
flowchart TD
    A["uvicorn start<br>main.py:96 → start_worker_once()"] --> B["threading.Thread(daemon=True)<br>agent_worker.py:173"]
    B --> C["_run_loop()<br>co AGENT_INTERVAL_S=3.5s<br>agent_worker.py:30"]

    C --> D{"get_agent_state(user_id)<br>enabled?"}
    D -->|disabled| D1["sleep(interval*2)"]
    D -->|enabled| E["asyncio.run(agent_tick())<br>agent_worker.py:88"]
    D1 --> C

    E --> F["agent_tick(user_id)<br>agent_engine.py:296"]
    F --> G["_pull_new_stm(since_ts)<br>agent_engine.py:26-47"]
    G --> H{nowe wiadomości?}
    H -->|nie| H1[return ok, processed=0]
    H -->|tak| I["Per message loop"]

    I --> I1["evolve(user_id, content, role)<br>psyche update"]
    I --> I2{"role == 'user'?"}
    I2 -->|yes| I3["extract_facts_from_text(content)<br>agent_engine.py:55-99"]
    I3 --> I4["add_fact() per extracted fact<br>heuristic: lubię/nazywam/pracuję/hasło"]
    I2 -->|yes| I5["plan_from_text(content)<br>agent_engine.py:102-149"]
    I5 --> I6["enqueue_task() per planned task<br>web.fetch / fs.write / system.snapshot"]

    I --> J["add_episode(batch summary)<br>agent_engine.py:371"]
    J --> K["update_cursor(last_ts)<br>agent_engine.py:378"]

    K --> L["Run queued tasks (max 8)"]
    L --> L1["claim_next_task()"]
    L1 --> L2["execute_task()<br>agent_engine.py:167"]
    L2 --> L3{"task type?"}
    L3 -->|web.fetch| L4["fetch_url → add_fact<br>agent_engine.py:200-220"]
    L3 -->|fs.write| L5["write_file → add_fact<br>agent_engine.py:223-243"]
    L3 -->|system.snapshot| L6["create_snapshot → add_fact<br>agent_engine.py:246-261"]
    L4 --> L7["complete_task()"]
    L5 --> L7
    L6 --> L7

    L7 --> M["append_event('agent.tick')"]
    M --> C

    E -->|error, retry max 3| E1["sleep(RETRY_DELAY * attempt)<br>agent_worker.py:93-102"]
    E1 --> C
```

---

## 7. Agent Loop (via API) — [BRAK CALLERÓW AUTOMATYCZNYCH]

### DOWODY:

- Endpoint: `aihub/agent_api.py:88-93` (`POST /agent/loop`)
- run_loop: `aihub/agent_loop.py:272-296`
- agent_cycle: `aihub/agent_loop.py:169-267`
- \_execute_action **STUB**: `aihub/agent_loop.py:127-160`

> **WAŻNE:** Ten moduł NIE jest wołany automatycznie przez runtime. Jest dostępny TYLKO przez `POST /agent/loop` endpoint. `agent_worker` (diagram 6) woła `agent_tick` z `agent_engine.py`, a NIE `agent_cycle` z `agent_loop.py`.

```mermaid
flowchart TD
    A["POST /agent/loop<br>agent_api.py:88"] --> B["run_loop(text, user_id, max_iters)<br>agent_loop.py:272"]
    B --> C["agent_cycle(user_id)<br>agent_loop.py:169"]

    C --> D["get_psyche_state → ensure_user<br>agent_loop.py:37"]
    D --> E["get_pending_messages(limit=20)<br>SELECT stm_messages<br>agent_loop.py:50"]
    E --> F{"messages?"}
    F -->|empty| F1[return 0 processed]
    F -->|yes| G["rank_messages(user_id, messages)<br>attention_controller.py:73"]

    G --> G1["Per msg: urgency (keyword), relevance (pattern)<br>score = 0.4*urgency + 0.6*relevance"]
    G1 --> G2[Sort desc, process top 3]

    G2 --> H["DecisionRequest → cognitive_controller.decide()"]
    H --> I["process_decision → conflict check<br>agent_loop.py:69-114"]
    I --> J{"conflict?"}
    J -->|yes| K[log + skip]
    J -->|no| L["_execute_action(action_type, params)<br>agent_loop.py:127"]

    L --> L1{"action_type"}
    L1 -->|query| M1["STUB: {query, context: 'memory_search_executed'}"]
    L1 -->|learn| M2["STUB: {topic, stored: True}"]
    L1 -->|research| M3["STUB: {topic, researched: True}"]
    L1 -->|action| M4["STUB: {action, executed: True}"]

    style M1 fill:#fdd,stroke:#900
    style M2 fill:#fdd,stroke:#900
    style M3 fill:#fdd,stroke:#900
    style M4 fill:#fdd,stroke:#900
```

---

## 8. Memory GC Pipeline — [BRAK CALLERÓW W RUNTIME]

### DOWODY:

- collect_garbage: `aihub/memory_gc.py:44-130`
- check_stale: `aihub/meta_memory.py:157-187`
- \_archive_old_facts: `aihub/memory_gc.py:136-160`
- \_remove_low_priority_facts: `aihub/memory_gc.py:172-196`
- knowledge_evolution.evolve_all: `aihub/knowledge_evolution.py` (jeśli count>2000)
- schedule_gc: `aihub/memory_gc.py:217-218` — **LOGGER-ONLY, NO-OP**
- **KTO WOŁA?** Grep po `collect_garbage|schedule_gc|memory_gc` → **NIKT w runtime.** Tylko definicje w `memory_gc.py`.

```mermaid
flowchart TD
    A["collect_garbage(user_id)<br>memory_gc.py:44<br>⚠️ NIKT NIE WOŁA W RUNTIME"]

    A --> B["check_stale(user_id, days=90)<br>meta_memory.py:157"]
    B --> C{"stale_ids?"}
    C -->|yes| D["soft-delete max 100<br>UPDATE deleted=1<br>memory_gc.py:76-78"]
    C -->|no| E[skip]

    D --> F["_archive_old_facts(threshold=30d)<br>memory_gc.py:136"]
    E --> F
    F --> G["SELECT WHERE ts < threshold<br>AND layer NOT IN L3,L3_archive"]
    G --> H["UPDATE layer='L3_archive'"]

    H --> I["_get_fact_count(user_id)<br>memory_gc.py:163"]
    I --> J{" > max_facts_per_user (5000)?"}
    J -->|yes| K["_remove_low_priority_facts<br>ORDER BY importance ASC, ts ASC<br>memory_gc.py:172"]
    J -->|no| L[skip]

    K --> M{" > compress_above_count (2000)?"}
    L --> M
    M -->|yes| N["knowledge_evolution.evolve_all(user_id)<br>TF-IDF dedup > 0.75 similarity"]
    M -->|no| O[skip]

    N --> P["VACUUM<br>memory_gc.py:207"]
    O --> P
    P --> Q["append_event('memory.gc')"]

    style A fill:#ff9,stroke:#c90
```

---

## 9. Autonauka — Trzy mechanizmy (z oceną aktywności)

### DOWODY:

- memory_engine auto-fact: `aihub/memory_engine.py:147-164`
- agent_engine extract_facts: `aihub/agent_engine.py:55-99`
- learning_engine: `aihub/learning_engine.py:1-310`
- Callers learning_engine: `cognitive_controller.py:27` (import), `cognitive_controller.py:82` (instancja), **BRAK wywołań metod**

```mermaid
flowchart LR
    subgraph A1 ["✅ AKTYWNY: memory_engine.process_turn"]
        ME1[user_msg] --> ME2{"keyword match?<br>lubię|nie lubię|preferuję<br>zawsze|nigdy|ważne<br>zakaz|nakaz<br>memory_engine.py:147-153"}
        ME2 -->|yes| ME3["add_fact(cały user_msg)<br>tags: user,preference,intent<br>memory_engine.py:155-164"]
        ME2 -->|no| ME4[skip]
    end

    subgraph A2 ["✅ AKTYWNY: agent_engine.extract_facts_from_text"]
        AE1["user_msg z STM<br>(via agent_tick)"] --> AE2{"heuristic keywords<br>lubię/nazywam/pracuję/hasło<br>agent_engine.py:55-99"}
        AE2 -->|match| AE3["add_fact()<br>tags: user,preference / identity / bio / safety"]
        AE2 -->|no| AE4[skip]
    end

    subgraph A3 ["🔴 NIEAKTYWNY: learning_engine.LearningEngine"]
        LE1["6 regex patterns<br>learning_engine.py:35-95"] --> LE2["extract_facts_from_message<br>validate + dedup hash"]
        LE2 --> LE3["add_fact per match<br>per-rule importance/confidence"]
        LE3 --> LE4["⚠️ NIKT NIE WOŁA<br>process_turn() ani<br>learn_from_reflection()"]
    end

    style A1 fill:#dfd,stroke:#090
    style A2 fill:#dfd,stroke:#090
    style A3 fill:#fdd,stroke:#900
```

---

## 10. Research Engine — [PLACEHOLDER, 0 WYNIKÓW]

### DOWODY:

- ResearchEngine.research: `aihub/research_engine.py:112-181`
- \_generate_placeholder_results: `aihub/research_engine.py:184-189` → **return []**
- Callers: `cognitive_controller._decide_research` zwraca DecisionResult z `action_type="research"` ale `_execute_action` w `agent_loop.py:155` to STUB. `agent_engine.execute_task` NIE obsługuje type="research".

```mermaid
flowchart TD
    A["research(user_id, query)<br>research_engine.py:112"] --> B["ensure_user"]
    B --> C["_generate_placeholder_results(query)<br>research_engine.py:184"]
    C --> D["return []<br>⚠️ ZAWSZE PUSTA LISTA<br>logger.warning: no search API configured"]
    D --> E["results = [] → 0 facts extracted"]
    E --> F["append_event('research.completed', results_count=0)"]

    style C fill:#fdd,stroke:#900
    style D fill:#fdd,stroke:#900
```

---

## 11. Architektura pamięci — Warstwy i przepływ (zweryfikowane)

### DOWODY:

- Schemat DB: `aihub/db.py:22-120`
- STM (stm_messages): `aihub/db.py:67-75`
- L1/L2 (memory_nodes): `aihub/db.py:27-50`
- FTS5 (memory_fts): `aihub/db.py:53-60`
- memory_meta: `aihub/db.py:95-112`
- memory_facts VIEW: `aihub/db.py:115-120`
- Vector files: `aihub/vector_engine.py:16-19`

```mermaid
flowchart TB
    subgraph WRITE ["Zapis (POST /memory/add + agent_tick)"]
        U[User msg] --> PT["process_turn()<br>memory_engine.py:127"]
        A2[Assistant msg] --> PT
        PT --> VH["vector_hook.remember_turn()<br>→ FAISS add_memory()"]
        PT --> STM_W["add_stm() x2<br>→ stm_messages"]
        PT --> L1_W["add_episode(summary)<br>→ memory_nodes L1"]
        PT -->|"keyword match"| L2_W["add_fact()<br>→ memory_nodes L2"]
        L1_W --> FTS_W["upsert FTS5<br>db.py:330-345"]
        L2_W --> FTS_W
    end

    subgraph STORAGE ["SQLite (db.py)"]
        STM["stm_messages<br>max STM_MAX_MESSAGES<br>FIFO prune"]
        MN["memory_nodes<br>id, user_id, layer, content<br>tags, meta, ts, importance<br>confidence, deleted"]
        FTS["memory_fts (FTS5)<br>content, user_id, layer, node_id"]
        MM["memory_meta<br>access_count, freshness<br>usage_score, stale_warning"]
        PS["psyche_state<br>mood, energy, focus, style<br>temperature, traits"]
        EL["event_log<br>user_id, type, data, ts"]
    end

    subgraph VECTOR_STORE ["Vector (pliki)"]
        VI["data/vector.index (FAISS)"]
        VM["data/vector_meta.json"]
    end

    subgraph READ ["Odczyt (POST /memory/search)"]
        Q[query] --> RC["retrieve_context()<br>memory_engine.py:193"]
        RC --> STM_R["get_stm()"]
        RC --> FTS_R["search_nodes_fts() L1 + L2<br>FTS5 MATCH → fallback LIKE"]
        FTS_R --> TFIDF["_vector_rerank()<br>vector_index.py TF-IDF<br>NIE FAISS!"]
        TFIDF --> BLEND["score = 0.72*cos + 0.18*imp + 0.10*conf"]
    end

    VH --> VI
    VH --> VM
    STM_W --> STM
    L1_W --> MN
    L2_W --> MN
    FTS_W --> FTS
    STM_R --> STM
    FTS_R --> FTS

    style VH fill:#fdb,stroke:#f60
    style TFIDF fill:#bef,stroke:#09c
    style STM fill:#adf,stroke:#06c
    style MN fill:#bef,stroke:#09c
```

---

## Podsumowanie: Co jest w kodzie, a co NIE żyje w runtime

| Moduł                | Import w runtime?                                | Wywołanie w runtime?                                                        | Status                  |
| -------------------- | ------------------------------------------------ | --------------------------------------------------------------------------- | ----------------------- |
| memory_engine        | ✅ main.py:44                                    | ✅ /memory/add, /memory/search, /turn, agent_tick                           | **AKTYWNY**             |
| psyche_engine        | ✅ main.py:47                                    | ✅ /psyche/\*, /memory/add, agent_tick                                      | **AKTYWNY**             |
| vector_hook          | ✅ memory_engine.py:21                           | ✅ process_turn() → remember_turn()                                         | **AKTYWNY (zapis)**     |
| vector_engine        | ✅ vector_hook.py:1                              | ✅ add_memory() z FAISS                                                     | **AKTYWNY (zapis)**     |
| vector_index         | ✅ memory_engine.py:22-27                        | ✅ \_vector_rerank() w retrieve_context()                                   | **AKTYWNY (odczyt)**    |
| cognitive_controller | ✅ main.py:49                                    | ✅ POST /cognitive/decide                                                   | **AKTYWNY**             |
| agent_worker         | ✅ main.py:51                                    | ✅ start_worker_once() at startup                                           | **AKTYWNY**             |
| agent_engine         | ✅ agent_api.py:16 + agent_worker.py:12          | ✅ agent_tick() co 3.5s                                                     | **AKTYWNY**             |
| agent_loop           | ✅ agent_api.py:84                               | ⚠️ TYLKO POST /agent/loop (ręczny)                                          | **SEMI-AKTYWNY**        |
| attention_controller | ✅ cognitive_controller.py:21 + agent_loop.py:17 | ⚠️ W agent_loop (ręczny), w cognitive_controller instancja ale brak wywołań | **SEMI-AKTYWNY**        |
| prediction_engine    | ✅ cognitive_controller.py:28                    | ✅ predict_next_action() w decide()                                         | **AKTYWNY**             |
| conflict_detector    | ✅ cognitive_controller.py:22 + main.py:50       | ✅ check_conflict() w decide()                                              | **AKTYWNY**             |
| meta_memory          | ✅ cognitive_controller.py:26                    | ✅ check_stale() w \_estimate_memory_pressure()                             | **AKTYWNY (częściowo)** |
| knowledge_graph      | ✅ cognitive_controller.py:24 + main.py          | ✅ stats() w /cognitive/health                                              | **AKTYWNY (częściowo)** |
| learning_engine      | ✅ cognitive_controller.py:27                    | ❌ Instancja istnieje, BRAK wywołań metod                                   | **MARTWY KOD**          |
| research_engine      | ❌ Brak importu w main/agent                     | ❌ Placeholder (return [])                                                  | **MARTWY KOD**          |
| memory_gc            | ❌ Brak importu w main/agent                     | ❌ Nikt nie woła collect_garbage()                                          | **MARTWY KOD**          |
| knowledge_evolution  | ✅ memory_gc.py:17                               | ❌ Tylko przez memory_gc (który jest martwy)                                | **MARTWY KOD**          |
