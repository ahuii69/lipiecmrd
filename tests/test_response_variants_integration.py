#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Tests for ResponseVariantsEngine — integration + execution-impact tests.

Proves that:
1. Deliberation engine triggers on grey-zone conditions
2. Generated candidates get real multi-axis scoring
3. Synthesis produces a different (better) response than the original
4. Full pipeline wiring through run_deliberation is functional
5. Non-triggering conditions pass through unchanged
6. Trace metadata contains all required fields
7. Experience write-back fields are populated
"""

from __future__ import annotations

import pytest

from aihub.response_variants_engine import (
    CONFIDENCE_GREY_ZONE_UPPER,
    SIMULATION_RISK_THRESHOLD,
    ResponseCandidate,
    ResponseSynthesisResult,
    ResponseVariantsEngine,
)

# ── Helpers ───────────────────────────────────────────────────────────────


def _make_decision_core(
    *,
    strategy: str = "contextual",
    confidence: float = 0.5,
    simulation_ran: bool = False,
    simulation_risk: str = "",
    consistency: str | None = None,
    experience_blocker: str | None = None,
    handoff_bias: float = 0.0,
    exp_handoff_bias: float = 0.0,
) -> dict:
    return {
        "selected_strategy": strategy,
        "strategy_confidence": confidence,
        "escalation_use_reasoning": True,
        "reason_codes": ["test"],
        "strategy_degraded": False,
        "strategy_hints": [],
        "simulation_ran": simulation_ran,
        "simulation_risk_summary": simulation_risk,
        "simulation_best_action": None,
        "simulation_variants_count": 0,
        "consistency_check_ran": False,
        "consistency_classification": consistency,
        "contradictions_found": 0,
        "policy_hints_loaded": False,
        "policy_profile_name": None,
        "experience_blocker_reason": experience_blocker,
        "policy_handoff_bias": handoff_bias,
        "experience_handoff_bias": exp_handoff_bias,
    }


def _make_blocker(
    *, active: bool = False, hard: bool = False, resolution: str = "allow"
):
    return {
        "blocker_active": active,
        "hard": hard,
        "resolution": resolution,
    }


# ── 1) Trigger Logic Tests ───────────────────────────────────────────────


class TestTriggerDecision:
    """should_run_variants must correctly detect grey-zone conditions."""

    def test_instant_strategy_never_triggers(self) -> None:
        """Instant strategy = fast path, deliberation never runs."""
        dc = _make_decision_core(strategy="instant", confidence=0.1)
        should_run, reasons = ResponseVariantsEngine.should_run_variants(
            decision_core=dc,
            response_text="Some response text that is long enough for deliberation.",
        )
        assert should_run is False
        assert reasons == []

    def test_grey_zone_confidence_triggers(self) -> None:
        """Confidence below threshold should trigger deliberation."""
        dc = _make_decision_core(confidence=CONFIDENCE_GREY_ZONE_UPPER - 0.05)
        should_run, reasons = ResponseVariantsEngine.should_run_variants(
            decision_core=dc,
            response_text="A" * 100,
        )
        assert should_run is True
        assert "grey_zone_confidence" in reasons

    def test_high_confidence_does_not_trigger(self) -> None:
        """Confidence well above threshold should NOT trigger."""
        dc = _make_decision_core(confidence=0.95)
        should_run, reasons = ResponseVariantsEngine.should_run_variants(
            decision_core=dc,
            response_text="A" * 100,
        )
        # Unless other conditions trigger it
        assert "grey_zone_confidence" not in reasons

    def test_simulation_risk_triggers(self) -> None:
        """Elevated simulation risk should trigger deliberation."""
        dc = _make_decision_core(
            confidence=0.9,
            simulation_ran=True,
            simulation_risk=f"{SIMULATION_RISK_THRESHOLD + 0.1}",
        )
        should_run, reasons = ResponseVariantsEngine.should_run_variants(
            decision_core=dc,
            response_text="A" * 100,
        )
        assert should_run is True
        assert "simulation_risk_elevated" in reasons

    def test_consistency_conflict_triggers(self) -> None:
        """Consistency conflict should trigger deliberation."""
        dc = _make_decision_core(confidence=0.9, consistency="conflict")
        should_run, reasons = ResponseVariantsEngine.should_run_variants(
            decision_core=dc,
            response_text="A" * 100,
        )
        assert should_run is True
        assert "consistency_conflict" in reasons

    def test_blocker_caution_triggers(self) -> None:
        """Active soft blocker with caution_pass should trigger."""
        dc = _make_decision_core(confidence=0.9)
        blocker = _make_blocker(active=True, hard=False, resolution="caution_pass")
        should_run, reasons = ResponseVariantsEngine.should_run_variants(
            decision_core=dc,
            blocker_verdict=blocker,
            response_text="A" * 100,
        )
        assert should_run is True
        assert "blocker_caution_active" in reasons

    def test_hard_blocker_does_not_trigger(self) -> None:
        """Hard blocker should NOT trigger deliberation (it blocks the turn)."""
        dc = _make_decision_core(confidence=0.9)
        blocker = _make_blocker(active=True, hard=True, resolution="hard_block")
        should_run, reasons = ResponseVariantsEngine.should_run_variants(
            decision_core=dc,
            blocker_verdict=blocker,
            response_text="A" * 100,
        )
        assert "blocker_caution_active" not in reasons

    def test_experience_caution_triggers(self) -> None:
        """Experience blocker reason should trigger."""
        dc = _make_decision_core(confidence=0.9, experience_blocker="past_failure")
        should_run, reasons = ResponseVariantsEngine.should_run_variants(
            decision_core=dc,
            response_text="A" * 100,
        )
        assert should_run is True
        assert "experience_caution" in reasons

    def test_handoff_uncertainty_triggers(self) -> None:
        """Handoff bias in the uncertain range should trigger."""
        dc = _make_decision_core(confidence=0.9, handoff_bias=0.3)
        should_run, reasons = ResponseVariantsEngine.should_run_variants(
            decision_core=dc,
            response_text="A" * 100,
        )
        assert should_run is True
        assert "handoff_uncertainty" in reasons


# ── 2) Variant Spec Generation ────────────────────────────────────────────


class TestVariantSpecs:
    """build_variant_specs must produce 3 properly structured specs."""

    def test_produces_three_specs(self) -> None:
        dc = _make_decision_core()
        specs = ResponseVariantsEngine.build_variant_specs(
            original_messages=[{"role": "user", "content": "test"}],
            decision_core=dc,
            original_response="original text",
        )
        assert len(specs) == 3

    def test_variant_types(self) -> None:
        dc = _make_decision_core()
        specs = ResponseVariantsEngine.build_variant_specs(
            original_messages=[{"role": "user", "content": "test"}],
            decision_core=dc,
            original_response="original text",
        )
        types = {s["variant_type"] for s in specs}
        assert types == {"direct", "contextual", "actionable"}

    def test_system_prompt_injected(self) -> None:
        """Each spec must modify the system prompt for variant steering."""
        dc = _make_decision_core()
        specs = ResponseVariantsEngine.build_variant_specs(
            original_messages=[
                {"role": "system", "content": "original system"},
                {"role": "user", "content": "test"},
            ],
            decision_core=dc,
            original_response="original text",
        )
        for spec in specs:
            first_msg = spec["messages"][0]
            assert first_msg["role"] == "system"
            # Must contain variant-specific prefix + original system prompt
            assert "original system" in first_msg["content"]
            assert len(first_msg["content"]) > len("original system")


# ── 3) Candidate Evaluation ──────────────────────────────────────────────


class TestCandidateEvaluation:
    """evaluate_candidates must produce real, non-zero scores with execution impact."""

    def test_scores_are_populated(self) -> None:
        """Each quality axis must have a meaningful score."""
        candidates = [
            ResponseCandidate(
                variant_id="rv_direct_test",
                variant_type="direct",
                text="To jest krótka, konkretna odpowiedź na pytanie.",
            ),
            ResponseCandidate(
                variant_id="rv_contextual_test",
                variant_type="contextual",
                text=(
                    "W kontekście tego pytania należy rozważyć kilka aspektów. "
                    "Po pierwsze, kwestia związana z historią tematu. Po drugie, "
                    "obecne trendy wskazują na istotne zmiany w podejściu do tego zagadnienia. "
                    "Warto również zwrócić uwagę na powiązania z innymi dziedzinami."
                ),
            ),
            ResponseCandidate(
                variant_id="rv_actionable_test",
                variant_type="actionable",
                text=(
                    "Wykonaj następujące kroki:\n"
                    "1. Zainstaluj wymagane zależności\n"
                    "2. Skonfiguruj plik .env\n"
                    "3. Uruchom serwer\n"
                    "```bash\npip install -r requirements.txt\n```"
                ),
            ),
        ]

        dc = _make_decision_core()
        evaluated = ResponseVariantsEngine.evaluate_candidates(
            candidates,
            decision_core=dc,
            original_response="original",
        )

        for c in evaluated:
            assert c.clarity_score > 0, f"{c.variant_type} clarity must be > 0"
            assert c.goal_fit_score > 0, f"{c.variant_type} goal_fit must be > 0"
            assert (
                c.actionability_score > 0
            ), f"{c.variant_type} actionability must be > 0"
            assert c.style_fit_score > 0, f"{c.variant_type} style_fit must be > 0"
            assert c.aggregate_score > 0, f"{c.variant_type} aggregate must be > 0"
            assert c.confidence_estimate > 0, f"{c.variant_type} confidence must be > 0"

    def test_actionable_variant_scores_higher_on_actionability(self) -> None:
        """The actionable variant with steps/code should score higher on actionability."""
        candidates = [
            ResponseCandidate(
                variant_id="rv_direct_test",
                variant_type="direct",
                text="To jest prosta odpowiedź bez kroków.",
            ),
            ResponseCandidate(
                variant_id="rv_actionable_test",
                variant_type="actionable",
                text=(
                    "Wykonaj kroki:\n"
                    "- krok 1: zainstaluj\n"
                    "- krok 2: skonfiguruj\n"
                    "```python\nprint('hello')\n```"
                ),
            ),
        ]

        dc = _make_decision_core()
        evaluated = ResponseVariantsEngine.evaluate_candidates(
            candidates,
            decision_core=dc,
            original_response="original",
        )

        direct = next(c for c in evaluated if c.variant_type == "direct")
        actionable = next(c for c in evaluated if c.variant_type == "actionable")
        assert (
            actionable.actionability_score > direct.actionability_score
        ), "Actionable variant should score higher on actionability axis"

    def test_empty_response_gets_zero_aggregate(self) -> None:
        """Empty candidate text should get aggregate_score = 0."""
        candidates = [
            ResponseCandidate(
                variant_id="rv_empty",
                variant_type="direct",
                text="",
            ),
        ]
        dc = _make_decision_core()
        evaluated = ResponseVariantsEngine.evaluate_candidates(
            candidates, decision_core=dc, original_response="original"
        )
        assert evaluated[0].aggregate_score == 0.0
        assert "empty_response" in evaluated[0].cons

    def test_high_certainty_words_increase_risk(self) -> None:
        """Response with many certainty words should have elevated risk score."""
        candidates = [
            ResponseCandidate(
                variant_id="rv_certain",
                variant_type="contextual",
                text=(
                    "Na pewno to jest absolutnie zawsze prawda. "
                    "Zdecydowanie nigdy nie ma wyjątków od tej reguły. "
                    "Certainly this is absolutely always the case."
                ),
            ),
            ResponseCandidate(
                variant_id="rv_normal",
                variant_type="contextual",
                text="Wydaje się, że jest to prawdopodobne w większości przypadków.",
            ),
        ]
        dc = _make_decision_core()
        evaluated = ResponseVariantsEngine.evaluate_candidates(
            candidates, decision_core=dc, original_response="original"
        )
        certain = next(c for c in evaluated if c.variant_id == "rv_certain")
        normal = next(c for c in evaluated if c.variant_id == "rv_normal")
        assert (
            certain.risk_score > normal.risk_score
        ), "High certainty word count should increase risk"

    def test_pros_cons_populated(self) -> None:
        """Candidates with good structure should have pros populated."""
        candidates = [
            ResponseCandidate(
                variant_id="rv_good",
                variant_type="contextual",
                text=(
                    "To jest dobrze ustrukturyzowana odpowiedź z wieloma zdaniami. "
                    "Zawiera kontekst, wyjaśnienie i jest czytelna. "
                    "Poniżej znajdziesz dodatkowe informacje na ten temat."
                ),
            ),
        ]
        dc = _make_decision_core()
        evaluated = ResponseVariantsEngine.evaluate_candidates(
            candidates, decision_core=dc, original_response="original"
        )
        assert len(evaluated[0].pros) > 0, "Good candidate should have pros"


# ── 4) Synthesis Tests ────────────────────────────────────────────────────


class TestSynthesis:
    """synthesize_final_response must produce a merged result from scored candidates."""

    def test_winner_is_highest_scoring(self) -> None:
        """The winner must be the candidate with the highest aggregate score."""
        c1 = ResponseCandidate(
            variant_id="rv_a", variant_type="direct", text="Short.", aggregate_score=0.3
        )
        c2 = ResponseCandidate(
            variant_id="rv_b",
            variant_type="contextual",
            text="Longer and more detailed text.",
            aggregate_score=0.8,
        )
        c3 = ResponseCandidate(
            variant_id="rv_c",
            variant_type="actionable",
            text="Steps to do.",
            aggregate_score=0.5,
        )

        result = ResponseVariantsEngine.synthesize_final_response(
            [c1, c2, c3], original_response="original"
        )
        assert result.winner_variant_id == "rv_b"
        assert result.winner_variant_type == "contextual"
        assert result.synthesis_confidence == 0.8

    def test_unique_paragraphs_merged(self) -> None:
        """Non-redundant paragraphs from losers should be merged into the final response."""
        winner = ResponseCandidate(
            variant_id="rv_w",
            variant_type="direct",
            text="Główna odpowiedź na pytanie użytkownika jest taka.",
            aggregate_score=0.9,
        )
        loser = ResponseCandidate(
            variant_id="rv_l",
            variant_type="actionable",
            text="Główna odpowiedź.\n\nDodatkowo możesz wykonać następujące kroki do pełnej realizacji zadania.",
            aggregate_score=0.6,
        )

        result = ResponseVariantsEngine.synthesize_final_response(
            [winner, loser], original_response="original"
        )
        # The merged text should be longer than just the winner
        assert len(result.final_response) > len(winner.text)
        assert winner.variant_id in result.used_variants

    def test_low_scoring_candidates_dropped(self) -> None:
        """Candidates with aggregate < 0.25 should be dropped, not merged."""
        winner = ResponseCandidate(
            variant_id="rv_good",
            variant_type="direct",
            text="Good response.",
            aggregate_score=0.9,
        )
        bad = ResponseCandidate(
            variant_id="rv_bad",
            variant_type="contextual",
            text="Terrible response with no value.",
            aggregate_score=0.1,
        )

        result = ResponseVariantsEngine.synthesize_final_response(
            [winner, bad], original_response="original"
        )
        assert "rv_bad" in result.dropped_variants
        assert "rv_bad" not in result.used_variants

    def test_empty_candidates_returns_original(self) -> None:
        """No candidates → return original response."""
        result = ResponseVariantsEngine.synthesize_final_response(
            [], original_response="fallback"
        )
        assert result.final_response == "fallback"
        assert result.synthesis_reason == "no_candidates"

    def test_synthesis_result_fields_populated(self) -> None:
        """All synthesis result fields must be populated, not empty defaults."""
        c = ResponseCandidate(
            variant_id="rv_x",
            variant_type="direct",
            text="Real response text.",
            aggregate_score=0.7,
            risk_score=0.2,
            pros=["clear_structure"],
            cons=["too_brief"],
        )
        result = ResponseVariantsEngine.synthesize_final_response(
            [c], original_response="original"
        )
        assert result.winner_variant_id == "rv_x"
        assert result.candidates_evaluated == 1
        assert result.synthesis_confidence == 0.7
        assert result.synthesis_risk == 0.2
        assert result.synthesis_duration_ms >= 0
        assert "direct" in result.synthesis_reason


# ── 5) Full Pipeline (run_deliberation) ──────────────────────────────────


class TestFullDeliberationPipeline:
    """run_deliberation must trigger, generate, evaluate, synthesize, and return trace."""

    @pytest.mark.anyio
    async def test_deliberation_triggered_replaces_response(self) -> None:
        """When triggered, the final response must differ from the original."""

        original = "Oryginalna odpowiedź prowizoryczna."

        async def mock_provider_call(*, messages, tools):
            """Simulate provider generating variant-specific text."""
            from aihub.chat_contracts import ModelResponse, ProviderUsage

            content = messages[0].content if messages else ""
            if "zwięźle" in content:
                text = "Krótka zwięzła odpowiedź."
            elif "kontekst" in content.lower():
                text = (
                    "W szerokim kontekście, ta odpowiedź bierze pod uwagę wiele aspektów. "
                    "Dane historyczne, obecny stan i prognozy wskazują jednoznacznie na pozytywny trend."
                )
            else:
                text = (
                    "Wykonaj następujące kroki:\n"
                    "1. Pobierz narzędzie\n"
                    "2. Skonfiguruj ustawienia\n"
                    "3. Uruchom test\n"
                    "```bash\n./run.sh\n```"
                )

            return ModelResponse(
                content=text,
                model="test",
                provider="test",
                usage=ProviderUsage(
                    prompt_tokens=10, completion_tokens=20, total_tokens=30
                ),
                tool_calls=[],
            )

        dc = _make_decision_core(confidence=0.4)  # Grey zone → triggers

        final_text, metadata = await ResponseVariantsEngine.run_deliberation(
            decision_core=dc,
            blocker_verdict=None,
            original_response=original,
            original_messages=[{"role": "user", "content": "Jak to działa?"}],
            provider_call_fn=mock_provider_call,
        )

        # ── EXECUTION IMPACT: response_text was replaced ──
        assert final_text != original, "Deliberation must replace the original response"
        assert len(final_text) > 0, "Final text must not be empty"

        # ── METADATA: all required trace fields present ──
        assert metadata["response_variants_triggered"] is True
        assert metadata["response_variants_count"] == 3
        assert "grey_zone_confidence" in metadata["response_variants_reason_codes"]
        assert metadata["response_variants_winner"] is not None
        assert metadata["response_variants_winner_type"] in (
            "direct",
            "contextual",
            "actionable",
        )
        assert len(metadata["response_variants_synthesis_used"]) >= 1
        assert metadata["response_variants_confidence"] > 0
        assert metadata["response_variants_duration_ms"] > 0
        assert len(metadata["response_variants_scores"]) == 3

        # ── SCORES: each candidate has per-axis detail ──
        for score in metadata["response_variants_scores"]:
            assert "variant_type" in score
            assert "aggregate_score" in score
            assert score["aggregate_score"] > 0
            assert "clarity" in score
            assert "goal_fit" in score
            assert "risk" in score
            assert "actionability" in score

    @pytest.mark.anyio
    async def test_deliberation_not_triggered_passthrough(self) -> None:
        """When conditions are normal, deliberation should NOT trigger."""

        original = "Original high-confidence response."

        async def should_not_be_called(*, messages, tools):
            raise AssertionError(
                "Provider should not be called when deliberation is not triggered"
            )

        dc = _make_decision_core(strategy="instant", confidence=0.95)

        final_text, metadata = await ResponseVariantsEngine.run_deliberation(
            decision_core=dc,
            blocker_verdict=None,
            original_response=original,
            original_messages=[{"role": "user", "content": "test"}],
            provider_call_fn=should_not_be_called,
        )

        assert final_text == original, "Non-triggered deliberation must return original"
        assert metadata["response_variants_triggered"] is False
        assert metadata["response_variants_count"] == 0

    @pytest.mark.anyio
    async def test_variant_generation_failure_uses_original(self) -> None:
        """If all variant calls fail, the original response is used as fallback."""

        original = (
            "Fallback original text that is long enough for deliberation to consider."
        )

        async def failing_provider(*, messages, tools):
            raise RuntimeError("Provider down")

        dc = _make_decision_core(confidence=0.3)

        final_text, metadata = await ResponseVariantsEngine.run_deliberation(
            decision_core=dc,
            blocker_verdict=None,
            original_response=original,
            original_messages=[{"role": "user", "content": "help"}],
            provider_call_fn=failing_provider,
        )

        # Should still succeed with original text as all candidate fallbacks
        assert metadata["response_variants_triggered"] is True
        assert metadata["response_variants_count"] == 3
        # All candidates should have the "generation_failed_used_original" con
        for score in metadata["response_variants_scores"]:
            assert "generation_failed_used_original" in score["cons"]

    @pytest.mark.anyio
    async def test_multiple_trigger_reasons(self) -> None:
        """Multiple conditions should all appear in reason_codes."""

        async def mock_call(*, messages, tools):
            from aihub.chat_contracts import ModelResponse, ProviderUsage

            return ModelResponse(
                content="Variant text.",
                model="test",
                provider="test",
                usage=ProviderUsage(
                    prompt_tokens=5, completion_tokens=5, total_tokens=10
                ),
                tool_calls=[],
            )

        dc = _make_decision_core(
            confidence=0.3,  # grey_zone_confidence
            consistency="conflict",  # consistency_conflict
            experience_blocker="past_issue",  # experience_caution
        )

        _, metadata = await ResponseVariantsEngine.run_deliberation(
            decision_core=dc,
            blocker_verdict=None,
            original_response="Original response with enough text to be considered.",
            original_messages=[{"role": "user", "content": "test"}],
            provider_call_fn=mock_call,
        )

        reasons = metadata["response_variants_reason_codes"]
        assert "grey_zone_confidence" in reasons
        assert "consistency_conflict" in reasons
        assert "experience_caution" in reasons


# ── 6) Trace Field Contract Tests ─────────────────────────────────────────


class TestTraceFieldContract:
    """Verify that all trace fields expected by the cockpit are present."""

    REQUIRED_FIELDS = [
        "response_variants_triggered",
        "response_variants_count",
        "response_variants_reason_codes",
    ]

    TRIGGERED_FIELDS = [
        "response_variants_winner",
        "response_variants_winner_type",
        "response_variants_synthesis_used",
        "response_variants_dropped",
        "response_variants_confidence",
        "response_variants_risk",
        "response_variants_summary",
        "response_variants_duration_ms",
        "response_variants_scores",
    ]

    @pytest.mark.anyio
    async def test_non_triggered_metadata_has_base_fields(self) -> None:
        dc = _make_decision_core(strategy="instant")
        _, metadata = await ResponseVariantsEngine.run_deliberation(
            decision_core=dc,
            blocker_verdict=None,
            original_response="text",
            original_messages=[{"role": "user", "content": "x"}],
            provider_call_fn=lambda **kw: None,
        )
        for field in self.REQUIRED_FIELDS:
            assert field in metadata, f"Missing base field: {field}"

    @pytest.mark.anyio
    async def test_triggered_metadata_has_all_fields(self) -> None:
        async def mock_call(*, messages, tools):
            from aihub.chat_contracts import ModelResponse, ProviderUsage

            return ModelResponse(
                content="Generated variant text.",
                model="test",
                provider="test",
                usage=ProviderUsage(
                    prompt_tokens=5, completion_tokens=5, total_tokens=10
                ),
                tool_calls=[],
            )

        dc = _make_decision_core(confidence=0.3)
        _, metadata = await ResponseVariantsEngine.run_deliberation(
            decision_core=dc,
            blocker_verdict=None,
            original_response="Some original response text for deliberation.",
            original_messages=[{"role": "user", "content": "test"}],
            provider_call_fn=mock_call,
        )
        for field in self.REQUIRED_FIELDS + self.TRIGGERED_FIELDS:
            assert field in metadata, f"Missing triggered field: {field}"
