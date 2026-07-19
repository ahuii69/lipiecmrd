"""World-class adaptive runtime: dynamic budget, evidence retrieval, self-eval."""

from __future__ import annotations

import time

from aihub.memory_context_pack import (
    MemoryContextPackItem,
    evidence_score_components,
    select_with_diversity,
)
from aihub.turn.adaptive_runtime import plan_adaptive_runtime, truncate_memory_pack_in_context
from aihub.turn.continuous_self_eval import evaluate_continuous_self
from aihub.turn.prompt_budget import refine_prompt_budget_dynamic, select_prompt_budget
from aihub.turn.turn_signals import compute_turn_signals


def test_dynamic_budget_skips_low_roi_layers():
    base = select_prompt_budget(
        user_text="ok",
        selected_strategy="instant",
        web_decision="off",
    )
    # Force contextual envelope with high confidence / low novelty signals.
    base = select_prompt_budget(
        user_text="Ile to jest 2+2?",
        selected_strategy="contextual",
        web_decision="off",
    )
    signals = compute_turn_signals(
        user_text="Ile to jest 2+2?",
        selected_strategy="contextual",
        strategy_confidence=0.9,
        intent_confidence=0.9,
        ambiguity=0.0,
        memory_hits=0,
        memory_pack_items=0,
        history_len=2,
        budget_profile=base.profile,
    )
    refined = refine_prompt_budget_dynamic(base, signals)
    assert refined.dynamic_refined is True
    assert "BUDGET_DYNAMIC_REFINED" in refined.reason_codes
    assert refined.max_prompt_tokens <= base.max_prompt_tokens
    # Low memory usefulness → memory layers skipped or lean pack.
    assert refined.memory_pack_max_items is not None
    assert refined.memory_pack_max_items <= 4
    assert "execution_handbook" in refined.layers_skipped or "execution_handbook" not in refined.layers_included


def test_dynamic_budget_keeps_memory_on_recall():
    base = select_prompt_budget(
        user_text="Jak nazywa się mój pies?",
        selected_strategy="contextual",
    )
    signals = compute_turn_signals(
        user_text="Jak nazywa się mój pies?",
        selected_strategy="contextual",
        strategy_confidence=0.7,
        memory_pack_items=3,
        budget_profile="contextual",
    )
    refined = refine_prompt_budget_dynamic(base, signals)
    assert "memory" in refined.layers_included or "memory_pack" in refined.layers_included
    assert refined.memory_pack_max_items >= 3


def test_adaptive_runtime_skips_reflection_and_critic_when_confident():
    budget = select_prompt_budget(user_text="Ile to 2+2?", selected_strategy="contextual")
    signals = compute_turn_signals(
        user_text="Ile to 2+2?",
        selected_strategy="contextual",
        strategy_confidence=0.9,
        intent_confidence=0.9,
        ambiguity=0.05,
        memory_hits=0,
        budget_profile="contextual",
    )
    # Amplify confidence path
    signals.confidence = 0.85
    signals.complexity = 0.2
    signals.uncertainty = 0.15
    signals.tool_probability = 0.05
    plan = plan_adaptive_runtime(signals, budget, decision_core={"selected_strategy": "contextual"})
    assert plan.skip_reflection is True
    assert plan.skip_critic is True
    assert plan.memory_pack_max_items <= 3


def test_adaptive_runtime_enables_planner_for_agentic():
    budget = select_prompt_budget(
        user_text="Zaplanuj trzyetapową migrację PostgreSQL",
        selected_strategy="agentic",
    )
    signals = compute_turn_signals(
        user_text="Zaplanuj trzyetapową migrację PostgreSQL",
        selected_strategy="agentic",
        strategy_confidence=0.6,
        budget_profile="agentic",
    )
    plan = plan_adaptive_runtime(
        signals, budget, decision_core={"selected_strategy": "agentic", "planner_recommended": True}
    )
    assert plan.skip_planner is False
    assert plan.planner_max_nodes >= 4


def test_truncate_memory_pack_respects_adaptive_limits():
    ctx = {
        "memory_context_pack_prompt": "PAMIĘĆ\n- [a] one\n- [b] two\n- [c] three\n- [d] four",
        "memory_context_pack": {
            "selected_ids": ["a", "b", "c", "d"],
            "facts": [
                {"id": "a", "content": "one"},
                {"id": "b", "content": "two"},
                {"id": "c", "content": "three"},
                {"id": "d", "content": "four"},
            ],
        },
    }
    budget = select_prompt_budget(user_text="x", selected_strategy="contextual")
    signals = compute_turn_signals(user_text="x", budget_profile="contextual")
    signals.memory_usefulness = 0.1
    signals.expected_token_roi = 0.2
    plan = plan_adaptive_runtime(signals, budget)
    plan.memory_pack_max_items = 2
    plan.memory_pack_max_chars = 80
    out = truncate_memory_pack_in_context(ctx, plan)
    assert out["applied"] is True
    assert len(ctx["memory_context_pack"]["selected_ids"]) <= 2


def test_evidence_scoring_prefers_fresh_high_reliability():
    now = time.time()
    fresh = MemoryContextPackItem(
        id="fresh",
        source="memory_v2",
        memory_type="fact",
        title="Profile26-abc",
        content="Profile26-abc odkurzacz to narzędzie",
        score=0.5,
        confidence=0.9,
        salience=0.7,
        metadata={"updated_ts": now - 3600},
    )
    stale = MemoryContextPackItem(
        id="stale",
        source="graph_stm",
        memory_type="fact",
        title="Profile26-abc",
        content="Profile26-abc odkurzacz to produkt marketingowy",
        score=0.55,
        confidence=0.4,
        salience=0.4,
        metadata={"updated_ts": now - 90 * 86400},
    )
    q = "Co wiemy o Profile26-abc odkurzacz?"
    f1 = evidence_score_components(fresh, query=q, correction_hints="korekta: narzędzie")
    f2 = evidence_score_components(stale, query=q, correction_hints="korekta: narzędzie")
    assert f1["freshness"] > f2["freshness"]
    assert f1["source_reliability"] > f2["source_reliability"]
    assert f1["composite"] > f2["composite"]


def test_diversity_mmr_avoids_near_duplicates():
    items = []
    for i, text in enumerate(
        [
            "User lubi kawę rano",
            "Użytkownik lubi kawę z rana",
            "Preferuje herbatę wieczorem",
        ]
    ):
        items.append(
            (
                MemoryContextPackItem(
                    id=f"m{i}",
                    source="memory_v2",
                    memory_type="preference",
                    title="pref",
                    content=text,
                    score=0.8 - i * 0.01,
                    confidence=0.8,
                    salience=0.7,
                ),
                {"composite": 0.8 - i * 0.01},
            )
        )
    picked = select_with_diversity(items, max_items=2)
    ids = {p[0].id for p in picked}
    assert "m2" in ids  # tea preference should survive diversity
    assert len(picked) == 2


def test_continuous_self_eval_metrics_present():
    ev = evaluate_continuous_self(
        message="Zaplanuj migrację w 3 etapach",
        response_text="1. Backup\n2. Restore\n3. Verify",
        trace={
            "response_grounding_mode": "tools_verified",
            "planner_used": True,
            "planner_tasks_count": 3,
            "budget_profile": "agentic",
            "usage_total_tokens": 800,
            "memory_context_pack_selected_ids": ["x"],
            "reflection_ran": False,
            "post_reflection_skipped": True,
            "strategy_confidence": 0.7,
        },
        decision_core={"strategy_confidence": 0.7, "budget_profile": "agentic"},
        ok=True,
    )
    d = ev.to_dict()
    for key in (
        "hallucination_risk",
        "retrieval_usefulness",
        "memory_usefulness",
        "planner_usefulness",
        "reflection_usefulness",
        "tool_usefulness",
        "token_efficiency",
        "confidence_calibration",
        "answer_completeness",
    ):
        assert key in d
        assert 0.0 <= d[key] <= 1.0
    assert ev.hallucination_risk < 0.4
    assert ev.answer_completeness >= 0.7
