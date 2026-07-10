#!/usr/bin/env python3
"""Long-horizon memory, psyche stabilization, and self-consistency (active stack)."""

import time

import pytest

from aihub.db import now_ts
from aihub.memory_psyche_contracts import MemoryV2RuntimeContext, PsycheV2BehaviorContext
from aihub.memory_v2_models import MemoryV2Item
from aihub.memory_v2_repository import insert_memory_item
from aihub.memory_v2_scoring import (
    apply_stability_evaluation,
    calculate_outcome_reinforcement,
    effective_procedure_confidence,
    evaluate_stability_tier_after_update,
    initial_stability_tier_for_new_item,
    is_runtime_actionable_contradiction,
    is_transient_contradiction_item,
)
from aihub.runtime_psyche_bridge import evaluate_self_consistency
from aihub.psyche_v2_adaptation import apply_mode_hysteresis, smooth_pressure_value
from aihub.psyche_v2_models import PsycheV2Profile, PsycheV2State
from aihub.psyche_v2_relations import apply_relation_outcome_long_horizon
from aihub.psyche_v2_service import PsycheV2Service
from aihub.runtime_memory_bridge import build_memory_v2_runtime_snapshot


def _item(
    *,
    tier: str = "transient",
    rc: int = 0,
    succ: int = 0,
    fail: int = 0,
    contradiction: str = "none",
    created_offset: float = 0.0,
) -> MemoryV2Item:
    ts = now_ts() - created_offset
    return MemoryV2Item(
        id=f"m-{rc}-{succ}-{tier}",
        user_id="u-lh",
        memory_type="fact",
        scope="user",
        source_kind="chat_turn",
        title="t",
        content="c",
        summary="s",
        salience_score=0.5,
        reinforcement_count=rc,
        success_reinforcements=succ,
        failure_reinforcements=fail,
        contradiction_state=contradiction,
        stability_tier=tier,
        created_ts=ts,
        updated_ts=ts,
    )


def test_stability_tier_not_promoted_on_single_reinforcement():
    it = _item(rc=1, succ=1, fail=0, tier="transient")
    assert evaluate_stability_tier_after_update(it) == "transient"


def test_stability_promotes_to_developing_after_repeated_success():
    it = _item(rc=3, succ=2, fail=0, tier="transient")
    assert evaluate_stability_tier_after_update(it) == "developing"


def test_transient_contradiction_not_actionable_for_runtime_guard():
    suspected = _item(rc=2, succ=1, fail=0, tier="transient", contradiction="suspected")
    assert is_transient_contradiction_item(suspected) is True
    assert is_runtime_actionable_contradiction(suspected) is False


def test_stable_conflicted_is_actionable():
    conf = _item(rc=5, succ=3, fail=1, tier="stable", contradiction="conflicted")
    assert is_runtime_actionable_contradiction(conf) is True


def test_apply_stability_evaluation_persists_tier_change():
    it = _item(rc=3, succ=2, fail=0, tier="transient")
    out = apply_stability_evaluation(it)
    assert out.stability_tier == "developing"


def test_procedure_confidence_capped_for_tiny_sample():
    raw = effective_procedure_confidence(0.9, evidence_count=1, success_count=1, failure_count=0)
    assert raw <= 0.36


def test_smooth_pressure_does_not_snap_to_spike():
    prev = 0.2
    spiked = 0.95
    sm = smooth_pressure_value(prev, spiked, alpha=0.32)
    assert 0.2 < sm < spiked


def test_mode_hysteresis_requires_three_agreeing_ticks():
    base = PsycheV2State(user_id="u", current_mode="neutral", updated_ts=time.time())
    s1 = apply_mode_hysteresis(base, "cautious")
    assert s1.current_mode == "neutral"
    assert s1.pending_mode == "cautious"
    assert s1.mode_streak == 1
    s2 = apply_mode_hysteresis(s1, "cautious")
    assert s2.current_mode == "neutral"
    assert s2.pending_mode == "cautious"
    assert s2.mode_streak == 2
    s3 = apply_mode_hysteresis(s2, "cautious")
    assert s3.current_mode == "cautious"
    assert s3.pending_mode == ""
    assert s3.mode_streak == 0


def test_relation_single_failure_small_trust_delta():
    p = PsycheV2Profile(user_id="u", updated_ts=time.time())
    t0 = p.relation_trust
    apply_relation_outcome_long_horizon(p, "failure", contradictions_present=0)
    assert abs(p.relation_trust - t0) < 0.02


def test_relation_ema_moves_slowly_across_mixed_outcomes():
    p = PsycheV2Profile(user_id="u", updated_ts=time.time())
    for _ in range(4):
        apply_relation_outcome_long_horizon(p, "success", contradictions_present=0)
    hi = p.relation_interaction_quality_ema
    apply_relation_outcome_long_horizon(p, "failure", contradictions_present=0)
    assert p.relation_interaction_quality_ema < hi


def test_self_consistency_dampens_transient_contradiction_overhang():
    mem = MemoryV2RuntimeContext(
        loaded=True,
        top_facts=[],
        top_preferences=[],
        top_procedures=[],
        contradiction_alerts=[],
        autobiographical_summary="",
        reinforced_patterns=[],
        retrieval_reason_codes=[],
        confidence_modifier=0.5,
        total_items=0,
        transient_contradiction_hints=["a", "b"],
    )
    psy = PsycheV2BehaviorContext(
        loaded=True,
        mode="neutral",
        pressure=0.4,
        trust=0.5,
        friction=0.1,
        warmth=0.5,
        directness_bias=0.5,
        reassurance_bias=0.5,
        autonomy_bias=0.5,
        structuredness_bias=0.5,
        tool_bias=0.5,
        web_bias=0.5,
        caution_bias=0.75,
        verbosity_bias=0.5,
        relation_quality_ema=0.5,
    )
    r = evaluate_self_consistency(
        memory_ctx=mem, psyche_ctx=psy, core_caution_baseline=0.35
    )
    assert r.decision == "suppress"


def test_snapshot_counts_actionable_contradictions_separately(isolated_db):
    uid = "snap-lh"
    ts = now_ts()
    # Suspected: not actionable
    insert_memory_item(
        MemoryV2Item(
            id="s1",
            user_id=uid,
            memory_type="fact",
            scope="user",
            source_kind="chat_turn",
            title="a",
            content="c",
            summary="s",
            salience_score=0.4,
            reinforcement_count=1,
            success_reinforcements=1,
            failure_reinforcements=0,
            contradiction_state="suspected",
            stability_tier="transient",
            created_ts=ts,
            updated_ts=ts,
        )
    )
    # Stable conflicted: actionable
    insert_memory_item(
        MemoryV2Item(
            id="s2",
            user_id=uid,
            memory_type="fact",
            scope="user",
            source_kind="chat_turn",
            title="b",
            content="c",
            summary="s",
            salience_score=0.5,
            reinforcement_count=5,
            success_reinforcements=4,
            failure_reinforcements=0,
            contradiction_state="conflicted",
            stability_tier="stable",
            created_ts=ts,
            updated_ts=ts,
        )
    )
    snap = build_memory_v2_runtime_snapshot(uid, "")
    assert snap.get("contradictions_count", 0) >= 2
    assert snap.get("actionable_contradictions_count", 0) == 1


def test_outcome_reinforcement_less_extreme_with_few_samples():
    low_n = calculate_outcome_reinforcement(1, 0, 1)
    high_n = calculate_outcome_reinforcement(5, 0, 5)
    assert low_n < high_n
    assert abs(low_n - 0.5) < abs(high_n - 0.5)


def test_initial_tier_explicit_user_learning_is_developing_not_stable():
    assert (
        initial_stability_tier_for_new_item("preference", "user", "explicit_learning")
        == "developing"
    )
    assert initial_stability_tier_for_new_item("fact", "session", "chat_turn") == "transient"


def test_self_consistency_flags_small_procedure_evidence():
    mem = MemoryV2RuntimeContext(
        loaded=True,
        top_facts=[],
        top_preferences=[],
        top_procedures=[{"evidence_count": 1, "confidence": 0.2}],
        contradiction_alerts=[],
        autobiographical_summary="",
        reinforced_patterns=[],
        retrieval_reason_codes=[],
        confidence_modifier=0.45,
        total_items=2,
        confidence_modifier_raw=0.85,
    )
    psy = PsycheV2BehaviorContext(
        loaded=True,
        mode="neutral",
        pressure=0.3,
        trust=0.5,
        friction=0.1,
        warmth=0.5,
        directness_bias=0.5,
        reassurance_bias=0.5,
        autonomy_bias=0.5,
        structuredness_bias=0.5,
        tool_bias=0.5,
        web_bias=0.5,
        caution_bias=0.5,
        verbosity_bias=0.5,
        relation_quality_ema=0.5,
        psyche_drift_score=0.1,
    )
    r = evaluate_self_consistency(memory_ctx=mem, psyche_ctx=psy, core_caution_baseline=0.5)
    assert any("small_evidence" in x for x in r.reasons)
    assert r.decision == "dampen"


@pytest.mark.asyncio
async def test_psyche_apply_event_smooths_pressure(isolated_db):
    uid = "psyche-smooth"
    svc = PsycheV2Service()
    svc.ensure_user(uid)
    svc.apply_event(uid, "tool_failure", "f1", signal_strength=0.9)
    svc.apply_event(uid, "tool_failure", "f2", signal_strength=0.9)
    snap = svc.get_snapshot(uid)
    assert 0.0 < snap.state.pressure_smoothed <= 1.0
    assert snap.state.pressure > 0.0
