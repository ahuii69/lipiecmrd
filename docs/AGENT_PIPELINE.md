# AGENT PIPELINE — Autonomous Task Graph Runtime

Ten dokument opisuje aktualny pipeline wykonania agenta po sprincie autonomicznym.

## Przepływ główny

```text
message
  ↓
memory retrieval
  ↓
planner
  ↓
task graph
  ↓
reasoning loop
  ↓
executor
  ↓
memory + knowledge graph update
```

## Moduły i odpowiedzialności

- `aihub/planner_engine.py` — generuje plan jako `TaskGraph` (`reason`, `memory_query`, `research`, `learn`, `action`), korzysta z wiadomości + kontekstu pamięci + kontekstu KG i gwarantuje niepusty graph.
- `aihub/task_graph.py` — przechowuje `TaskNode` i zależności oraz udostępnia: `add_task`, `next_ready_task`, `mark_complete`, `has_pending`, `serialize`, `deserialize`.
- `aihub/reasoning_engine.py` — wykonuje pętlę tasków pending z limitami bezpieczeństwa (`max_steps`, `timeout_seconds`) i może generować follow-up taski.
- `aihub/agent_executor.py` — wykonuje task types: `query`, `learn`, `research`, `action`, `reason` oraz mapuje aliasy kompatybilności (`memory_query`/`memory_search` → `query`, `execute` → `action`).

## Integracja z API

- `POST /agent/run` (`aihub/agent_api.py`) uruchamia `run_agent(...)` z `agent_runner.py`
- `agent_runner`:
    1. pobiera memory context (`retrieve_context`),
    2. pobiera knowledge context (`query_nodes`),
    3. uruchamia `run_reasoning_loop(...)`,
    4. zwraca wynik reasoning + user-scoped vector search.

## Integracja pamięci i wiedzy

- `memory_engine.add_episode()` i `memory_engine.add_fact()` karmią KG (`add_node` + relacje)
- KG persystuje do SQLite (`knowledge_nodes`, `knowledge_edges`)
- KG ładuje się przy starcie aplikacji (`main.py` startup -> `load_from_db()`)

## Skalowanie

- research backendy równolegle (`asyncio.gather`)
- deduplikacja wiedzy przez ANN clustering + FAISS nearest neighbors
- vector search z izolacją danych po `user_id`
