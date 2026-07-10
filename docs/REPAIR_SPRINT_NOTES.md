# AI-Hub — Production Repair Sprint: SPIĘCIE Memory + Psyche + Autonauka + Research

**Data:** 2025-01
**Baseline:** 36 testów ✅
**Po sprincie:** 49 testów ✅ (36 original + 13 new)

---

## FAZA 0 — Baseline

- Zweryfikowano 36 testów regresji (`test_p2p8_regression.py` + `test_memory_facts_risk.py`)
- Audyt plików: `meta_memory.py`, `memory_engine.py`, `learning_engine.py`, `research_engine.py`, `agent_engine.py`, `psyche_engine.py`, `memory_gc.py`, `cognitive_controller.py`

## FAZA 1 — MetaMemory: touch on retrieve

**Plik:** `aihub/meta_memory.py`, `aihub/memory_engine.py`

- Dodano `touch_nodes(node_ids: List[str]) -> int` — batch INSERT…ON CONFLICT UPDATE na `memory_meta`
- Zwiększa `access_count`, odświeża `last_access`, `usage_score+0.03`, `freshness_score+0.05`
- Wired into `retrieve_context()` — po `_pack()` episodic+semantic, przed `append_event`
- Obsługa błędów: `except (OSError, ImportError)` — nie crash'uje retrieve

**Testy:** `TestMetaMemoryTouch` (3 testy)

## FAZA 2 — Aktywacja LearningEngine

**Pliki:** `aihub/memory_engine.py`, `aihub/psyche_engine.py`, `aihub/cognitive_controller.py`

- `process_turn()` teraz wywołuje `LearningEngine.extract_facts_from_message()` po `add_episode()`
- Keyword fallback działa tylko gdy LearningEngine nie wyekstraktował żadnych faktów (dedup)
- `reflect()` wywołuje `learn_from_reflection()` po zbudowaniu reflection dict
- Usunięto martwą instancję `LearningEngine` z `cognitive_controller.py`

**Testy:** `TestLearningEngine` (4 testy)

## FAZA 3 — Prawdziwy ResearchEngine

**Plik:** `aihub/research_engine.py`

- Usunięto `_generate_placeholder_results()` (zwracał `[]`)
- Dodano `_fetch_wikipedia(query)` — Wikipedia REST API (opensearch + extract)
- Dodano `_fetch_duckduckgo(query)` — DuckDuckGo instant answer API (zero API keys)
- Oba backendy: httpx z `HTTP_TIMEOUT_S`, limity `HTTP_MAX_BYTES`
- Graceful degradation: jeśli oba backendy fail'ują, wynik ok=True z 0 results

**Testy:** `TestResearchEngine` (3 testy)

## FAZA 4 — Agent: research.query

**Plik:** `aihub/agent_engine.py`

- `plan_from_text()` — rozpoznaje: "wyszukaj", "research", "znajdź info", "zbadaj", "sprawdź temat"
- `execute_task()` — nowy handler `research.query` → wywołuje `ResearchEngine.research()`
- Dodano `_execute_research(user_id, payload)` — async, loguje wyniki do event_log

**Testy:** `TestAgentResearch` (2 testy)

## FAZA 5 — GC trigger

**Plik:** `aihub/agent_engine.py`

- Dodano `_maybe_gc(user_id)` — liczy memory_nodes/LTM_MAX_FACTS_PER_USER
- Jeśli pressure > 0.7 → wywołuje `collect_garbage(user_id)` z `memory_gc`
- Wywoływany z `agent_tick()` po wykonaniu tasków

**Testy:** `TestGCTrigger` (1 test)

---

## Podsumowanie zmian

| Moduł                         | Status przed              | Status po                            |
| ----------------------------- | ------------------------- | ------------------------------------ |
| `meta_memory.touch_nodes`     | nie istniał               | ✅ wired into retrieve               |
| `learning_engine`             | dead code (niewywoływany) | ✅ wired into process_turn + reflect |
| `research_engine`             | placeholder (return [])   | ✅ Wikipedia + DuckDuckGo            |
| `agent_engine.research.query` | nie istniał               | ✅ plan + execute                    |
| `memory_gc`                   | dead code (niewywoływany) | ✅ wired via \_maybe_gc              |

## Test suite

```
tests/test_p2p8_regression.py     — 26 testów ✅
tests/test_memory_facts_risk.py   — 10 testów ✅
tests/test_repair_sprint.py       — 13 testów ✅
                           RAZEM:   49 testów ✅
```

---

## SAFE CLEANUP SPRINT v3.1 — 2026-03-06

### Co przeniesiono do `aihub/_dead/`

**Standalone dead .py (15):**
agent_memory, agent_planner, agent_tools, agent_runtime_patch, executor_engine,
goals_engine, reflection_engine, context_builder, procedures, prompt, prompts,
tools, planner, run.sh, DEPRECATED.md

**Dead subdirs (10):**
\_legacy_api/, routers/, middleware/, psyche/, memory/, web/, fs/, sse/, util/, workers/

**Dead core/ files (4):** config.py, background.py, logging.py, openapi.py
(KEEP: core/security.py + core/**init**.py — runtime dep via auth_patch.py)

**Dead services/ files (3):** events.py, memory*intel.py, predictor.py
(KEEP: services/self_rewriter.py — utility for bin/snap*\*.py)

**Top-level agent/ (1 dir):** config.py, db.py, helpers.py, prompt.py, psyche.py

**Artefakty (3):** self_rewriter.py.orig/.rej/.patch

**Razem: 36 elementów przeniesionych**

### Dodatkowe zmiany

- `aihub/__init__.py`: usunięto `"tools"` i `"planner"` z `__all__`
- `aihub/services/__init__.py`: odtworzony (potrzebny dla bin/snap\_\*.py)

### Wynik gate

```
GATE 1: from aihub.main import app → routes: 32, exit 0
GATE 2: pytest -q tests/ → 92 passed in 71s, exit 0
```
