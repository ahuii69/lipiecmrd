"""Practical verification: CSE feedback loop, corpus, retrieval A/B, soak/resilience."""

from __future__ import annotations

import concurrent.futures
import time
import uuid

import pytest

from aihub.adaptive_learning.engine import apply_learning_influences_to_decision, process_turn_learning
from aihub.turn.adaptive_runtime import plan_adaptive_runtime
from aihub.turn.cse_feedback import (
    apply_cse_prior_to_decision,
    apply_cse_prior_to_signals,
    load_cse_prior,
    persist_cse_prior,
)
from aihub.turn.continuous_self_eval import evaluate_continuous_self
from aihub.turn.prompt_budget import refine_prompt_budget_dynamic, select_prompt_budget
from aihub.turn.turn_signals import compute_turn_signals


def test_cse_prior_changes_next_turn_decision(isolated_db):
    """CSE is not trace-only: prior must mutate next-turn decision_core."""
    uid = f"cse-{uuid.uuid4().hex[:8]}"
    cse = evaluate_continuous_self(
        message="Jaka jest aktualna cena BTC?",
        response_text="Wykonuję sprawdzenie rynku… BTC rośnie.",  # ungrounded claim
        trace={
            "response_grounding_mode": "fallback",
            "used_fallback": True,
            "anti_hallucination_clamp_applied": True,
            "budget_profile": "research",
            "strategy_confidence": 0.8,
        },
        decision_core={"strategy_confidence": 0.8, "web_decision": "off"},
        ok=False,
    ).to_dict()
    assert cse["hallucination_risk"] >= 0.55
    # Seed prior with enough samples
    persist_cse_prior(uid, cse)
    persist_cse_prior(uid, cse)
    prior = load_cse_prior(uid)
    assert prior is not None
    assert int(prior.get("samples") or 0) >= 2

    dc = {
        "selected_strategy": "instant",
        "strategy_confidence": 0.8,
        "web_decision": "off",
        "reason_codes": [],
        "session_id": "s1",
    }
    apply_cse_prior_to_decision(dc, prior, message="Jaka jest aktualna cena eth?")
    assert dc.get("cse_force_critic") is True
    assert dc.get("web_decision") == "optional"
    assert dc.get("cse_prior_influenced") is True
    assert any(c.startswith("CSE_PRIOR_") for c in dc["reason_codes"])


def test_cse_prior_via_learning_pipeline_influences_apply(isolated_db):
    """process_turn_learning persists CSE; apply_learning_influences loads it."""
    uid = f"cse-learn-{uuid.uuid4().hex[:8]}"
    trace = {
        "continuous_self_eval": {
            "hallucination_risk": 0.7,
            "retrieval_usefulness": 0.3,
            "memory_usefulness": 0.25,
            "planner_usefulness": 0.5,
            "reflection_usefulness": 0.5,
            "tool_usefulness": 0.4,
            "token_efficiency": 0.3,
            "confidence_calibration": 0.35,
            "answer_completeness": 0.4,
            "overall_quality": 0.35,
            "reason_codes": ["TEST"],
        },
        "response_grounding_mode": "fallback",
        "used_fallback": True,
        "strategy_confidence": 0.75,
        "budget_profile": "contextual",
    }
    process_turn_learning(
        turn_id=str(uuid.uuid4()),
        user_id=uid,
        session_id="s",
        message="aktualna pogoda w Gdańsku",
        response_text="Pada.",
        trace=trace,
        decision_core={"selected_strategy": "contextual", "strategy_confidence": 0.75, "web_decision": "off"},
        ok=True,
    )
    # Second persist to cross sample threshold
    process_turn_learning(
        turn_id=str(uuid.uuid4()),
        user_id=uid,
        session_id="s",
        message="aktualne kursy walut",
        response_text="USD drożeje.",
        trace=trace,
        decision_core={"selected_strategy": "contextual", "strategy_confidence": 0.75, "web_decision": "off"},
        ok=True,
    )
    prior = load_cse_prior(uid)
    assert prior and int(prior["samples"]) >= 2

    dc = {
        "selected_strategy": "instant",
        "strategy_confidence": 0.8,
        "web_decision": "off",
        "reason_codes": [],
        "session_id": "s2",
        "cognitive_ambiguity": 0.1,
    }
    apply_learning_influences_to_decision(
        decision_core=dc,
        user_id=uid,
        message="aktualna cena ropy",
        intent="research",
    )
    assert dc.get("cse_prior") is not None
    assert "CSE_PRIOR_LOADED" in (dc.get("reason_codes") or [])
    # High hall + current-events lexicon → optional web + critic
    assert dc.get("cse_force_critic") is True or dc.get("web_decision") == "optional" or dc.get("cse_lean_budget")


def test_cse_prior_changes_adaptive_signals_and_budget(isolated_db):
    uid = f"cse-sig-{uuid.uuid4().hex[:8]}"
    persist_cse_prior(
        uid,
        {
            "hallucination_risk": 0.2,
            "token_efficiency": 0.25,
            "memory_usefulness": 0.7,
            "confidence_calibration": 0.8,
            "answer_completeness": 0.7,
            "overall_quality": 0.7,
            "reason_codes": [],
        },
    )
    persist_cse_prior(
        uid,
        {
            "hallucination_risk": 0.2,
            "token_efficiency": 0.22,
            "memory_usefulness": 0.75,
            "confidence_calibration": 0.8,
            "answer_completeness": 0.7,
            "overall_quality": 0.7,
            "reason_codes": [],
        },
    )
    prior = load_cse_prior(uid)
    base = select_prompt_budget(user_text="Wyjaśnij TCP handshake", selected_strategy="contextual")
    signals = compute_turn_signals(
        user_text="Wyjaśnij TCP handshake",
        selected_strategy="contextual",
        budget_profile=base.profile,
        strategy_confidence=0.7,
    )
    before_roi = signals.expected_token_roi
    signals = apply_cse_prior_to_signals(signals, prior)
    assert signals.expected_token_roi <= before_roi
    assert any("CSE_SIG" in c for c in signals.reason_codes)
    refined = refine_prompt_budget_dynamic(base, signals)
    assert refined.max_prompt_tokens <= base.max_prompt_tokens


def test_cse_boosts_memory_on_recall_after_weak_memory(isolated_db):
    uid = f"cse-mem-{uuid.uuid4().hex[:8]}"
    for _ in range(2):
        persist_cse_prior(
            uid,
            {
                "hallucination_risk": 0.3,
                "token_efficiency": 0.6,
                "memory_usefulness": 0.2,
                "confidence_calibration": 0.6,
                "answer_completeness": 0.6,
                "overall_quality": 0.5,
                "reason_codes": [],
            },
        )
    prior = load_cse_prior(uid)
    dc = {
        "selected_strategy": "instant",
        "strategy_confidence": 0.7,
        "web_decision": "off",
        "reason_codes": [],
    }
    apply_cse_prior_to_decision(dc, prior, message="Pamiętasz jak nazywa się mój pies?")
    assert dc.get("selected_strategy") == "contextual"
    assert dc.get("cse_boost_memory_pack") is True
    assert dc.get("requires_memory") is True


def test_adaptive_corpus_gates():
    from scripts.eval_adaptive_corpus import evaluate_corpus

    out = evaluate_corpus()
    assert out["n"] >= 25
    assert out["gates"]["profile_accuracy_ge_0.85"], out
    assert out["gates"]["lean_token_save_ge_15"], out
    assert out["gates"]["planner_recall_ge_0.8"], out
    assert out["pass"] is True


def test_retrieval_ab_win_rate():
    from scripts.eval_retrieval_ab import evaluate_retrieval_ab

    out = evaluate_retrieval_ab()
    assert out["n"] >= 5
    assert out["evidence_precision"] >= out["baseline_precision"] - 1e-9
    assert out["evidence_recall"] >= out["baseline_recall"] - 1e-9
    assert out["win_rate"] >= 0.5
    assert out["loss_rate"] == 0
    assert out["evidence_f1"] >= out["baseline_f1"]
    assert out["pass"] is True


def test_soak_adaptive_decision_loop_parallel():
    """Compressed soak: many parallel adaptive decisions remain stable."""
    texts = [
        "kim jesteś?",
        "elo",
        "Jak nazywa się mój pies?",
        "Zaplanuj migrację PG",
        "aktualna pogoda",
        "Ile to 2+2?",
        "Poprawka: lubię herbatę",
        "Sprawdź dokumentację API",
    ]

    def one(i: int) -> dict:
        text = texts[i % len(texts)]
        strat = ["instant", "contextual", "research", "agentic"][i % 4]
        web = "required" if strat == "research" else "off"
        base = select_prompt_budget(user_text=text, selected_strategy=strat, web_decision=web)
        signals = compute_turn_signals(
            user_text=text,
            selected_strategy=strat,
            web_decision=web,
            strategy_confidence=0.5 + (i % 5) * 0.08,
            budget_profile=base.profile,
            memory_pack_items=i % 4,
        )
        refined = refine_prompt_budget_dynamic(base, signals)
        plan = plan_adaptive_runtime(signals, refined, decision_core={"selected_strategy": strat})
        return {
            "profile": refined.profile,
            "dyn": refined.dynamic_refined,
            "skip_r": plan.skip_reflection,
            "tokens": refined.max_prompt_tokens,
        }

    n = 240
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(one, range(n)))
    elapsed = time.perf_counter() - t0
    assert len(results) == n
    assert all(r["dyn"] or r["profile"] in ("meta_light", "casual_light") for r in results)
    # Throughput gate: compressed soak should finish quickly locally.
    assert elapsed < 30.0
    # Distribution sanity: not all profiles identical
    profiles = {r["profile"] for r in results}
    assert len(profiles) >= 3


def test_partial_failure_resilience_web_and_memory(isolated_db, monkeypatch):
    """Partial outages must not crash adaptive/CSE paths."""
    # Simulate Brave/web billing failure path already handled elsewhere — ensure CSE still works.
    ev = evaluate_continuous_self(
        message="Sprawdź aktualne newsy",
        response_text="Nie udało się pobrać źródeł.",
        trace={
            "controlled_web_triggered": True,
            "controlled_web_ok": False,
            "response_grounding_mode": "fallback",
            "used_fallback": True,
            "budget_profile": "research",
            "strategy_confidence": 0.6,
        },
        decision_core={"web_decision": "required", "strategy_confidence": 0.6},
        ok=True,
    )
    assert ev.retrieval_usefulness <= 0.45
    assert 0.0 <= ev.hallucination_risk <= 1.0

    # Memory pack build failure should be swallowable by callers; scoring still works offline.
    from aihub.memory_context_pack import MemoryContextPackItem, evidence_score_components

    item = MemoryContextPackItem(
        id="x",
        source="memory_v2",
        memory_type="fact",
        title="t",
        content="c",
        score=0.5,
        confidence=0.5,
        salience=0.5,
        metadata={},
    )
    feats = evidence_score_components(item, query="q", correction_hints="")
    assert "composite" in feats

    # Provider-ish failure: learning still persists CSE prior.
    uid = f"resilience-{uuid.uuid4().hex[:8]}"
    process_turn_learning(
        turn_id=str(uuid.uuid4()),
        user_id=uid,
        session_id="s",
        message="test",
        response_text="ok",
        trace={
            "continuous_self_eval": ev.to_dict(),
            "used_fallback": True,
            "provider_failover_happened": True,
            "strategy_confidence": 0.5,
        },
        decision_core={"selected_strategy": "contextual", "strategy_confidence": 0.5},
        ok=True,
    )
    assert load_cse_prior(uid) is not None


@pytest.mark.parametrize("workers", [4, 8])
def test_concurrent_cse_persist_stable(isolated_db, workers):
    uid = f"cse-conc-{uuid.uuid4().hex[:8]}"

    def write(i: int) -> None:
        persist_cse_prior(
            uid,
            {
                "hallucination_risk": 0.3 + (i % 5) * 0.05,
                "token_efficiency": 0.5,
                "memory_usefulness": 0.5,
                "confidence_calibration": 0.5,
                "answer_completeness": 0.5,
                "overall_quality": 0.5,
                "reason_codes": [f"w{i}"],
            },
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(write, range(40)))
    prior = load_cse_prior(uid)
    assert prior is not None
    assert int(prior["samples"]) >= 1
    assert 0.0 <= float(prior["hallucination_risk"]) <= 1.0
