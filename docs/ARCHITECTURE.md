# AI-Hub Architecture (Autonomous Agent Sprint)

## Core goals of this architecture

- Keep **FastAPI public endpoints unchanged**.
- Add autonomous planning with `PlannerEngine` + `TaskGraph` + `ReasoningEngine`.
- Unify execution through `AgentExecutor` (including `reason` tasks).
- Persist and reload Knowledge Graph at startup.
- Isolate vector retrieval by `user_id`.
- Prepare DB layer for future PostgreSQL migration (without changing runtime backend now).

## Runtime layers

### 1) API Layer

`aihub/main.py` + routers in `agent_api.py`, `admin_api.py`.

Responsibilities:

- endpoint exposure,
- startup/shutdown lifecycle,
- invoking memory/cognitive/agent subsystems.

### 2) Cognitive Orchestration Layer

`cognitive_controller.py`, `agent_loop.py`, `planner_engine.py`, `reasoning_engine.py`, `conflict_detector.py`, `attention_controller.py`.

Responsibilities:

- intent recognition,
- memory and knowledge-aware planning,
- dependency-aware task graph construction,
- iterative reasoning loop with safety limits (`max_steps`, timeout),
- resource/conflict checks,
- final action dispatch through `AgentExecutor`.

### 3) Execution Layer

`agent_executor.py` + background task executors in `agent_engine.py`.

Responsibilities:

- map action types (`query`, `learn`, `research`, `action`, `reason`) to actual subsystems,
- execute tools (`web_fetch`, `fs_write`, `snapshot`),
- keep execution result contract (`ok`, `action`, `result`).

### 3.1) Planning & Reasoning Runtime

`task_graph.py`, `planner_engine.py`, `reasoning_engine.py`, `agent_runner.py`.

Responsibilities:

- build graph of tasks with explicit dependencies,
- schedule next ready task by priority and dependency completion,
- execute loop with runtime follow-up generation,
- stop on `max_steps` or timeout,
- return full execution trace and serialized graph state.

### 4) Memory + Knowledge Layer

`memory_engine.py`, `meta_memory.py`, `memory_gc.py`, `knowledge_graph.py`, `knowledge_evolution.py`.

Responsibilities:

- STM/LTM storage and retrieval,
- memory ranking/touch/gc,
- knowledge graph feed and persistence,
- deduplication and archive cycles.

### 5) External Enrichment Layer

`research_engine.py`, `web_tools.py`, `vector_engine.py`.

Responsibilities:

- research from multiple backends,
- vector memory indexing/search,
- user-isolated semantic retrieval.

## Updated pipeline

```text
Message
  ↓
Memory retrieval
  ↓
Planner
  ↓
Task graph
  ↓
Reasoning loop
  ↓
Executor
  ↓
Memory + KnowledgeGraph update (persisted)
```

## Autonomous execution entrypoint

`POST /agent/run` uses `agent_runner.run_agent(...)`, which now:

1. retrieves memory context,
2. reads knowledge context,
3. builds a task graph via planner,
4. runs reasoning loop,
5. executes tasks through `AgentExecutor`,
6. returns graph + execution trace + user-scoped vector hits.

## Scalability-oriented changes

- Parallel research backend fan-out via `asyncio.gather`.
- ANN-based clustering path in `knowledge_evolution.deduplicate`.
- `DBAdapter` + `SQLiteAdapter` — jedyny wspierany backend; PostgreSQL nie jest częścią tego repozytorium.
- Strict vector filtering by `user_id` in retrieval path.
- Task graph scheduling avoids repeated O(n²) dependency scans in loop logic.

## Compatibility constraints

- No public FastAPI endpoint paths changed.
- Runtime DB backend remains SQLite.
- Existing memory and event contracts preserved.
