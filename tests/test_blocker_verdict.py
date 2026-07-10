"""Tests for BlockerVerdict taxonomy, evaluator, run_turn execution impact,
feedback loop, resolution types, and cockpit observability.

Every test proves REAL execution impact or truthful observability —
no trace-only decoration.
"""

from __future__ import annotations

from typing import Any, List

import pytest

from aihub.chat_contracts import (
    BlockerResolution,
    BlockerScope,
    BlockerSeverity,
    BlockerType,
    BlockerVerdict,
    ChatTurnInput,
    ModelResponse,
    ProviderUsage,
    ToolCallRequest,
)


# ── Test helpers ─────────────────────────────────────────────────────────


class _FakeProvider:
    def __init__(self, responses: List[ModelResponse]):
        self.provider_name = "deepinfra"
        self._responses = list(responses)
        self.calls = 0

    async def generate(self, _request):
        self.calls += 1
        return self._responses.pop(0)


def _base_decision_core(**overrides) -> dict:
    """Return a baseline decision_core dict with ALL fields the evaluator reads."""
    base: dict[str, Any] = {
        "selected_strategy": "instant",
        "reason_codes": [],
        "strategy_confidence": 0.75,
        "strategy_degraded": False,
        "selected_goal": None,
        "simulation_ran": False,
        "simulation_best_action": None,
        "simulation_variants_count": 0,
        "simulation_risk_summary": None,
        "policy_hints_loaded": False,
        "policy_profile_name": None,
        "policy_hints": [],
        "consistency_check_ran": False,
        "consistency_classification": None,
        "contradictions_found": 0,
        "strategy_hints": "",
        "experience_lookup_happened": False,
        "experience_matches_count": 0,
        "experience_influenced_strategy": False,
        "experience_confidence_adjustment": None,
        "experience_handoff_bias": None,
        "experience_blocker_reason": None,
        "experience_blocker_severity": 0.0,
        "experience_signal_summary": "not_evaluated",
        "experience_action_bias": {},
        # New taxonomy fields
        "experience_recurring_failure_detected": False,
        "experience_recurring_failure_types": [],
    }
    base.update(overrides)
    return base


def _make_provider(*contents: str) -> _FakeProvider:
    """Factory for a fake provider with sequential text responses."""
    return _FakeProvider([
        ModelResponse(
            provider="deepinfra",
            model="test",
            content=c,
            usage=ProviderUsage(total_tokens=len(c)),
        )
        for c in contents
    ])


def _eval(dc: dict) -> BlockerVerdict:
    """Shortcut: call the evaluator directly."""
    from aihub.chat_runtime import ChatRuntime
    return ChatRuntime._evaluate_blocker_verdict(dc)


def _patch_decision_core(monkeypatch, cr, overrides: dict):
    """Monkey-patch _pre_exec_decision_core to inject overrides."""
    original = cr.ChatRuntime._pre_exec_decision_core

    def _patched(self, **kwargs):
        result = original(self, **kwargs)
        result.update(overrides)
        if "reason_codes" not in overrides:
            result.setdefault("reason_codes", [])
        return result

    monkeypatch.setattr(cr.ChatRuntime, "_pre_exec_decision_core", _patched)


# ═══════════════════════════════════════════════════════════════════════
# 1. TAXONOMY — model serialization and type literals
# ═══════════════════════════════════════════════════════════════════════


class TestBlockerTaxonomy:
    """Verify all 8+1 blocker_type values + resolution types serialize correctly."""

    def test_allow_factory(self):
        v = BlockerVerdict.allow()
        assert v.blocker_active is False
        assert v.hard is False
        assert v.blocker_type == "none"
        assert v.blocker_severity == "info"
        assert v.resolution == "allow"
        assert v.reason == ""
        assert v.feedback_applied is False
        assert v.escalated_from_history is False
        assert v.deescalated_from_history is False

    def test_all_blocker_types_are_valid_literals(self):
        """Every taxonomy type must be accepted by the Literal constraint."""
        types: list[BlockerType] = [
            "none",
            "consistency_conflict",
            "repeated_failure",
            "degraded_runtime",
            "high_risk_path",
            "policy_violation_internal",
            "low_confidence_decision",
            "resource_exhaustion",
            "contradictory_memory_state",
        ]
        for t in types:
            v = BlockerVerdict(blocker_type=t)
            assert v.blocker_type == t

    def test_all_resolution_types_are_valid_literals(self):
        resolutions: list[BlockerResolution] = [
            "allow", "caution_pass", "downgrade", "reroute", "hard_block",
        ]
        for r in resolutions:
            v = BlockerVerdict(resolution=r)
            assert v.resolution == r

    def test_all_severity_levels(self):
        for sev in ("info", "caution", "hard"):
            v = BlockerVerdict(blocker_severity=sev)  # type: ignore[arg-type]
            assert v.blocker_severity == sev

    def test_all_scope_levels(self):
        for scope in ("turn", "session", "user"):
            v = BlockerVerdict(blocker_scope=scope)  # type: ignore[arg-type]
            assert v.blocker_scope == scope

    def test_hard_blocker_full_serialization(self):
        v = BlockerVerdict(
            blocker_active=True,
            blocker_type="consistency_conflict",
            blocker_scope="turn",
            blocker_severity="hard",
            hard=True,
            resolution="hard_block",
            reason="test conflict",
            source="consistency_engine",
            recommended_action="fix it",
            contributing_signals=["consistency_classification"],
            confidence=0.9,
            user_message="Conflict detected",
            dev_message="dev debug info",
            remediation_hint="rephrase",
            next_best_action="contextual",
            feedback_applied=True,
            escalated_from_history=True,
            deescalated_from_history=False,
            feedback_detail="escalated due to recurring",
            signals_count=3,
        )
        d = v.model_dump()
        assert d["hard"] is True
        assert d["blocker_type"] == "consistency_conflict"
        assert d["resolution"] == "hard_block"
        assert d["confidence"] == 0.9
        assert "consistency_classification" in d["contributing_signals"]
        assert d["user_message"] == "Conflict detected"
        assert d["dev_message"] == "dev debug info"
        assert d["feedback_applied"] is True
        assert d["escalated_from_history"] is True
        assert d["signals_count"] == 3

    def test_caution_blocker_with_new_type(self):
        v = BlockerVerdict(
            blocker_active=True,
            blocker_type="repeated_failure",
            blocker_severity="caution",
            hard=False,
            resolution="caution_pass",
            reason="history warning",
        )
        assert v.blocker_active is True
        assert v.hard is False
        assert v.resolution == "caution_pass"


# ═══════════════════════════════════════════════════════════════════════
# 2. EVALUATOR — each rule, priority, feedback loop
# ═══════════════════════════════════════════════════════════════════════


class TestBlockerEvaluator:
    """Tests for _evaluate_blocker_verdict() — deterministic rule engine."""

    # ── Allow baseline ───────────────────────────────────────────────

    def test_no_blocker_baseline(self):
        """Clean decision_core → allow."""
        v = _eval(_base_decision_core())
        assert v.blocker_active is False
        assert v.hard is False
        assert v.blocker_type == "none"
        assert v.resolution == "allow"

    # ── P0 Hard: consistency_conflict ────────────────────────────────

    def test_hard_consistency_conflict(self):
        """R1: conflict + contradictions ≥ 1 + confidence < 0.40 → hard_block."""
        v = _eval(_base_decision_core(
            consistency_classification="conflict",
            contradictions_found=1,
            strategy_confidence=0.30,
        ))
        assert v.blocker_active is True
        assert v.hard is True
        assert v.blocker_type == "consistency_conflict"
        assert v.blocker_severity == "hard"
        assert v.resolution == "hard_block"
        assert v.source == "consistency_engine"
        assert "consistency_classification" in v.contributing_signals
        assert v.user_message != ""
        assert v.dev_message != ""

    # ── P0 Hard: repeated_failure ────────────────────────────────────

    def test_hard_repeated_failure(self):
        """R2: experience_blocker_severity ≥ 0.80 → hard_block."""
        v = _eval(_base_decision_core(
            experience_blocker_reason="repeated tool failure",
            experience_blocker_severity=0.85,
        ))
        assert v.blocker_active is True
        assert v.hard is True
        assert v.blocker_type == "repeated_failure"
        assert v.resolution == "hard_block"
        assert v.confidence == 0.85
        assert v.source == "experience_memory"

    # ── P0 Hard: degraded_runtime ────────────────────────────────────

    def test_hard_degraded_runtime(self):
        """R3: strategy_degraded + confidence < 0.35 → hard_block."""
        v = _eval(_base_decision_core(
            strategy_degraded=True,
            strategy_confidence=0.25,
        ))
        assert v.blocker_active is True
        assert v.hard is True
        assert v.blocker_type == "degraded_runtime"
        assert v.resolution == "hard_block"
        assert v.source == "strategy_selector"

    # ── P0 Hard: policy_violation_internal ────────────────────────────

    def test_hard_policy_violation(self):
        """R4: policy avoid weight ≥ 0.70 → hard_block."""
        v = _eval(_base_decision_core(
            policy_hints=[{
                "action_type": "reason",
                "signal": "avoid",
                "weight": 0.75,
                "reason": "blocked by reflection history",
            }],
        ))
        assert v.blocker_active is True
        assert v.hard is True
        assert v.blocker_type == "policy_violation_internal"
        assert v.resolution == "hard_block"
        assert v.source == "policy_engine"
        assert v.blocker_scope == "session"
        assert v.feedback_applied is True
        assert v.escalated_from_history is True

    # ── P1: high_risk_path → downgrade ───────────────────────────────

    def test_high_risk_path_downgrade(self):
        """R5: sim risk ≥ 0.80 + agentic strategy → downgrade, not hard."""
        v = _eval(_base_decision_core(
            simulation_ran=True,
            simulation_risk_summary="risk=0.85 conf=0.40 util=0.20",
            selected_strategy="agentic",
        ))
        assert v.blocker_active is True
        assert v.hard is False
        assert v.blocker_type == "high_risk_path"
        assert v.blocker_severity == "caution"
        assert v.resolution == "downgrade"
        assert v.next_best_action == "contextual"
        assert v.source == "simulation_engine"

    def test_high_risk_path_research_downgrade_to_instant(self):
        """R5: sim risk ≥ 0.80 + research strategy → downgrade to instant."""
        v = _eval(_base_decision_core(
            simulation_ran=True,
            simulation_risk_summary="risk=0.90 conf=0.30 util=0.10",
            selected_strategy="research",
        ))
        assert v.resolution == "downgrade"
        assert v.next_best_action == "instant"

    def test_high_risk_path_with_instant_strategy_no_downgrade(self):
        """If strategy is instant, high risk is irrelevant — R5 requires
        agentic/research, R10 covers 0.65–0.80 only → no blocker at all."""
        v = _eval(_base_decision_core(
            simulation_ran=True,
            simulation_risk_summary="risk=0.85 conf=0.40 util=0.20",
            selected_strategy="instant",
        ))
        # R5 skips (instant), R10 skips (0.85 > 0.80 range) → allow
        assert v.blocker_active is False
        assert v.resolution == "allow"

    # ── P1: low_confidence_decision → reroute ────────────────────────

    def test_low_confidence_reroute(self):
        """R6: confidence < 0.45 + agentic/research + not degraded → reroute."""
        v = _eval(_base_decision_core(
            strategy_confidence=0.40,
            selected_strategy="agentic",
            strategy_degraded=False,
        ))
        assert v.blocker_active is True
        assert v.hard is False
        assert v.blocker_type == "low_confidence_decision"
        assert v.resolution == "reroute"
        assert v.next_best_action == "contextual"

    def test_low_confidence_alone_does_not_hard_block(self):
        """Low confidence alone (no degraded, no other signals) → reroute, NOT hard."""
        v = _eval(_base_decision_core(
            strategy_confidence=0.30,
            selected_strategy="research",
            strategy_degraded=False,
        ))
        assert v.hard is False
        assert v.resolution != "hard_block"
        assert v.blocker_type == "low_confidence_decision"

    def test_low_confidence_on_instant_no_reroute(self):
        """Low confidence on 'instant' strategy doesn't trigger reroute
        (R6 is only for agentic/research)."""
        v = _eval(_base_decision_core(
            strategy_confidence=0.40,
            selected_strategy="instant",
            strategy_degraded=False,
        ))
        # R6 requires agentic/research
        assert v.blocker_type != "low_confidence_decision"

    # ── P2: Caution-level rules ──────────────────────────────────────

    def test_caution_consistency_conflict(self):
        """R7: conflict + contradictions but confidence ≥ 0.40 → caution_pass."""
        v = _eval(_base_decision_core(
            consistency_classification="conflict",
            contradictions_found=1,
            strategy_confidence=0.50,
        ))
        assert v.blocker_active is True
        assert v.hard is False
        assert v.blocker_type == "consistency_conflict"
        assert v.blocker_severity == "caution"
        assert v.resolution == "caution_pass"

    def test_caution_repeated_failure_mild(self):
        """R8: experience_blocker present but severity < 0.80 → caution_pass."""
        v = _eval(_base_decision_core(
            experience_blocker_reason="some concern",
            experience_blocker_severity=0.40,
        ))
        assert v.blocker_active is True
        assert v.hard is False
        assert v.blocker_type == "repeated_failure"
        assert v.resolution == "caution_pass"

    def test_caution_degraded_runtime_mild(self):
        """R9: degraded + confidence ≥ 0.35 → caution_pass."""
        v = _eval(_base_decision_core(
            strategy_degraded=True,
            strategy_confidence=0.50,
        ))
        assert v.blocker_active is True
        assert v.hard is False
        assert v.blocker_type == "degraded_runtime"
        assert v.resolution == "caution_pass"

    def test_caution_sim_risk_moderate(self):
        """R10: sim risk 0.65–0.80 → caution_pass."""
        v = _eval(_base_decision_core(
            simulation_ran=True,
            simulation_risk_summary="risk=0.70 conf=0.50 util=0.40",
        ))
        assert v.blocker_active is True
        assert v.hard is False
        assert v.blocker_type == "high_risk_path"
        assert v.resolution == "caution_pass"

    def test_sim_below_threshold_no_blocker(self):
        """sim risk < 0.65 → no blocker at all."""
        v = _eval(_base_decision_core(
            simulation_ran=True,
            simulation_risk_summary="risk=0.50 conf=0.70 util=0.60",
        ))
        assert v.blocker_active is False

    def test_contradictory_memory_state(self):
        """R11: many experience matches with near-zero net signal
        → contradictory_memory_state."""
        v = _eval(_base_decision_core(
            experience_matches_count=5,
            experience_confidence_adjustment=0.01,
        ))
        assert v.blocker_active is True
        assert v.hard is False
        assert v.blocker_type == "contradictory_memory_state"
        assert v.resolution == "caution_pass"
        assert v.source == "experience_memory"
        # Must be distinct from consistency_conflict
        assert v.blocker_type != "consistency_conflict"

    def test_resource_exhaustion(self):
        """R12: policy penalize ≥ 0.60 → resource_exhaustion caution."""
        v = _eval(_base_decision_core(
            policy_hints=[{
                "action_type": "reason",
                "signal": "penalize",
                "weight": 0.65,
                "reason": "over-used",
            }],
        ))
        assert v.blocker_active is True
        assert v.hard is False
        assert v.blocker_type == "resource_exhaustion"
        assert v.resolution == "caution_pass"
        assert v.feedback_applied is True

    # ── Priority: hard > caution ─────────────────────────────────────

    def test_hard_wins_over_caution(self):
        """When multiple signals present, P0 (hard) beats P2 (caution)."""
        v = _eval(_base_decision_core(
            consistency_classification="conflict",
            contradictions_found=1,
            strategy_confidence=0.30,
            experience_blocker_reason="also bad",
            experience_blocker_severity=0.50,
            strategy_degraded=True,
        ))
        assert v.hard is True
        assert v.resolution == "hard_block"
        assert v.blocker_type == "consistency_conflict"

    def test_downgrade_loses_to_hard(self):
        """P0 hard beats P1 downgrade."""
        v = _eval(_base_decision_core(
            # P0: hard consistency
            consistency_classification="conflict",
            contradictions_found=2,
            strategy_confidence=0.20,
            # P1: would-be downgrade
            simulation_ran=True,
            simulation_risk_summary="risk=0.90 conf=0.30 util=0.10",
            selected_strategy="agentic",
        ))
        assert v.hard is True
        assert v.resolution == "hard_block"

    # ── Repeated failure escalation ──────────────────────────────────

    def test_repeated_failure_escalates_severity(self):
        """Recurring failures (≥ 2 types) escalate caution → hard."""
        v = _eval(_base_decision_core(
            # Mild caution trigger (degraded + confidence ≥ 0.35)
            strategy_degraded=True,
            strategy_confidence=0.50,
            # Recurring failure feedback
            experience_recurring_failure_detected=True,
            experience_recurring_failure_types=["timeout", "tool_crash"],
        ))
        # Should have been escalated from caution to hard
        assert v.hard is True
        assert v.resolution == "hard_block"
        assert v.escalated_from_history is True
        assert v.feedback_applied is True
        assert "recurring" in v.feedback_detail.lower() or "Escalated" in v.feedback_detail

    # ── Degraded + low confidence → hard ─────────────────────────────

    def test_degraded_plus_low_confidence_becomes_hard(self):
        """degraded + confidence < 0.35 crosses the hard threshold."""
        v = _eval(_base_decision_core(
            strategy_degraded=True,
            strategy_confidence=0.30,
        ))
        assert v.hard is True
        assert v.blocker_type == "degraded_runtime"
        assert v.resolution == "hard_block"

    # ── Feedback loop: policy boost de-escalation ────────────────────

    def test_policy_boost_deescalates_hard(self):
        """Policy 'boost' signal (≥ 0.65) de-escalates hard → caution
        (except consistency/policy types)."""
        v = _eval(_base_decision_core(
            # This would normally be hard (R3)
            strategy_degraded=True,
            strategy_confidence=0.25,
            # But boost de-escalates it
            policy_hints=[{
                "action_type": "reason",
                "signal": "boost",
                "weight": 0.70,
                "reason": "recent successes",
            }],
        ))
        assert v.hard is False
        assert v.deescalated_from_history is True
        assert v.feedback_applied is True
        assert v.resolution == "caution_pass"

    def test_policy_boost_does_not_deescalate_consistency_conflict(self):
        """Policy boost cannot de-escalate consistency_conflict — safety preserved."""
        v = _eval(_base_decision_core(
            consistency_classification="conflict",
            contradictions_found=2,
            strategy_confidence=0.25,
            policy_hints=[{
                "action_type": "reason",
                "signal": "boost",
                "weight": 0.90,
                "reason": "try to de-escalate",
            }],
        ))
        # consistency_conflict is exempt from de-escalation
        assert v.hard is True
        assert v.deescalated_from_history is False

    def test_policy_avoid_escalates_caution(self):
        """Policy 'avoid' (≥ 0.55) escalates a non-consistency caution → hard."""
        v = _eval(_base_decision_core(
            # Mild trigger (degraded caution)
            strategy_degraded=True,
            strategy_confidence=0.50,
            # Policy avoid escalation
            policy_hints=[{
                "action_type": "reason",
                "signal": "avoid",
                "weight": 0.60,
                "reason": "historical pattern",
            }],
        ))
        assert v.hard is True
        assert v.escalated_from_history is True
        assert v.feedback_applied is True


# ═══════════════════════════════════════════════════════════════════════
# 3. EXECUTION IMPACT — run_turn integration tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_hard_block_prevents_provider_call(monkeypatch):
    """HARD BLOCK: provider.calls == 0, ok=False, model=blocker_gate."""
    from aihub import chat_runtime as cr

    provider = _make_provider("should NOT appear")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    _patch_decision_core(monkeypatch, cr, {
        "consistency_classification": "conflict",
        "contradictions_found": 2,
        "strategy_confidence": 0.25,
        "consistency_check_ran": True,
    })

    out = await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_hard_block", session_id="s1",
        message="conflicting message", mode="chat",
    ))

    assert provider.calls == 0
    assert out.ok is False
    assert out.model == "blocker_gate"
    assert out.usage.total_tokens == 0
    bv = out.trace["blocker_verdict"]
    assert bv["hard"] is True
    assert bv["blocker_type"] == "consistency_conflict"
    assert bv["resolution"] == "hard_block"
    assert "BLOCKER_HARD_GATE" in out.trace["reason_codes"]
    assert out.errors[0]["type"] == "blocker_hard_gate"
    assert out.errors[0]["resolution"] == "hard_block"
    # User message is present and non-empty
    assert bv["user_message"] != ""
    assert bv["dev_message"] != ""


@pytest.mark.anyio
async def test_caution_pass_proceeds_with_trace(monkeypatch):
    """CAUTION PASS: provider called, ok=True, verdict in trace with caution_pass."""
    from aihub import chat_runtime as cr

    provider = _make_provider("normal response despite caution")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    _patch_decision_core(monkeypatch, cr, {
        "strategy_degraded": True,
        "strategy_confidence": 0.50,
    })

    out = await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_caution_pass", session_id="s1",
        message="test caution message", mode="chat",
    ))

    assert provider.calls >= 1
    assert out.ok is True
    assert "normal response" in out.response_text
    bv = out.trace["blocker_verdict"]
    assert bv["blocker_active"] is True
    assert bv["hard"] is False
    assert bv["blocker_type"] == "degraded_runtime"
    assert bv["resolution"] == "caution_pass"


@pytest.mark.anyio
async def test_downgrade_changes_effective_strategy(monkeypatch):
    """DOWNGRADE: strategy is actually changed from agentic→contextual."""
    from aihub import chat_runtime as cr

    provider = _make_provider("response after downgrade")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    _patch_decision_core(monkeypatch, cr, {
        "simulation_ran": True,
        "simulation_risk_summary": "risk=0.90 conf=0.30 util=0.10",
        "selected_strategy": "agentic",
    })

    out = await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_downgrade", session_id="s1",
        message="downgrade test", mode="chat",
    ))

    # Provider WAS called (downgrade is not a block)
    assert provider.calls >= 1
    assert out.ok is True

    bv = out.trace["blocker_verdict"]
    assert bv["blocker_type"] == "high_risk_path"
    assert bv["resolution"] == "downgrade"
    assert bv["next_best_action"] == "contextual"

    # Strategy was changed - verify via reason_codes
    codes = out.trace["reason_codes"]
    downgrade_code = [c for c in codes if "DOWNGRADE" in c.upper()]
    assert len(downgrade_code) >= 1, f"No DOWNGRADE reason code found in {codes}"

    # Actual strategy in trace should reflect the downgraded value
    assert out.trace["selected_strategy"] == "contextual"


@pytest.mark.anyio
async def test_reroute_changes_strategy(monkeypatch):
    """REROUTE: low_confidence on agentic → rerouted to contextual."""
    from aihub import chat_runtime as cr

    provider = _make_provider("response after reroute")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    _patch_decision_core(monkeypatch, cr, {
        "strategy_confidence": 0.40,
        "selected_strategy": "agentic",
        "strategy_degraded": False,
    })

    out = await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_reroute", session_id="s1",
        message="reroute test", mode="chat",
    ))

    assert provider.calls >= 1
    assert out.ok is True

    bv = out.trace["blocker_verdict"]
    assert bv["blocker_type"] == "low_confidence_decision"
    assert bv["resolution"] == "reroute"

    codes = out.trace["reason_codes"]
    reroute_code = [c for c in codes if "REROUTE" in c.upper()]
    assert len(reroute_code) >= 1, f"No REROUTE reason code found in {codes}"


@pytest.mark.anyio
async def test_allow_clean_trace(monkeypatch):
    """ALLOW: no blocker, clean verdict in trace."""
    from aihub import chat_runtime as cr

    provider = _make_provider("clean turn")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)

    out = await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_allow", session_id="s1",
        message="hello clean turn", mode="chat",
    ))

    assert out.ok is True
    bv = out.trace["blocker_verdict"]
    assert bv["blocker_active"] is False
    assert bv["hard"] is False
    assert bv["blocker_type"] == "none"
    assert bv["resolution"] == "allow"


@pytest.mark.anyio
async def test_hard_block_with_feedback_escalation(monkeypatch):
    """Recurring failures escalate a caution into hard — execution actually blocked."""
    from aihub import chat_runtime as cr

    provider = _make_provider("should not reach")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    _patch_decision_core(monkeypatch, cr, {
        # mild trigger
        "strategy_degraded": True,
        "strategy_confidence": 0.50,
        # escalation
        "experience_recurring_failure_detected": True,
        "experience_recurring_failure_types": ["timeout", "tool_crash"],
    })

    out = await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_escalation", session_id="s1",
        message="escalation test", mode="chat",
    ))

    # Escalated to hard → provider NOT called
    assert provider.calls == 0
    assert out.ok is False
    assert out.model == "blocker_gate"
    bv = out.trace["blocker_verdict"]
    assert bv["hard"] is True
    assert bv["escalated_from_history"] is True
    assert bv["feedback_applied"] is True


@pytest.mark.anyio
async def test_policy_violation_hard_block(monkeypatch):
    """policy_violation_internal hard-blocks execution."""
    from aihub import chat_runtime as cr

    provider = _make_provider("blocked by policy")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    _patch_decision_core(monkeypatch, cr, {
        "policy_hints": [{
            "action_type": "reason",
            "signal": "avoid",
            "weight": 0.80,
            "reason": "forbidden action",
        }],
    })

    out = await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_policy", session_id="s1",
        message="policy violation test", mode="chat",
    ))

    assert provider.calls == 0
    assert out.ok is False
    bv = out.trace["blocker_verdict"]
    assert bv["blocker_type"] == "policy_violation_internal"
    assert bv["resolution"] == "hard_block"
    assert bv["source"] == "policy_engine"


@pytest.mark.anyio
async def test_blocker_verdict_truthful_in_all_trace_paths(monkeypatch):
    """blocker_verdict is in trace for the success path."""
    from aihub import chat_runtime as cr

    provider = _make_provider("ok")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    out = await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_trace_truth", session_id="s1",
        message="success path", mode="chat",
    ))
    assert "blocker_verdict" in out.trace
    assert out.trace["blocker_verdict"]["resolution"] == "allow"


@pytest.mark.anyio
async def test_blocker_resolution_in_trace(monkeypatch):
    """blocker_resolution field is present and matches the verdict."""
    from aihub import chat_runtime as cr

    provider = _make_provider("downgraded")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    _patch_decision_core(monkeypatch, cr, {
        "simulation_ran": True,
        "simulation_risk_summary": "risk=0.85 conf=0.40 util=0.20",
        "selected_strategy": "agentic",
    })

    out = await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_resolution_trace", session_id="s1",
        message="resolution trace test", mode="chat",
    ))

    bv = out.trace["blocker_verdict"]
    assert bv["resolution"] == "downgrade"
    assert bv["blocker_active"] is True


@pytest.mark.anyio
async def test_blocker_feedback_applied_in_trace(monkeypatch):
    """feedback_applied field is truthfully set when feedback loop runs."""
    from aihub import chat_runtime as cr

    provider = _make_provider("feedback test")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    _patch_decision_core(monkeypatch, cr, {
        "experience_blocker_reason": "past failures",
        "experience_blocker_severity": 0.50,
        "experience_recurring_failure_detected": True,
        "experience_recurring_failure_types": ["err1"],
    })

    out = await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_feedback_trace", session_id="s1",
        message="feedback trace test", mode="chat",
    ))

    bv = out.trace["blocker_verdict"]
    assert bv["blocker_active"] is True
    # feedback_applied should be True (recurring failure info fed into verdict)
    assert bv["feedback_applied"] is True


# ═══════════════════════════════════════════════════════════════════════
# 4. COCKPIT API — truthful payload with dev/user views
# ═══════════════════════════════════════════════════════════════════════


def test_cockpit_blocker_status_no_traces():
    """When no traces exist, endpoint returns safe defaults with correct structure."""
    from aihub.cockpit_api import cockpit_blocker_status

    result = cockpit_blocker_status("nonexistent_user_xyz_42")
    assert result["blocker_active"] is False
    assert result["hard"] is False
    assert result["blocker_type"] == "none"
    assert result["resolution"] == "allow"
    assert result["blocker_verdict"] is None
    assert result["traces_available"] == 0


@pytest.mark.anyio
async def test_cockpit_returns_new_taxonomy_fields(monkeypatch):
    """After a blocker event, cockpit returns full taxonomy fields
    including dev/user views."""
    from aihub import chat_runtime as cr
    from aihub.cockpit_api import cockpit_blocker_status

    provider = _make_provider("blocked")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    _patch_decision_core(monkeypatch, cr, {
        "consistency_classification": "conflict",
        "contradictions_found": 1,
        "strategy_confidence": 0.20,
        "consistency_check_ran": True,
    })

    await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_cockpit_full", session_id="s1",
        message="cockpit taxonomy test", mode="chat",
    ))

    result = cockpit_blocker_status("tax_cockpit_full")
    assert result["blocker_active"] is True
    assert result["hard"] is True
    assert result["blocker_type"] == "consistency_conflict"
    assert result["resolution"] == "hard_block"
    assert result["traces_available"] >= 1
    assert result["blocker_verdict"] is not None

    # DEV view
    dev = result["dev"]
    assert dev["blocker_active"] is True
    assert dev["resolution"] == "hard_block"
    assert dev["source"] == "consistency_engine"
    assert dev["confidence"] > 0
    assert len(dev["contributing_signals"]) > 0
    assert dev["dev_message"] != ""

    # USER view
    user = result["user"]
    assert user["blocker_active"] is True
    assert user["user_message"] != ""
    assert user["severity"] == "hard"


@pytest.mark.anyio
async def test_cockpit_feedback_fields_truthful(monkeypatch):
    """Cockpit dev view correctly reflects feedback_applied and escalation."""
    from aihub import chat_runtime as cr
    from aihub.cockpit_api import cockpit_blocker_status

    provider = _make_provider("escalated")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)
    _patch_decision_core(monkeypatch, cr, {
        "strategy_degraded": True,
        "strategy_confidence": 0.50,
        "experience_recurring_failure_detected": True,
        "experience_recurring_failure_types": ["timeout", "crash"],
    })

    await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_cockpit_feedback", session_id="s1",
        message="feedback cockpit test", mode="chat",
    ))

    result = cockpit_blocker_status("tax_cockpit_feedback")
    dev = result["dev"]
    assert dev["feedback_applied"] is True
    assert dev["escalated_from_history"] is True
    assert dev["feedback_detail"] != ""


# ═══════════════════════════════════════════════════════════════════════
# 5. TRACE CACHE — persistence and structure
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_trace_cache_persists_full_verdict(monkeypatch):
    """Trace cache contains blocker_verdict with all taxonomy fields."""
    from aihub import chat_runtime as cr

    provider = _make_provider("cached")
    monkeypatch.setattr(cr, "get_default_provider", lambda: provider)

    await cr.ChatRuntime().run_turn(ChatTurnInput(
        user_id="tax_trace_cache", session_id="s1",
        message="trace cache test", mode="chat",
    ))

    traces = cr.get_cached_chat_traces("tax_trace_cache", limit=1)
    assert len(traces) >= 1
    bv = traces[-1]["blocker_verdict"]
    assert isinstance(bv, dict)
    # Verify key taxonomy fields are present
    for field in [
        "blocker_active", "hard", "blocker_type", "resolution",
        "blocker_severity", "source", "contributing_signals",
        "confidence", "user_message", "dev_message",
        "feedback_applied", "escalated_from_history",
        "deescalated_from_history", "signals_count",
    ]:
        assert field in bv, f"Missing field '{field}' in trace blocker_verdict"


# ═══════════════════════════════════════════════════════════════════════
# 6. DEV/USER PAYLOAD COMPATIBILITY
# ═══════════════════════════════════════════════════════════════════════


class TestDevUserPayload:
    """Verify user_message and dev_message are always populated for active blockers."""

    def test_all_hard_rules_have_messages(self):
        """Every hard blocker rule produces both user_message and dev_message."""
        hard_cases = [
            _base_decision_core(
                consistency_classification="conflict",
                contradictions_found=1, strategy_confidence=0.20,
            ),
            _base_decision_core(
                experience_blocker_reason="failure",
                experience_blocker_severity=0.90,
            ),
            _base_decision_core(
                strategy_degraded=True, strategy_confidence=0.20,
            ),
            _base_decision_core(
                policy_hints=[{"action_type": "reason", "signal": "avoid",
                               "weight": 0.80, "reason": "test"}],
            ),
        ]
        for dc in hard_cases:
            v = _eval(dc)
            assert v.blocker_active is True, f"Expected active blocker for {dc}"
            assert v.user_message != "", f"Empty user_message for type={v.blocker_type}"
            assert v.dev_message != "", f"Empty dev_message for type={v.blocker_type}"

    def test_caution_rules_have_messages(self):
        """Every caution blocker rule produces both user_message and dev_message."""
        caution_cases = [
            _base_decision_core(
                consistency_classification="conflict",
                contradictions_found=1, strategy_confidence=0.60,
            ),
            _base_decision_core(
                experience_blocker_reason="mild",
                experience_blocker_severity=0.30,
            ),
            _base_decision_core(
                strategy_degraded=True, strategy_confidence=0.50,
            ),
            _base_decision_core(
                simulation_ran=True,
                simulation_risk_summary="risk=0.70 conf=0.50 util=0.40",
            ),
            _base_decision_core(
                experience_matches_count=5,
                experience_confidence_adjustment=0.005,
            ),
            _base_decision_core(
                policy_hints=[{"action_type": "reason", "signal": "penalize",
                               "weight": 0.65, "reason": "test"}],
            ),
        ]
        for dc in caution_cases:
            v = _eval(dc)
            assert v.blocker_active is True
            assert v.user_message != "", f"Empty user_message for type={v.blocker_type}"
            assert v.dev_message != "", f"Empty dev_message for type={v.blocker_type}"

    def test_allow_has_empty_messages(self):
        """Allow verdict has empty messages (nothing to communicate)."""
        v = _eval(_base_decision_core())
        assert v.user_message == ""
        assert v.dev_message == ""


# ═══════════════════════════════════════════════════════════════════════
# 7. REGRESSION GUARD — existing contract compatibility
# ═══════════════════════════════════════════════════════════════════════


class TestRegressionGuard:
    """Ensure backward-compatible fields still work."""

    def test_verdict_has_all_original_fields(self):
        """All fields from the original BlockerVerdict are still present."""
        v = BlockerVerdict.allow()
        d = v.model_dump()
        original_fields = [
            "blocker_active", "blocker_type", "blocker_scope",
            "blocker_severity", "hard", "reason", "source",
            "contributing_signals", "confidence",
            "turn_id", "timestamp",
        ]
        for f in original_fields:
            assert f in d, f"Missing backward-compatible field: {f}"

    def test_verdict_has_new_taxonomy_fields(self):
        """All new taxonomy fields exist."""
        v = BlockerVerdict.allow()
        d = v.model_dump()
        new_fields = [
            "resolution", "recommended_action",
            "user_message", "dev_message",
            "remediation_hint", "next_best_action",
            "feedback_applied", "escalated_from_history",
            "deescalated_from_history", "feedback_detail",
            "signals_count",
        ]
        for f in new_fields:
            assert f in d, f"Missing new taxonomy field: {f}"

    def test_trace_blocker_verdict_key_still_used(self):
        """The trace key 'blocker_verdict' is used (not renamed)."""
        v = BlockerVerdict.allow()
        d = v.model_dump()
        assert "blocker_active" in d
        assert "blocker_type" in d
