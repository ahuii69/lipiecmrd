# AI-Hub — Plan naprawy: MEMORY + PSYCHIKA + AUTONAUKA + RESEARCH

> **Zakres:** Tylko 4 podsystemy. Priorytet: krytyczne → ważne → nice-to-have.
> **Źródło:** Audyt z `docs/AUDYT_MEMORY_PSYCHIKA_RESEARCH.md`

---

## FAZA 1: Ożywienie martwego kodu (KRYTYCZNE)

### 1.1 Podłączyć LearningEngine do pipeline

**Problem:** `LearningEngine` z 6 regułami regex istnieje w `learning_engine.py` ale nikt go nie wywołuje. `CognitiveController` go instanciuje ([cognitive_controller.py:80](aihub/cognitive_controller.py#L80)) ale nie woła.

**Akcja:**

1. W `main.py` endpoint `POST /memory/add` — po `process_turn()` dodać wywołanie `learning_engine.process_turn(user_id, user_msg, assistant_msg, intent, meta)`.
2. LUB: W `memory_engine.process_turn()` — zastąpić prosty keyword matching wywołaniem `learning_engine.extract_facts_from_message()`.
3. **Rekomendacja:** Opcja 2 — zastąpić keyword matching, nie duplikować logiki.

**Pliki do zmiany:**

- [aihub/memory_engine.py](aihub/memory_engine.py#L130) — `process_turn()` linie 143-155

**Effort:** Mały (5-10 linii kodu)

---

### 1.2 Research Engine — podłączyć prawdziwe API

**Problem:** `_generate_placeholder_results()` zwraca `[]` ([research_engine.py:228](aihub/research_engine.py#L228)). Brak endpointu HTTP. Agent loop stub.

**Akcja:**

1. Dodać konfigurację search API w `config.py` (np. `SEARCH_API_KEY`, `SEARCH_API_URL`).
2. Zamienić `_generate_placeholder_results()` na rzeczywiste wywołanie HTTP do SerpAPI / Brave Search / Tavily.
3. Dodać endpoint `POST /research` w `main.py`.
4. W `agent_loop._execute_action()` ([agent_loop.py:145](aihub/agent_loop.py#L145)) — zamienić stub na import i wywołanie `research_engine.research()`.

**Pliki do zmiany:**

- [aihub/config.py](aihub/config.py) — dodać stałe search API
- [aihub/research_engine.py](aihub/research_engine.py#L228) — `_generate_placeholder_results()` → real API call
- [aihub/main.py](aihub/main.py) — dodać endpoint `/research`
- [aihub/agent_loop.py](aihub/agent_loop.py#L145) — podłączyć `research_engine.research()`

**Effort:** Średni (wymaga wyboru API + klucz + error handling)

---

### 1.3 Knowledge Graph — persystencja do SQLite

**Problem:** `KnowledgeGraph` trzyma nodes/edges w `dict` singletona ([knowledge_graph.py:55-57](aihub/knowledge_graph.py#L55)). Restart = puste.

**Akcja:**

1. Dodać tabele `knowledge_nodes` i `knowledge_edges` w `db.py:init_db()`.
2. `add_node()` / `add_edge()` → `INSERT OR REPLACE` do SQLite.
3. `__init__()` → załaduj z DB.
4. Albo: jeśli graf nie jest krytyczny — oznaczyć jako deprecated i usunąć z cognitive pipeline.

**Pliki do zmiany:**

- [aihub/db.py](aihub/db.py#L30) — dodać tabele
- [aihub/knowledge_graph.py](aihub/knowledge_graph.py#L50) — persist do DB

**Effort:** Średni

---

## FAZA 2: Integracja brakujących powiązań (WAŻNE)

### 2.1 Meta-memory tracking w retrieve_context()

**Problem:** `meta_memory.track_access()` ([meta_memory.py:72](aihub/meta_memory.py#L72)) nigdzie nie jest wołany. Pipeline odczytu nie trackuje co jest przydatne.

**Akcja:**
W `memory_engine.retrieve_context()` ([memory_engine.py:178](aihub/memory_engine.py#L178)) — po blend+sort, dla każdego zwróconego wyniku wywołać `meta_memory.track_access(result["id"])`.

**Pliki do zmiany:**

- [aihub/memory_engine.py](aihub/memory_engine.py#L178) — dodać `track_access()` w retrieve

**Effort:** Mały (3-5 linii)

---

### 2.2 Memory GC — scheduler / endpoint

**Problem:** `collect_garbage()` istnieje ale brak triggera ([memory_gc.py:225](aihub/memory_gc.py#L225)). `schedule_gc()` to no-op.

**Akcja:**

1. **Opcja A (prosta):** Dodać endpoint `POST /admin/gc/{user_id}` w `main.py` → `collect_garbage(user_id)`.
2. **Opcja B (automatyczna):** Background task z `asyncio.create_task()` uruchamiany w `@app.on_event("startup")` — co X godzin → `collect_garbage()` dla aktywnych userów.
3. **Rekomendacja:** Opcja A + B.

**Pliki do zmiany:**

- [aihub/main.py](aihub/main.py) — endpoint + startup task
- [aihub/memory_gc.py](aihub/memory_gc.py#L225) — implementować `schedule_gc()` z real asyncio loop

**Effort:** Mały-Średni

---

### 2.3 Reflection Engine — podłączyć do pipeline

**Problem:** `reflection_engine.py` ma pełną implementację ale nikt go nie importuje.

**Akcja:**

1. Opcja A: W `POST /psyche/reflect` — oprócz `psyche_engine.reflect()` wywołać też `reflection_engine.reflect()` → przekazać insights do `learning_engine.learn_from_reflection()`.
2. Opcja B: Zintegrować `reflection_engine` z `agent_loop.agent_cycle()` jako krok po decyzji.

**Pliki do zmiany:**

- [aihub/main.py](aihub/main.py#L174) — endpoint `/psyche/reflect`

**Effort:** Mały

---

### 2.4 FAISS search w retrieve_context()

**Problem:** `vector_engine.search()` istnieje ([vector_engine.py:155](aihub/vector_engine.py#L155)) ale nie jest wołany w pipeline odczytu. Tylko `add_memory()` jest wołany (przez vector_hook).

**Akcja:**
W `retrieve_context()` — dodać krok FAISS search. Jeśli FAISS jest dostępny, dorzucić wyniki do kandydatów przed TF-IDF reranking. Graceful fallback jeśli faiss-cpu nie zainstalowany.

**Pliki do zmiany:**

- [aihub/memory_engine.py](aihub/memory_engine.py#L178) — dodać `vector_engine.search()` z try/except

**Effort:** Mały (10 linii + error handling)

---

### 2.5 Temperature → LLM integration

**Problem:** `psyche_engine.evolve()` wylicza `temperature = 0.55+0.25*(mood-0.5)` ale nigdzie nie jest wysyłana do LLM API.

**Akcja:**
Wszędzie gdzie jest wywołanie LLM (OpenAI/Anthropic API) — czytać `psyche_state.temperature` i przekazywać jako parametr `temperature` w API call.

**Pliki do zmiany:** Zależy od lokalizacji LLM call (prawdopodobnie `agent_engine.py` lub `agent_runner.py`).

**Effort:** Mały (1 linia per call site)

---

## FAZA 3: Ulepszenia jakości (NICE-TO-HAVE)

### 3.1 Ulepszyć autonauka keywords → NER/spaCy

**Problem:** Obecne 8 keyword substring match ([memory_engine.py:143](aihub/memory_engine.py#L143)) jest prymitywne. Cały user_msg jest zapisywany jako fakt.

**Akcja:**

1. Zastąpić keyword matching wywołaniem `LearningEngine.extract_facts_from_message()` (6 regex rules z capture groups).
2. Długoterminowo: dodać spaCy NER dla polskiego (model `pl_core_news_sm`) — ekstrakcja osób, miejsc, organizacji.
3. Dodać kontekstową walidację — nie zapisuj faktu jeśli to pytanie ("Czy lubisz pizzę?" nie powinno stać się faktem).

**Effort:** Średni-Duży

---

### 3.2 Rozszerzyć sentiment analysis

**Problem:** 40 polskich słów w 3 listach ([psyche_engine.py:9-42](aihub/psyche_engine.py#L9)). Brak lemmatyzacji — "lubię" matchuje ale "lubiłem" nie.

**Akcja:**

1. Dodać stemming/lemmatyzację (np. `stempel` dla PL).
2. Rozszerzyć słowniki o 100+ słów.
3. Dodać bigramy: "nie lubię" powinno być negatywne (nie osobno "nie" + "lubię").
4. Rozważyć model ML zamiast keyword matching.

**Effort:** Średni

---

### 3.3 Blend weights tuning

**Problem:** Stałe wagi `0.72*cosine + 0.18*importance + 0.10*confidence` ([memory_engine.py:178](aihub/memory_engine.py#L178)). Brak A/B testing.

**Akcja:**

1. Przenieść wagi do `config.py` jako zmienne env.
2. Dodać endpoint `/admin/blend-weights` do dynamicznej zmiany.
3. Dodać logging retrieval quality → A/B testing offline.

**Effort:** Mały

---

### 3.4 In-memory state persistence

**Problem:** Restart traci: `KnowledgeGraph.nodes`, `LearningEngine.learned_facts`, `CognitiveController.states`, `PredictionEngine.predictions_cache`, `ReflectionEngine.insights`.

**Akcja:**

1. Dla krytycznych: persystencja do SQLite (KnowledgeGraph — patrz 1.3).
2. Dla pomocniczych: `learned_facts` → sprawdzać istnienie w DB zamiast set (już implementowane przez SHA256 dedup w `add_fact`).
3. `CognitiveController.states` → opcjonalnie RAM cache, bo reload z psyche_state wystarczy.

**Effort:** Zależy od komponentu

---

## PODSUMOWANIE PRIORYTETÓW

| #   | Akcja                       | Priorytet    | Effort      | Wpływ                                      |
| --- | --------------------------- | ------------ | ----------- | ------------------------------------------ |
| 1.1 | Podłączyć LearningEngine    | 🔴 KRYTYCZNY | Mały        | Uruchomi 6 regex rules → lepsza ekstrakcja |
| 1.2 | Research Engine → real API  | 🔴 KRYTYCZNY | Średni      | Odblokuje cały research subsystem          |
| 1.3 | KnowledgeGraph → SQLite     | 🔴 KRYTYCZNY | Średni      | Persist grafu wiedzy                       |
| 2.1 | Meta-memory tracking        | 🟡 WAŻNE     | Mały        | GC będzie wiedział co jest przydatne       |
| 2.2 | GC scheduler/endpoint       | 🟡 WAŻNE     | Mały        | Automatyczne czyszczenie pamięci           |
| 2.3 | Reflection Engine podłączyć | 🟡 WAŻNE     | Mały        | Insights → learning → lepsze fakty         |
| 2.4 | FAISS search w retrieve     | 🟡 WAŻNE     | Mały        | Semantic search obok FTS                   |
| 2.5 | Temperature → LLM           | 🟡 WAŻNE     | Mały        | Psychika wpływa na styl odpowiedzi         |
| 3.1 | NER zamiast keywords        | 🟢 NICE      | Średni-Duży | Lepsza ekstrakcja faktów                   |
| 3.2 | Rozszerzyć sentiment        | 🟢 NICE      | Średni      | Precyzyjniejsza psychika                   |
| 3.3 | Blend weights config        | 🟢 NICE      | Mały        | Tuning retrieval                           |
| 3.4 | In-memory persistence       | 🟢 NICE      | Zależy      | Odporność na restart                       |

---

## KOLEJNOŚĆ IMPLEMENTACJI (rekomendowana)

```
Sprint 1 (szybkie wygrane):
  ├── 1.1  Podłączyć LearningEngine (5-10 linii)
  ├── 2.1  track_access() w retrieve (3-5 linii)
  ├── 2.2a Endpoint POST /admin/gc (10 linii)
  └── 2.3  Reflection → pipeline (5 linii)

Sprint 2 (integracja):
  ├── 2.4  FAISS search w retrieve (10 linii + try/except)
  ├── 2.5  Temperature → LLM call (1 linia per site)
  └── 2.2b Background GC scheduler (asyncio task)

Sprint 3 (research):
  ├── 1.2  Research API (config + HTTP client + endpoint)
  └── 1.3  KnowledgeGraph → SQLite

Sprint 4 (jakość):
  ├── 3.1  NER/spaCy zamiast keywords
  ├── 3.2  Rozszerzyć sentiment
  └── 3.3  Blend weights config
```
