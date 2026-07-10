# AI-Hub — Audyt: MEMORY + PSYCHIKA + AUTONAUKA + RESEARCH

> **Wersja:** 1.0 | **Data:** 2025-01-XX | **Tryb:** 100% READ-ONLY, dowody z kodu
> **Pliki audytowane:** 18 modułów Python w `aihub/`

---

## TL;DR (po chłopsku)

| Podsystem                       | Status            | Szczegóły                                                                                                                                                      |
| ------------------------------- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Memory (STM/L1/L2)**          | ✅ DZIAŁA         | Pełny pipeline: zapis STM → epizody L1 → fakty L2, FTS5 search, TF-IDF reranking. Realne tabele, realne dane.                                                  |
| **Memory (Vector/FAISS)**       | ⚠️ CZĘŚCIOWO      | `vector_hook.py` → `vector_engine.py` (FAISS+SentenceTransformer) wymaga `faiss-cpu` + `sentence-transformers`. Crashuje na importach jeśli nie zainstalowane. |
| **Memory (Meta/GC/Evolution)**  | ✅ DZIAŁA         | `meta_memory.py` (tracking access/freshness), `memory_gc.py` (stale→archive→vacuum), `knowledge_evolution.py` (TF-IDF dedup+merge). Pełny cykl życia.          |
| **Psychika**                    | ✅ DZIAŁA         | Sentiment analysis (PL), mood/energy/focus evolve(), trait learning, temperature adaptation. Wpływa na CognitiveController.                                    |
| **Autonauka (memory_engine)**   | ✅ DZIAŁA         | 8 keyword triggers w `process_turn()`. Prosty ale działa.                                                                                                      |
| **Autonauka (learning_engine)** | ⚠️ MARTWY IMPORT  | `LearningEngine` z 6 regex rulami istnieje, ale **nikt nie wywołuje** `process_turn()` z learning_engine. CognitiveController go instantiuje ale nie woła.     |
| **Research**                    | 🔴 PLACEHOLDER    | `_generate_placeholder_results()` zwraca pustą listę. Komentarz: _"no search API configured"_. Brak zewnętrznego API.                                          |
| **Knowledge Graph**             | 🔴 IN-MEMORY ONLY | Dane w `dict` singletona, brak persystencji do DB. Restart → puste.                                                                                            |

---

## 1. MEMORY — Architektura warstwowa

### 1.1 Warstwy pamięci

```
┌─────────────────────────────────────────────────┐
│  STM (Short-Term Memory) — stm_messages table   │
│  Max: STM_MAX_MESSAGES = 200 per user            │
│  Prune: automatic FIFO (prune_stm)               │
├─────────────────────────────────────────────────┤
│  L1 (Episodic) — memory_nodes WHERE layer='L1'  │
│  Cap: EPISODES_MAX_PER_USER = 20000              │
│  Min importance: 0.55 / confidence: 0.55         │
├─────────────────────────────────────────────────┤
│  L2 (Semantic/Fakty) — memory_nodes layer='L2'  │
│  Cap: LTM_MAX_FACTS_PER_USER = 20000            │
│  Min importance: 0.60 / confidence: 0.55         │
├─────────────────────────────────────────────────┤
│  L3/L3_archive — archiwum (knowledge_evolution   │
│  + memory_gc przenoszą tu stare/niskopriorytet)  │
└─────────────────────────────────────────────────┘
```

**Dowody:**

- Stałe config: [aihub/config.py](aihub/config.py#L18-L20) → `STM_MAX_MESSAGES=200`, `LTM_MAX_FACTS_PER_USER=20000`, `EPISODES_MAX_PER_USER=20000`
- Schema tabeli `memory_nodes`: [aihub/db.py](aihub/db.py#L34-L46) → kolumny: id, user_id, layer, content, tags, meta, ts, importance, confidence, deleted
- Schema tabeli `stm_messages`: [aihub/db.py](aihub/db.py#L61-L69) → id, user_id, role, content, meta, ts
- FTS5: [aihub/db.py](aihub/db.py#L53-L58) → `memory_fts` USING fts5 (content, user_id UNINDEXED, layer UNINDEXED, node_id UNINDEXED)
- View `memory_facts`: [aihub/db.py](aihub/db.py#L121-L126) → alias do `memory_nodes`

### 1.2 Tabele DB (SQLite3 WAL)

| Tabela         | Cel                                         | Definiujący plik              |
| -------------- | ------------------------------------------- | ----------------------------- |
| `memory_nodes` | Główna tabela L0/L1/L2, PK=id TEXT (SHA256) | [db.py:34](aihub/db.py#L34)   |
| `memory_fts`   | FTS5 virtual table do full-text search      | [db.py:53](aihub/db.py#L53)   |
| `stm_messages` | STM — krótka pamięć dialogu                 | [db.py:61](aihub/db.py#L61)   |
| `psyche_state` | Stan psychiczny per user_id                 | [db.py:73](aihub/db.py#L73)   |
| `event_log`    | Logi zdarzeń systemowych                    | [db.py:85](aihub/db.py#L85)   |
| `snapshots`    | Snapshoty DB                                | [db.py:95](aihub/db.py#L95)   |
| `memory_meta`  | Meta-dane o użyciu faktów (GC, freshness)   | [db.py:102](aihub/db.py#L102) |
| `memory_facts` | VIEW → alias do memory_nodes                | [db.py:121](aihub/db.py#L121) |

**Indexy:** `idx_nodes_user_layer_ts`, `idx_nodes_user_imp`, `idx_stm_user_ts`, `idx_event_user_ts`, `idx_meta_priority` — [db.py:47-50](aihub/db.py#L47-L50), [db.py:70](aihub/db.py#L70), [db.py:92](aihub/db.py#L92), [db.py:117](aihub/db.py#L117)

### 1.3 Funkcje zapisu (memory_engine.py)

#### `add_stm(user_id, role, content, meta)` — [memory_engine.py:43](aihub/memory_engine.py#L43)

- Wstawia wiersz do `stm_messages` via `insert_stm_message()`
- Obcina `prune_stm(user_id, STM_MAX_MESSAGES)` — FIFO, keep najnowsze 200
- Loguje event `stm.add`

#### `_importance_from_text(text)` — [memory_engine.py:55](aihub/memory_engine.py#L55)

- Base: **0.45**
- `"zapamiętaj"`, `"ważne"`, `"kluczowe"` → **+0.25**
- `len(text) > 500` → **+0.10**
- `"hasło"`, `"token"` → **+0.10**
- Max 1.0

#### `_confidence_from_text(text)` — [memory_engine.py:65](aihub/memory_engine.py#L65)

- Base: **0.60**
- `"jestem"`, `"mam"`, `"nazywam się"` → **+0.10**
- `"chyba"`, `"może"` → **-0.15**
- Range: [0.20 .. 0.95]

#### `add_episode(user_id, summary, meta)` — [memory_engine.py:76](aihub/memory_engine.py#L76)

- Tworzy node L1, `importance ≥ 0.55`, `confidence ≥ 0.55`
- ID = `_id_for(summary, user_id, "L1")` → SHA256 hash → **automatyczna deduplikacja**
- Wywołuje `_enforce_caps(user_id)`

#### `add_fact(user_id, fact, tags, meta)` — [memory_engine.py:90](aihub/memory_engine.py#L90)

- Tworzy node L2, `importance ≥ 0.60`, `confidence ≥ 0.55`
- Tags: zawsze zawiera `"fact"` → `tags + ["fact"]`
- ID = `_id_for(fact, user_id, "L2")` → SHA256 → **dedup**
- Wywołuje `_enforce_caps(user_id)`

#### `_enforce_caps(user_id)` — [memory_engine.py:107](aihub/memory_engine.py#L107)

- Sprawdza count(L1) > `EPISODES_MAX_PER_USER` (20000) — usuwa najstarsze o najniższym importance
- Sprawdza count(L2) > `LTM_MAX_FACTS_PER_USER` (20000) — to samo
- **Soft-delete:** `DELETE` lub `UPDATE SET deleted=1` (zależy od contextu)

#### `_id_for(text, user_id, layer)` — [memory_engine.py:30](aihub/memory_engine.py#L30)

- `hashlib.sha256(f"{layer}:{user_id}:{text}".encode()).hexdigest()`
- **Deterministyczny** — ten sam tekst + user + layer = ten sam ID
- Skutek: `upsert_node()` w [db.py:276](aihub/db.py#L276) robi `INSERT ... ON CONFLICT(id) DO UPDATE` → nie tworzy duplikatów

### 1.4 Główny pipeline zapisu: `process_turn()`

**Plik:** [memory_engine.py:130](aihub/memory_engine.py#L130)

```python
def process_turn(user_id, user_msg, assistant_msg, intent, meta):
    # 1. Vector hook — zapisuje do FAISS (jeśli działa)
    remember_turn(user_msg, assistant_msg)

    # 2. STM — oba komunikaty do stm_messages
    add_stm(user_id, "user", user_msg, meta)
    add_stm(user_id, "assistant", assistant_msg, meta)

    # 3. Episodic (L1)
    summary = f"U: {user_msg[:120]} || A: {assistant_msg[:120]}"
    add_episode(user_id, summary, meta)

    # 4. AUTO-FACT EXTRACTION (Autonauka)
    keywords = ["lubię", "nie lubię", "preferuję", "zawsze", "nigdy", "ważne", "zakaz", "nakaz"]
    for kw in keywords:
        if kw in user_msg.lower():
            add_fact(user_id, user_msg, tags=["user","preference",intent],
                     meta={"source_episode": ep_id})
            break
```

**Wywoływane z:** [main.py:192](aihub/main.py#L192) — endpoint `POST /memory/add`

### 1.5 Pipeline odczytu: `retrieve_context()`

**Plik:** [memory_engine.py:178](aihub/memory_engine.py#L178)

```
1. STM: get_stm(user_id, limit=min(20, STM_MAX_MESSAGES))
2. FTS search L1: search_nodes_fts(user_id, "L1", query, limit*20) → kandydaci
3. FTS search L2: search_nodes_fts(user_id, "L2", query, limit*40) → kandydaci
4. TF-IDF reranking: _vector_rerank(query, all_candidates, limit*3)
5. Blend score = 0.72*cosine + 0.18*importance + 0.10*confidence
6. Sort → top `limit` wyników
7. Return: {stm: [...], episodic: [...], semantic: [...]}
```

**Blend weights:** `0.72 cosine + 0.18 importance + 0.10 confidence`
**Wywoływane z:** [main.py:213](aihub/main.py#L213) — endpoint `POST /memory/search`

### 1.6 TF-IDF Reranking (vector_index.py)

**Plik:** [aihub/vector_index.py](aihub/vector_index.py)

| Funkcja                               | Opis                                                                           | Linia                            |
| ------------------------------------- | ------------------------------------------------------------------------------ | -------------------------------- |
| `tokenize(text)`                      | Regex `[0-9A-Za-zÀ-ÿ_]+`, lowercase, cap `VEC_MAX_TOKENS_PER_DOC=6000`         | [L12](aihub/vector_index.py#L12) |
| `build_df(docs)`                      | Document frequency: ile dokumentów zawiera dany term                           | [L21](aihub/vector_index.py#L21) |
| `prune_vocab(df, n_docs)`             | Usuwa tomy < `VEC_MIN_DF=2` lub > `VEC_MAX_DF=0.90`, cap `VEC_MAX_VOCAB=60000` | [L29](aihub/vector_index.py#L29) |
| `tfidf_vector(tokens, df, n_docs)`    | Sublinear TF `1+log(1+f)` × IDF `log((N+1)/(df+1))+1`, L2 norm                 | [L44](aihub/vector_index.py#L44) |
| `cosine_sparse(a, b)`                 | Dot product na sparse dicts, iteruje mniejszy                                  | [L60](aihub/vector_index.py#L60) |
| `topk_cosine(query_vec, doc_vecs, k)` | Sort → top-k                                                                   | [L72](aihub/vector_index.py#L72) |

**Status:** ✅ DZIAŁA — pełna implementacja, bez zewnętrznych zależności (pure Python + `math` + `re`).

### 1.7 Vector Hook → FAISS (vector_engine.py)

**Chain:** `process_turn()` → [vector_hook.py:1](aihub/vector_hook.py#L1) `remember_turn()` → [vector_engine.py:127](aihub/vector_engine.py#L127) `add_memory(text)`

- **Model:** SentenceTransformer `all-MiniLM-L6-v2` (dim=384) — [vector_engine.py:15](aihub/vector_engine.py#L15)
- **Index:** FAISS `IndexFlatL2` — [vector_engine.py:49](aihub/vector_engine.py#L49)
- **Persist:** `data/vector.index` + `data/vector_meta.json` — [vector_engine.py:17-18](aihub/vector_engine.py#L17)
- **Problem:** Wymaga `sentence-transformers` i `faiss-cpu` w runtime — jeśli nie zainstalowane, `_init_model()` rzuca `RuntimeError` — [vector_engine.py:37-42](aihub/vector_engine.py#L37)
- **Kto szuka?** `vector_engine.search()` istnieje ([vector_engine.py:155](aihub/vector_engine.py#L155)) ale **nikt go nie wywołuje** z głównego pipeline. `retrieve_context()` używa TYLKO FTS5 + TF-IDF z `vector_index.py`.

### 1.8 Meta-Memory (meta_memory.py)

**Plik:** [aihub/meta_memory.py](aihub/meta_memory.py)

| Funkcja                                                     | Opis                                                                         | Linia                             |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------- | --------------------------------- |
| `track_access(fact_id)`                                     | Inkrementuje `access_count`, update `last_access`, boost `usage_score +0.05` | [L72](aihub/meta_memory.py#L72)   |
| `get_usage_score(fact_id)`                                  | `access_count * 0.02` (max 0.5) + recency (max 0.5, decay 365 dni)           | [L91](aihub/meta_memory.py#L91)   |
| `get_freshness_score(fact_id)`                              | Decay od `creation_ts`, boost jeśli `last_access` < 24h                      | [L115](aihub/meta_memory.py#L115) |
| `check_stale(user_id, days_threshold)`                      | Szuka w `memory_meta WHERE last_access < threshold AND stale_warning=0`      | [L140](aihub/meta_memory.py#L140) |
| `compute_overall_priority(fact_id, importance, confidence)` | `0.3*importance + 0.2*confidence + 0.3*usage + 0.3*recency`                  | [L174](aihub/meta_memory.py#L174) |
| `rank_facts(user_id, limit)`                                | `SELECT ... ORDER BY overall_priority DESC`                                  | [L204](aihub/meta_memory.py#L204) |

**⚠️ Problem:** `track_access()` **nigdzie nie jest wywoływany** w `retrieve_context()`. Meta-memory istnieje, ale pipeline odczytu pamięci nie korzysta z trackingu.

### 1.9 Knowledge Evolution (knowledge_evolution.py)

**Plik:** [aihub/knowledge_evolution.py](aihub/knowledge_evolution.py)

| Funkcja                                  | Opis                                                                      | Linia                                     |
| ---------------------------------------- | ------------------------------------------------------------------------- | ----------------------------------------- |
| `_compute_semantic_similarity(facts)`    | TF-IDF → pairwise cosine → pary > `similarity_threshold=0.75`             | [L40](aihub/knowledge_evolution.py#L40)   |
| `_merge_facts(fact1, fact2)`             | Wybór lepszego `(importance+confidence)/2`, merge tagów + meta            | [L66](aihub/knowledge_evolution.py#L66)   |
| `deduplicate(user_id, layer)`            | Znajdź podobne → merge → soft-delete gorszy                               | [L93](aihub/knowledge_evolution.py#L93)   |
| `reinforce(user_id, fact_id, increment)` | `importance += 0.1`, `confidence += 0.1`, max 0.99                        | [L160](aihub/knowledge_evolution.py#L160) |
| `archive_stale(user_id, days=90)`        | Przenosi stare fakty z L1/L2 na `L3` (importance<0.45 OR confidence<0.45) | [L197](aihub/knowledge_evolution.py#L197) |
| `evolve_all(user_id)`                    | `deduplicate("L1") + deduplicate("L2") + archive_stale()`                 | [L251](aihub/knowledge_evolution.py#L251) |

**Status:** ✅ Kompletna implementacja, wywoływana przez `MemoryGC`.

### 1.10 Memory GC (memory_gc.py)

**Plik:** [aihub/memory_gc.py](aihub/memory_gc.py)

**Pipeline `collect_garbage(user_id)`** — [memory_gc.py:47](aihub/memory_gc.py#L47):

1. **Stale detection:** `meta_memory.check_stale(days=90)` → soft-delete (limit 100/cykl)
2. **Archive:** Przenosi fakty > 30 dni do `L3_archive`
3. **Pressure relief:** Jeśli count > `max_facts_per_user=5000` → usuwaj najniższy importance
4. **Compress:** Jeśli count > `compress_above_count=2000` → `knowledge_evolution.evolve_all()`
5. **Vacuum:** `VACUUM` na DB

**⚠️ Kto go woła?** GC jest wołany z `GET /cognitive/health` w [main.py:425](aihub/main.py#L425) — ale ten endpoint **nie triggeruje** `collect_garbage()`, tylko pokazuje statystyki. GC nie ma schedulera — `schedule_gc()` loguje info ale **nie robi nic** (brak cron/background task).

---

## 2. PSYCHIKA — Architektura emocji i decyzji

### 2.1 Stan psychiczny (psyche_engine.py)

**Plik:** [aihub/psyche_engine.py](aihub/psyche_engine.py)

#### Domyślny stan — `_baseline()` [psyche_engine.py:49](aihub/psyche_engine.py#L49):

```python
{
    "mood": 0.55,           # Nastrój [0..1]
    "energy": 0.70,         # Energia [0..1]
    "focus": 0.65,          # Skupienie [0..1]
    "style": "ziomek",      # Styl komunikacji
    "temperature": 0.65,    # LLM temperature
    "traits": {
        "agreeableness": 0.55,   # Ugodowość
        "directness": 0.70,     # Bezpośredniość
        "sarcasm": 0.35,        # Sarkazm
        "swearing": 0.50,       # Przeklinanie
        "patience": 0.45,       # Cierpliwość
        "memory_hunger": 0.80   # Chęć zapamiętywania
    }
}
```

**Persystencja:** `psyche_state` tabela ([db.py:73](aihub/db.py#L73)) → `mood`, `energy`, `focus`, `style`, `temperature`, `traits` (JSON), `updated_at`

### 2.2 Analiza sentymentu — `analyze_sentiment(text)` [psyche_engine.py:80](aihub/psyche_engine.py#L80)

**Słowniki PL:**

- `_POS` (18 słów): dobrze, świetnie, super, ok, spoko, kocham, lubię, pięknie, dzięki, fajnie... — [psyche_engine.py:9](aihub/psyche_engine.py#L9)
- `_NEG` (16 słów): źle, problem, błąd, nienawidzę, chujowo, wkurwia, smutek, złość, gniew... — [psyche_engine.py:22](aihub/psyche_engine.py#L22)
- `_INTENSIFIERS` (6 słów): bardzo, mega, strasznie, naprawdę, kurwa, cholernie — [psyche_engine.py:38](aihub/psyche_engine.py#L38)

**Formuła:**

```
sentiment s = (pos_count - neg_count) / max(3, pos_count + neg_count)  → clamp [-1..1]
confidence  = 0.45 + 0.12*(pos+neg) + 0.05*intensifiers              → clamp [0..0.95]
```

### 2.3 Ewolucja stanu — `evolve(user_id, text, role)` [psyche_engine.py:95](aihub/psyche_engine.py#L95)

To jest **serce psychiki**. Wywołanie = każda wiadomość.

#### Wagi wg roli:

- `role == "user"` → `role_w = 1.0` (pełny wpływ)
- `role == "assistant"` → `role_w = 0.35` (słabszy wpływ)

#### Update mood/energy/focus:

```
mood   += role_w * 0.18 * sentiment * confidence              (natural drift → 0.55)
energy += role_w * 0.06 * sentiment * confidence - 0.01*(words/80)
focus  += role_w * 0.05 * confidence - 0.02*(words/200)
```

#### Trait learning:

- **Harsh language** (neg>pos & neg≥2): `directness+0.03, patience-0.03, swearing+0.02, sarcasm+0.02`
- **Friendly language** (pos>neg & pos≥2): `agreeableness+0.02, patience+0.02, sarcasm-0.01`

#### Temperature adaptation:

```
temperature = 0.55 + 0.25 * (mood - 0.5)   → clamp [0.25..0.95]
```

**Efekty:** Zły mood (0.3) → temperature 0.50 (bardziej deterministyczny). Dobry mood (0.8) → temperature 0.625 (bardziej kreatywny).

### 2.4 Refleksja — `reflect(user_id, context)` [psyche_engine.py:155](aihub/psyche_engine.py#L155)

- Frequency analysis ostatnich 20 wiadomości
- Top 8 topics (słowa ≥ 4 znaki, freq ≥ 2)
- Generuje tekstowy opis stanu: "mood: wysoki/niski, energy: ..."
- Loguje `psyche.reflect` event

**Wywoływany z:** [main.py:174](aihub/main.py#L174) — `POST /psyche/reflect`

### 2.5 Wpływ psychiki na decyzje (cognitive_controller.py)

**Plik:** [aihub/cognitive_controller.py](aihub/cognitive_controller.py)

**Jak psychika wpływa na `decide()`:**

1. **Energy jako urgency:** [cognitive_controller.py:169](aihub/cognitive_controller.py#L169)

    ```python
    decision_context["urgency"] = psyche.get("energy", 0.5)
    ```

    → Energy level → urgency → wpływa na research depth i response priority

2. **Focus na query confidence:** [cognitive_controller.py:325](aihub/cognitive_controller.py#L325)

    ```python
    adjusted_confidence = min(1.0, 0.7 + relevance*0.2 + focus*0.1)
    ```

3. **Energy na limit wyników:** [cognitive_controller.py:327](aihub/cognitive_controller.py#L327)

    ```python
    limit = 10 if energy < 0.3 else 20
    ```

    → Niski energy = mniej wyników (oszczędność zasobów)

4. **Focus na learning confidence:** [cognitive_controller.py:353](aihub/cognitive_controller.py#L353)

    ```python
    adjusted_confidence = min(1.0, 0.65 + energy*0.1 + focus*0.15)
    ```

5. **Memory pressure z meta_memory:** [cognitive_controller.py:439](aihub/cognitive_controller.py#L439)
    ```python
    stale = check_stale(user_id, days_threshold=30)
    pressure = len(stale) / 500.0
    ```

### 2.6 Reflection Engine (reflection_engine.py)

**Plik:** [aihub/reflection_engine.py](aihub/reflection_engine.py) — osobny, bardziej rozbudowany silnik refleksji.

**Cechy:**

- Kategorie: `_PREFERENCE_PATTERNS`, `_PROBLEM_PATTERNS`, `_GOAL_PATTERNS`, `_EMOTION_PATTERNS` — [reflection_engine.py:12-53](aihub/reflection_engine.py#L12)
- `_analyze_sentence_structure()` — zlicza pytania, asercje, avg sentence length
- `_detect_topics()` — frequency na słowach ≥4 znaków
- `_compare_mood_trend()` — positive vs negative words
- `reflect()` → zwraca `{insights, patterns, topics, mood_trend, recommendations}`

**⚠️ Stan:** Klasa istnieje, singleton `_reflection_engine` utworzony, ale **nigdzie nie jest wywoływany** z main.py ani agent_loop.py. Martwy import potencjalnie — nikt nie woła `reflection_engine.reflect()`.

---

## 3. AUTONAUKA — Ekstrakcja wiedzy z dialogu

### 3.1 Mechanizm #1: memory_engine.process_turn() — [memory_engine.py:130](aihub/memory_engine.py#L130)

**Status: ✅ AKTYWNY** — wywoływany z `POST /memory/add`

**Trigger:** 8 polskich słów kluczowych:

```python
["lubię", "nie lubię", "preferuję", "zawsze", "nigdy", "ważne", "zakaz", "nakaz"]
```

**Logika:** Jeśli user_msg.lower() zawiera KTÓREKOLWIEK z powyższych → `add_fact(user_id, user_msg, tags=["user","preference",intent])`.

**Ograniczenia:**

- Tylko **1 fakt per turn** (break po pierwszym match)
- **Cały user_msg** jest faktem (nie wyciąga fragmentu)
- Brak analizy kontekstu — `"nie lubię jak pada"` → zapisuje cały tekst jako fakt
- Tylko polskie keywords
- Deduplikacja przez SHA256 ID — ten sam tekst nie zostanie zduplikowany

### 3.2 Mechanizm #2: LearningEngine — [learning_engine.py](aihub/learning_engine.py)

**Status: ⚠️ ISTNIEJE ALE MARTWY** — nikt nie wywołuje go z HTTP endpointów.

**6 reguł regex:**

| Rule              | Pattern (przykład)                      | Tags                     | Importance | Confidence |
| ----------------- | --------------------------------------- | ------------------------ | ---------- | ---------- |
| `user_identity`   | `jestem ...`, `mam na imię ...`         | user, identity, personal | 0.75       | 0.85       |
| `user_preference` | `lubię ...`, `wolę ...`, `ulubiony ...` | user, preference         | 0.65       | 0.75       |
| `user_work`       | `pracuję w ...`, `jestem jako ...`      | user, work, profession   | 0.70       | 0.80       |
| `user_goal`       | `chcę ...`, `marzę ...`, `celem mi ...` | user, goal, aspiration   | 0.80       | 0.70       |
| `technical_fact`  | `używam ...`, `korzystam z ...`         | technical, skill, tool   | 0.60       | 0.75       |
| `constraint`      | `nie mogę ...`, `nie mam ...`           | constraint, limitation   | 0.70       | 0.80       |

**Cechy:**

- Regex ekstrakcja → wyciąga **fragment** pasujący (nie cały tekst)
- Per-rule importance/confidence (nie heurystyka z tekstu)
- In-memory deduplikacja: `self.learned_facts: Set[str]` — SHA256 hash per (fact_text, category)
- `_validate_extraction()` — odrzuca < 3 znaki, < 2 słowa, emaile, URLe
- Przetwarza OBIE strony: user_msg + assistant_msg (asystent z wagą `importance*0.7, confidence*0.8`)
- `learn_from_reflection()` — meta-nauka z refleksji → dodaje topics + recommendations jako fakty

**Problem:**

- `CognitiveController.__init__()` instantiuje `LearningEngine()` ([cognitive_controller.py:80](aihub/cognitive_controller.py#L80)) ale **nigdy nie wywołuje** `self.learning_engine.process_turn()` ani `extract_facts_from_message()`.
- Publiczne API `learning_engine.process_turn()` ([learning_engine.py:345](aihub/learning_engine.py#L345)) nie jest importowane nigdzie w `main.py`.
- Efekt: **cały LearningEngine to martwy kod**.

### 3.3 Porównanie dwóch mechanizmów

| Cecha        | memory_engine (aktywny)                | learning_engine (martwy)                  |
| ------------ | -------------------------------------- | ----------------------------------------- |
| Trigger      | 8 keyword substring match              | 6 regex rules z capture groups            |
| Ekstrakt     | Cały user_msg                          | Fragment (regex match)                    |
| Walidacja    | Brak                                   | Min 3 znaki, min 2 słowa, filtr email/URL |
| Deduplikacja | SHA256 na content                      | SHA256 na (category:content)              |
| Importance   | Z `_importance_from_text()` heurystyki | Per-rule stała (0.60-0.80)                |
| Asystent     | Nie                                    | Tak (z niższą wagą)                       |
| Meta-nauka   | Nie                                    | `learn_from_reflection()`                 |
| **Status**   | **DZIAŁA**                             | **MARTWY KOD**                            |

---

## 4. RESEARCH — System wyszukiwania zewnętrznego

### 4.1 ResearchEngine — [research_engine.py](aihub/research_engine.py)

**Status: 🔴 PLACEHOLDER**

**Architektura:**

- `ResearchEngine` klasa z `research()` (async) i `research_detailed()`
- `_extract_facts_from_text()` — regex extraction (definitions, statistics, dates, claims)
- `_calculate_relevance()` — word overlap score
- `_generate_placeholder_results()` — **zwraca pustą listę** → [research_engine.py:228](aihub/research_engine.py#L228)

**Komentarz w kodzie** [research_engine.py:134](aihub/research_engine.py#L134):

```python
# W rzeczywistej implementacji tu byłaby integracja z search API
# (np. SerpAPI, Google Search, Bing Search, etc.)
# Dla MVP, zwracamy strukturę z placeholder'ami
```

**Co by działało gdyby było API:**

1. `research(user_id, query)` → call search API → extract facts → `add_fact()` per fakt → log
2. `research_detailed(user_id, topic, subtopics)` → `research()` na każdym subtopic (max 5)

**Kto to woła:**

- Nikt z `main.py` — brak endpointu `/research`
- `agent_loop._execute_action()` ma `elif action_type == "research"` ([agent_loop.py:145](aihub/agent_loop.py#L145)) ale **nie importuje** `research_engine` — zwraca tylko `{"topic": topic, "researched": True}` stub
- `CognitiveController._decide_research()` zwraca `DecisionResult(action_type="research")` ale `agent_loop` to ignoruje

### 4.2 Knowledge Graph — [knowledge_graph.py](aihub/knowledge_graph.py)

**Status: 🔴 IN-MEMORY ONLY, BEZ PERSYSTENCJI**

- `KnowledgeGraph` klasa z `self.nodes: Dict`, `self.edges: List` — **zwykły dict w pamięci**
- Brak zapisu do DB — restart = puste
- `stats()` wywoływany z `GET /cognitive/health` ([main.py:460](aihub/main.py#L460))
- `detect_contradictions()` szuka krawędzi typu `"contradicts"` — ale nikt nie dodaje krawędzi do grafu w normalnym flow
- `ConflictDetector` instantiuje `KnowledgeGraph()` ([conflict_detector.py:23](aihub/conflict_detector.py#L23)) ale nie go nie populuje

---

## 5. TABELA STATUSU KOMPONENTÓW

| Komponent                        | Plik(i)                              | Status            | Dowód                                                                          |
| -------------------------------- | ------------------------------------ | ----------------- | ------------------------------------------------------------------------------ |
| STM (Short-Term Memory)          | memory_engine.py, db.py              | ✅ DZIAŁA         | `add_stm()` → `insert_stm_message()` → `stm_messages` table                    |
| L1 Episodic                      | memory_engine.py                     | ✅ DZIAŁA         | `add_episode()` → `upsert_node()` layer=L1                                     |
| L2 Semantic/Fakty                | memory_engine.py                     | ✅ DZIAŁA         | `add_fact()` → `upsert_node()` layer=L2                                        |
| L3 Archive                       | knowledge_evolution.py, memory_gc.py | ✅ DZIAŁA         | `archive_stale()` → layer=L3/L3_archive                                        |
| FTS5 Search                      | db.py                                | ✅ DZIAŁA         | `search_nodes_fts()` z fallback do LIKE                                        |
| TF-IDF Reranking                 | vector_index.py                      | ✅ DZIAŁA         | Pure Python, brak zew. zależności                                              |
| Blend Retrieval                  | memory_engine.py                     | ✅ DZIAŁA         | `0.72*cos + 0.18*imp + 0.10*conf`                                              |
| FAISS Vector Engine              | vector_engine.py                     | ⚠️ WYMAGA DEPS    | `faiss-cpu` + `sentence-transformers`                                          |
| FAISS Search (użycie w pipeline) | —                                    | 🔴 NIEUŻYWANY     | `vector_engine.search()` nigdzie nie wołany z pipeline                         |
| Meta-Memory Tracking             | meta_memory.py                       | ⚠️ BEZ INTEGRACJI | `track_access()` nigdzie nie wołany w retrieve                                 |
| Memory GC                        | memory_gc.py                         | ⚠️ BEZ SCHEDULERA | `collect_garbage()` istnieje ale brak cron/background. `schedule_gc()` = no-op |
| Knowledge Evolution              | knowledge_evolution.py               | ✅ DZIAŁA         | Wołany przez memory_gc.evolve_all()                                            |
| Enforce Caps                     | memory_engine.py                     | ✅ DZIAŁA         | \_enforce_caps() per add_episode/add_fact                                      |
| Sentiment Analysis               | psyche_engine.py                     | ✅ DZIAŁA         | 40 słów PL, formuła sentiment+confidence                                       |
| Mood/Energy/Focus                | psyche_engine.py                     | ✅ DZIAŁA         | evolve() + upsert_psyche()                                                     |
| Trait Learning                   | psyche_engine.py                     | ✅ DZIAŁA         | directness/patience/swearing/sarcasm modyfikowane                              |
| Temperature Adaptation           | psyche_engine.py                     | ✅ DZIAŁA         | 0.55 + 0.25\*(mood-0.5)                                                        |
| Psyche Reflect                   | psyche_engine.py                     | ✅ DZIAŁA         | Topic frequency + mood description                                             |
| Reflection Engine                | reflection_engine.py                 | 🔴 MARTWY         | Nikt nie wywołuje                                                              |
| Psyche → Decisions               | cognitive_controller.py              | ✅ DZIAŁA         | energy→urgency, focus→confidence, energy→limit                                 |
| Attention Controller             | attention_controller.py              | ✅ DZIAŁA         | rank_messages() w agent_loop                                                   |
| Cognitive Controller             | cognitive_controller.py              | ✅ DZIAŁA         | Intent extraction + decide() pipeline                                          |
| Conflict Detector                | conflict_detector.py                 | ✅ DZIAŁA         | Security + logical + resource checks                                           |
| Prediction Engine                | prediction_engine.py                 | ✅ DZIAŁA         | 5 patterns (focus, urgency, energy, pressure, research)                        |
| Autonauka (keyword)              | memory_engine.py                     | ✅ DZIAŁA         | 8 keywords → add_fact()                                                        |
| LearningEngine (regex)           | learning_engine.py                   | 🔴 MARTWY         | CognitiveController instantiuje ale nie woła                                   |
| Research Engine                  | research_engine.py                   | 🔴 PLACEHOLDER    | Returns empty list, no API                                                     |
| Knowledge Graph                  | knowledge_graph.py                   | 🔴 IN-MEMORY      | Brak persystencji, restart = puste                                             |

---

## 6. SPRAWDZENIE KRZYŻOWE — CO ŁĄCZY PODSYSTEMY

### Memory ↔ Psyche

- `/memory/add` ([main.py:192](aihub/main.py#L192)) woła `evolve(user_id, user_msg, "user")` i `evolve(user_id, assistant_msg, "assistant")` **PRZED** `process_turn()` → **Każdy zapis pamięci modyfikuje psychikę**.
- `CognitiveController.decide()` czyta `ensure_user()` dla psyche state → wpływa na limity i confidence.

### Memory ↔ Learning

- `memory_engine.process_turn()` zawiera inline autonauka (8 keywords → `add_fact()`).
- `learning_engine.process_turn()` też woła `memory_engine.add_fact()` — ale jest martwy.

### Psyche ↔ Decisions

- `evolve()` → mood/energy/focus → `cognitive_controller.decide()` czyta te wartości.
- Temperature = `0.55 + 0.25*(mood-0.5)` → powinno wpływać na LLM ale **brak integracji z żadnym LLM API** w audytowanym kodzie.

### Research ↔ Memory

- `research_engine.research()` wywołuje `add_fact()` per extracted fact — ale nigdy nie dostaje danych.

### GC ↔ Evolution ↔ Meta

- `memory_gc.collect_garbage()` → `meta_memory.check_stale()` → `knowledge_evolution.evolve_all()` → `deduplicate()` + `archive_stale()`.
- Pełny chain działa, ale **brak triggera** (scheduler/cron/endpoint).

---

## 7. WERYFIKACJA — SCENARIUSZE TESTOWE

### Scenariusz 1: User pisze "lubię pizzę"

```
1. POST /memory/add → evolve("user", "lubię pizzę", "user") → mood ↑ (pozytywne: "lubię" ∈ _POS)
2. evolve("assistant", response, "assistant") → mood ↑ (weak)
3. process_turn() → remember_turn() → FAISS add (if deps installed)
4. add_stm() x2
5. add_episode("U: lubię pizzę || A: ...")
6. AUTO-FACT: "lubię" ∈ keywords → add_fact("lubię pizzę", tags=["user","preference",intent])
7. Fakt ID = SHA256("L2:user_id:lubię pizzę") → deterministic dedup
```

**Wynik: ✅ Fakt zostaje zapisany. Psychika się zmienia.**

### Scenariusz 2: User pisze "wyszukaj informacje o AI"

```
1. POST /cognitive/decide → intent = "research" (keyword "wyszukaj")
2. _decide_research() → DecisionResult(action_type="research", query="wyszukaj informacje o AI")
3. agent_loop._execute_action("research", ...) → stub: {"topic": "...", "researched": True}
4. research_engine.research() NIE jest wołany
```

**Wynik: 🔴 Research nie działa. Stub zwraca fake response.**

### Scenariusz 3: Restart serwera

```
1. KnowledgeGraph.nodes = {} → PUSTE (in-memory only)
2. LearningEngine.learned_facts = set() → PUSTE (dedup cache resetowany)
3. CognitiveController.states = {} → PUSTE (cognitive state resetowany)
4. PredictionEngine.predictions_cache = {} → PUSTE
5. ReflectionEngine.insights = [] → PUSTE
6. SQLite tables: memory_nodes, stm_messages, psyche_state → ✅ TRWAŁE
```

**Wynik: ⚠️ Wszystko in-memory jest tracone. DB przetrwa.**

### Scenariusz 4: Memory GC

```
1. Brak schedulera → collect_garbage() NIGDY nie jest wywoływany automatycznie
2. Brak endpointu HTTP do ręcznego GC
3. schedule_gc() → logger.info() i nic więcej
```

**Wynik: 🔴 GC nie jest nigdy uruchamiany.**

---

## 8. PODSUMOWANIE KRYTYCZNYCH PROBLEMÓW

| #   | Problem                                                                  | Severity | Plik                    | Linia  |
| --- | ------------------------------------------------------------------------ | -------- | ----------------------- | ------ |
| 1   | **LearningEngine martwy** — instancjonowany ale nieużywany               | 🔴 HIGH  | cognitive_controller.py | L80    |
| 2   | **Research placeholder** — `_generate_placeholder_results()` returns []  | 🔴 HIGH  | research_engine.py      | L228   |
| 3   | **KnowledgeGraph in-memory** — restart = dane stracone                   | 🔴 HIGH  | knowledge_graph.py      | L55-57 |
| 4   | **Memory GC brak schedulera** — nigdy nie uruchamiany automatycznie      | 🟡 MED   | memory_gc.py            | L225   |
| 5   | **meta_memory.track_access() nieużywany** — retrieve nie trackuje access | 🟡 MED   | meta_memory.py          | L72    |
| 6   | **vector_engine.search() nieużywany** — FAISS search dead code           | 🟡 MED   | vector_engine.py        | L155   |
| 7   | **reflection_engine martwy** — nikt nie importuje                        | 🟡 MED   | reflection_engine.py    | L1     |
| 8   | **Autonauka keyword-only** — brak NER, brak kontekstu, 8 słów            | 🟡 MED   | memory_engine.py        | L143   |
| 9   | **Temperature unused** — wyliczana ale brak integracji z LLM             | 🟡 MED   | psyche_engine.py        | L145   |
| 10  | **FAISS deps optional** — crash jeśli brak faiss-cpu                     | 🟡 LOW   | vector_engine.py        | L37    |
