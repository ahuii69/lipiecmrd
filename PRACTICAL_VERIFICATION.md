# PRACTICAL VERIFICATION — Adaptive Intelligence

**Data:** 2026-07-19  
**Cel:** zweryfikować praktycznie (nie deklaratywnie) cztery obszary z feedbacku.

---

## 1. Adaptive Intelligence Layer — corpus

**Harness:** `scripts/eval_adaptive_corpus.py` (30 zróżnicowanych tur)  
**Test:** `tests/test_practical_verification_29.py::test_adaptive_corpus_gates`

| Metryka | Wynik | Gate |
|---------|-------|------|
| Profile accuracy | **96.7%** | ≥ 85% |
| Token save (lean vs full contextual envelope) | **57.2%** | ≥ 15% |
| Planner enable recall | **100%** | ≥ 80% |

Oszczędności liczone względem envelope `contextual` dla profili light (realny koszt vs „pełny stack”), oraz względem static cap po `refine_prompt_budget_dynamic` dla cięższych tur.

---

## 2. Evidence-driven retrieval — A/B

**Harness:** `scripts/eval_retrieval_ab.py`  
**Test:** `test_retrieval_ab_win_rate`

| Metryka | Baseline | Evidence |
|---------|----------|----------|
| Precision | 0.583 | **1.000** |
| F1 | 0.583 | **1.000** |
| Win-rate (per case) | — | **0.50** (reszta tie) |
| Losses | — | **0** |

Przy okazji ewaluacji wzmocniono scoring: `weighted_base` × reliability × confidence oraz kara za low-confidence STM — inaczej hałaśliwy STM z wysokim raw score wygrywał mimo słabej wiarygodności.

---

## 3. Continuous Self-Evaluation — wpływ na kolejne decyzje

**Nie tylko trace.** Pętla:

1. Turn N → `evaluate_continuous_self` → trace  
2. `process_turn_learning` → `persist_cse_prior` (EMA w `event_log`)  
3. Turn N+1 → `apply_learning_influences_to_decision` → `apply_cse_prior_to_decision`  
4. Pipeline → `apply_cse_prior_to_signals` → dynamic budget / adaptive plan overrides (`cse_force_critic`, `cse_lean_budget`, `cse_boost_memory_pack`, …)

**Testy behawioralne:**
- `test_cse_prior_changes_next_turn_decision` — high hallucination → `cse_force_critic` + `web_decision=optional`
- `test_cse_prior_via_learning_pipeline_influences_apply` — pełny learning path
- `test_cse_prior_changes_adaptive_signals_and_budget` — ROI/latency shrink
- `test_cse_boosts_memory_on_recall_after_weak_memory` — instant→contextual + memory boost

---

## 4. Soak / load / partial failure

| Harness | Wynik |
|---------|-------|
| `test_soak_adaptive_decision_loop_parallel` (240 iter, 16 workers) | PASS |
| `test_concurrent_cse_persist_stable` | PASS |
| `test_partial_failure_resilience_web_and_memory` | PASS |
| `scripts/soak_adaptive_runtime.py` (`AIHUB_SOAK_MINUTES=0.2`) | **PASS** — 30768 iter, 0 errors, ~2564/s, p50 0.3 ms |

Długi soak wielogodzinny:  
`AIHUB_SOAK_MINUTES=60 AIHUB_SOAK_WORKERS=12 python scripts/soak_adaptive_runtime.py`

Full pytest po zmianach: **1204 passed, 1 skipped**.

To jest soak warstwy adaptive/CSE (bez live LLM/provider billing). Odporność na awarie zewnętrzne pokrywają też istniejące testy failover (402 → reserve) + nowy partial-failure case (web miss → CSE nadal działa).

---

## 5. Pliki

- `aihub/turn/cse_feedback.py` — prior load/persist/apply  
- `aihub/adaptive_learning/engine.py` — CSE w learning + influences  
- `aihub/turn/mixins/pipeline.py` — signals ← prior, adaptive overrides  
- `aihub/memory_context_pack.py` — reliability-weighted evidence score  
- `scripts/eval_adaptive_corpus.py`  
- `scripts/eval_retrieval_ab.py`  
- `scripts/soak_adaptive_runtime.py`  
- `tests/test_practical_verification_29.py`

---

## Werdykt

**PRACTICAL VERIFICATION PASS** — routing/koszt zmierzone na corpusie, retrieval ma win-rate vs baseline, CSE zmienia zachowanie kolejnej tury (testy), soak/resilience pokryte harnessami.
