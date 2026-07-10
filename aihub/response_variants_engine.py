#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ResponseVariantsEngine — Tri-Response Deliberation Engine.

Active runtime layer that generates up to 3 structurally distinct response
candidates (direct / contextual / actionable), evaluates each on multiple
quality axes, then synthesizes a single superior final response by merging
the winner's core with strong fragments from other candidates.

This engine is triggered conditionally — only when the decision core
signals uncertainty (grey-zone confidence, caution blockers, simulation
risk, etc.).  When NOT triggered, the original provider response passes
through untouched — zero overhead, zero side-effects.

Integration point: after provider call loop, before grounding/shaping.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Variant archetypes ────────────────────────────────────────────────────

VARIANT_TYPES = ("direct", "contextual", "actionable")

VARIANT_SYSTEM_PREFIXES: dict[str, str] = {
    "direct": (
        "Odpowiedz zwięźle, konkretnie i na temat. "
        "Nie dodawaj kontekstu, zastrzeżeń ani dalszych kroków — "
        "sam rdzeń odpowiedzi."
    ),
    "contextual": (
        "Odpowiedz z uwzględnieniem kontekstu, tła i niuansów. "
        "Podaj wyjaśnienie, powiązania i uzasadnienie odpowiedzi. "
        "Złożoność jest ok, jeśli jest czytelna."
    ),
    "actionable": (
        "Odpowiedz zorientowany na działanie. "
        "Podaj konkretne kroki, instrukcje do wykonania lub gotowy artefakt. "
        "Użytkownik powinien móc natychmiast działać po przeczytaniu odpowiedzi."
    ),
}


# ── Data models ───────────────────────────────────────────────────────────


@dataclass
class ResponseCandidate:
    """One candidate response variant with multi-axis scoring."""

    variant_id: str
    variant_type: str  # "direct" | "contextual" | "actionable"
    text: str = ""
    # ── Quality scores (0.0–1.0, higher = better) ──
    clarity_score: float = 0.0
    goal_fit_score: float = 0.0
    risk_score: float = 0.0  # lower risk = better
    actionability_score: float = 0.0
    style_fit_score: float = 0.0
    confidence_estimate: float = 0.0
    groundedness_score: float = 0.0
    token_cost: int = 0
    # ── Qualitative analysis ──
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    # ── Aggregate computed score ──
    aggregate_score: float = 0.0


@dataclass
class ResponseSynthesisResult:
    """Final outcome of the deliberation process."""

    winner_variant_id: str = ""
    winner_variant_type: str = ""
    used_variants: list[str] = field(default_factory=list)
    dropped_variants: list[str] = field(default_factory=list)
    final_response: str = ""
    synthesis_reason: str = ""
    aggregate_pros: list[str] = field(default_factory=list)
    aggregate_cons: list[str] = field(default_factory=list)
    synthesis_confidence: float = 0.0
    synthesis_risk: float = 0.0
    candidates_evaluated: int = 0
    synthesis_duration_ms: float = 0.0


# ── Trigger thresholds ────────────────────────────────────────────────────

# Strategy confidence below this → deliberation kicks in
CONFIDENCE_GREY_ZONE_UPPER = 0.72
# Simulation risk at or above this → deliberation kicks in
SIMULATION_RISK_THRESHOLD = 0.55
# Minimum response length (chars) to consider deliberation worthwhile
MIN_RESPONSE_LENGTH = 80


# ── Engine ────────────────────────────────────────────────────────────────


class ResponseVariantsEngine:
    """Tri-response deliberation engine with real execution impact."""

    # ── 1) Trigger decision ───────────────────────────────────────────

    @staticmethod
    def should_run_variants(
        *,
        decision_core: dict[str, Any],
        blocker_verdict: Any | None = None,
        response_text: str = "",
        deliberation_history: dict[str, Any] | None = None,
    ) -> tuple[bool, list[str]]:
        """Determine if deliberation should run. Returns (should_run, reason_codes).

        ``deliberation_history`` comes from the experience read-path and contains
        execution-driving signals from past deliberation outcomes. When present,
        it modulates the trigger decision via:
          - should_suppress_deliberation: skip even if triggers present
          - should_force_deliberation: trigger even without standard reasons
          - deliberation_trigger_bias: shift the confidence grey-zone threshold
        """

        reasons: list[str] = []
        strategy = str(decision_core.get("selected_strategy", "instant"))
        dh = deliberation_history or {}

        # Never deliberate on instant strategy — it's fast-path by design
        if strategy == "instant":
            return False, []

        if not decision_core.get("escalation_use_reasoning"):
            return False, ["escalation_reasoning_disabled"]

        # ── Experience-driven suppression ──
        if dh.get("should_suppress_deliberation"):
            return False, ["experience_suppressed"]

        # Adjust confidence threshold based on trigger bias from history
        trigger_bias = float(dh.get("deliberation_trigger_bias") or 0.0)
        adjusted_confidence_threshold = CONFIDENCE_GREY_ZONE_UPPER + trigger_bias

        # Grey-zone confidence (threshold adjusted by historical trigger bias)
        confidence = decision_core.get("strategy_confidence")
        if confidence is not None and float(confidence) < adjusted_confidence_threshold:
            reasons.append("grey_zone_confidence")

        # Blocker caution (active but not hard)
        if blocker_verdict is not None:
            bv = blocker_verdict
            blocker_active = (
                getattr(bv, "blocker_active", False)
                if not isinstance(bv, dict)
                else bv.get("blocker_active", False)
            )
            hard = (
                getattr(bv, "hard", False)
                if not isinstance(bv, dict)
                else bv.get("hard", False)
            )
            resolution = (
                getattr(bv, "resolution", "allow")
                if not isinstance(bv, dict)
                else bv.get("resolution", "allow")
            )
            if (
                blocker_active
                and not hard
                and resolution in ("caution_pass", "downgrade")
            ):
                reasons.append("blocker_caution_active")

        # Simulation risk
        sim_risk = decision_core.get("simulation_risk_summary", "")
        sim_ran = decision_core.get("simulation_ran", False)
        if sim_ran and sim_risk:
            # Parse risk from summary if numeric, else use heuristic
            risk_val = _extract_risk_value(sim_risk)
            if risk_val >= SIMULATION_RISK_THRESHOLD:
                reasons.append("simulation_risk_elevated")

        # Consistency conflict or experience caution
        consistency = decision_core.get("consistency_classification")
        if consistency in ("conflict", "revision"):
            reasons.append("consistency_conflict")

        exp_blocker = decision_core.get("experience_blocker_reason")
        if exp_blocker:
            reasons.append("experience_caution")

        # Response too short for deliberation — skip even if triggers present
        if len(response_text.strip()) < MIN_RESPONSE_LENGTH and len(reasons) == 0:
            return False, []

        # Handoff uncertainty (considered but not decided)
        handoff_bias = float(decision_core.get("policy_handoff_bias") or 0.0)
        exp_handoff_bias = float(decision_core.get("experience_handoff_bias") or 0.0)
        combined_handoff = handoff_bias + exp_handoff_bias
        if 0.15 < combined_handoff < 0.65:
            reasons.append("handoff_uncertainty")

        # ── Experience-driven force-trigger ──
        if not reasons and dh.get("should_force_deliberation"):
            reasons.append("experience_forced")

        return len(reasons) > 0, reasons

    # ── 2) Build variant specifications ───────────────────────────────

    @staticmethod
    def build_variant_specs(
        *,
        original_messages: list[dict[str, Any]],
        decision_core: dict[str, Any],
        original_response: str,
    ) -> list[dict[str, Any]]:
        """Build 3 variant prompt specs: direct, contextual, actionable.

        Each spec is a dict with keys: variant_type, system_prefix, messages.
        The messages reuse the original conversation context, only modifying
        the system prompt to steer the variant archetype.
        """
        specs = []
        strategy = str(decision_core.get("selected_strategy", "contextual"))

        for vtype in VARIANT_TYPES:
            prefix = VARIANT_SYSTEM_PREFIXES[vtype]
            # Add strategy context to the prefix
            strategy_hint = (
                f" Aktualna strategia: {strategy}. "
                f"Oryginalna odpowiedź istnieje — Twoim zadaniem jest wygenerować "
                f"lepszą wersję w stylu '{vtype}'."
            )

            modified_messages = list(original_messages)  # shallow copy
            # Prepend variant-specific system instruction
            if modified_messages and modified_messages[0].get("role") == "system":
                modified_messages[0] = {
                    **modified_messages[0],
                    "content": prefix
                    + strategy_hint
                    + "\n\n"
                    + modified_messages[0].get("content", ""),
                }
            else:
                modified_messages.insert(
                    0,
                    {
                        "role": "system",
                        "content": prefix + strategy_hint,
                    },
                )

            specs.append(
                {
                    "variant_type": vtype,
                    "system_prefix": prefix,
                    "messages": modified_messages,
                }
            )

        return specs

    # ── 3) Generate candidates (provider-backed) ──────────────────────

    @staticmethod
    async def generate_candidates(
        *,
        variant_specs: list[dict[str, Any]],
        provider_call_fn: Any,
        original_response: str,
    ) -> list[ResponseCandidate]:
        """Generate variant responses using the provided call function.

        provider_call_fn signature: async (messages=list[ChatMessage], tools=[]) -> ModelResponse

        Uses a single batched JSON call when possible, falls back to
        individual calls if the provider doesn't support structured output.
        """
        from aihub.chat_contracts import ChatMessage

        candidates: list[ResponseCandidate] = []

        for spec in variant_specs:
            vtype = spec["variant_type"]
            vid = _make_variant_id(vtype)

            try:
                # Build ChatMessage list from spec dicts
                messages = [
                    ChatMessage(
                        role=m.get("role", "user"),
                        content=m.get("content", ""),
                        name=m.get("name"),
                        tool_call_id=m.get("tool_call_id"),
                    )
                    for m in spec["messages"]
                ]

                model_response = await provider_call_fn(messages=messages, tools=[])
                text = model_response.content or ""
                usage = model_response.usage
                token_cost = usage.total_tokens if usage else 0

                candidates.append(
                    ResponseCandidate(
                        variant_id=vid,
                        variant_type=vtype,
                        text=text,
                        token_cost=token_cost,
                    )
                )

            except Exception:
                logger.warning("Variant generation failed for %s", vtype, exc_info=True)
                # If a variant fails, keep the original response as that variant
                candidates.append(
                    ResponseCandidate(
                        variant_id=vid,
                        variant_type=vtype,
                        text=original_response,
                        token_cost=0,
                        cons=["generation_failed_used_original"],
                    )
                )

        return candidates

    # ── 4) Evaluate candidates ────────────────────────────────────────

    @staticmethod
    def evaluate_candidates(
        candidates: list[ResponseCandidate],
        *,
        decision_core: dict[str, Any],
        original_response: str,
        variant_preference_weights: dict[str, float] | None = None,
    ) -> list[ResponseCandidate]:
        """Score each candidate across quality axes and compute aggregate.

        Scoring is heuristic-based (no LLM call), using text features
        and decision_core context to produce deterministic, reproducible scores.

        ``variant_preference_weights`` (from deliberation history) biases the
        final aggregate score per variant type: {direct: 1.05, contextual: 0.95, ...}.
        Range [0.80, 1.20]. Applied multiplicatively after raw scoring.
        """
        strategy = str(decision_core.get("selected_strategy", "contextual"))
        confidence = float(decision_core.get("strategy_confidence") or 0.5)
        has_tools = bool(decision_core.get("simulation_ran", False))
        vpw = variant_preference_weights or {}

        for c in candidates:
            text = c.text.strip()
            if not text:
                c.aggregate_score = 0.0
                c.cons.append("empty_response")
                continue

            # ── Clarity: sentence structure, punctuation, readability ──
            sentences = [s.strip() for s in re.split(r"[.!?]\s+", text) if s.strip()]
            word_count = len(text.split())
            c.clarity_score = min(
                1.0,
                max(
                    0.1,
                    0.6  # baseline
                    + (0.15 if 20 < word_count < 500 else 0.0)  # sweet spot length
                    + (0.1 if len(sentences) >= 2 else 0.0)  # multi-sentence
                    + (
                        0.15 if not text.startswith("  ") else 0.0
                    ),  # no leading whitespace
                ),
            )

            # ── Goal-fit: strategy alignment ──
            strategy_bonus = {
                "direct": {
                    "instant": 0.2,
                    "contextual": 0.1,
                    "research": 0.0,
                    "agentic": -0.05,
                },
                "contextual": {
                    "instant": -0.05,
                    "contextual": 0.2,
                    "research": 0.15,
                    "agentic": 0.1,
                },
                "actionable": {
                    "instant": 0.0,
                    "contextual": 0.05,
                    "research": 0.1,
                    "agentic": 0.2,
                },
            }
            sb = strategy_bonus.get(c.variant_type, {}).get(strategy, 0.0)
            c.goal_fit_score = min(1.0, max(0.1, 0.55 + sb + (confidence * 0.15)))

            # ── Risk: hallucination proxy ──
            # Long unsupported claims, excessive superlatives, certainty words on uncertain topics
            certainty_words = len(
                re.findall(
                    r"\b(na pewno|zdecydowanie|zawsze|nigdy|certainly|absolutely|always|never)\b",
                    text,
                    re.IGNORECASE,
                )
            )
            c.risk_score = min(
                1.0,
                max(
                    0.0,
                    0.15
                    + certainty_words * 0.08
                    + (0.1 if word_count > 400 and not has_tools else 0.0),
                ),
            )

            # ── Actionability: steps, code, lists ──
            has_list = bool(re.search(r"^\s*[-•*\d]+[.)]\s", text, re.MULTILINE))
            has_code = bool(re.search(r"```", text))
            has_steps = bool(re.search(r"\b(krok|step)\s*\d", text, re.IGNORECASE))
            c.actionability_score = min(
                1.0,
                max(
                    0.1,
                    0.3
                    + (0.25 if has_list else 0.0)
                    + (0.2 if has_code else 0.0)
                    + (0.15 if has_steps else 0.0)
                    + (0.1 if c.variant_type == "actionable" else 0.0),
                ),
            )

            # ── Style-fit: variant type alignment ──
            c.style_fit_score = _compute_style_fit(c.variant_type, text)

            # ── Groundedness: tool/data backing ──
            c.groundedness_score = min(
                1.0,
                max(
                    0.1,
                    0.5
                    + (0.2 if has_tools else 0.0)
                    + (0.15 if has_code or has_list else 0.0)
                    - (certainty_words * 0.05),
                ),
            )

            # ── Confidence estimate ──
            c.confidence_estimate = min(
                1.0,
                max(
                    0.1,
                    (
                        c.clarity_score * 0.25
                        + c.goal_fit_score * 0.25
                        + (1.0 - c.risk_score) * 0.2
                        + c.actionability_score * 0.15
                        + c.style_fit_score * 0.15
                    ),
                ),
            )

            # ── Pros/Cons ──
            if c.clarity_score >= 0.7:
                c.pros.append("clear_structure")
            if c.goal_fit_score >= 0.65:
                c.pros.append("goal_aligned")
            if c.actionability_score >= 0.5:
                c.pros.append("actionable_content")
            if c.risk_score >= 0.35:
                c.cons.append("elevated_risk")
            if word_count < 15:
                c.cons.append("too_brief")
            if word_count > 500:
                c.cons.append("verbose")

            # ── Aggregate score (weighted) ──
            raw_aggregate = min(
                1.0,
                max(
                    0.0,
                    c.clarity_score * 0.20
                    + c.goal_fit_score * 0.25
                    + (1.0 - c.risk_score) * 0.20
                    + c.actionability_score * 0.15
                    + c.style_fit_score * 0.10
                    + c.groundedness_score * 0.10,
                ),
            )

            # Apply historical variant preference weight (execution-driving bias)
            pref_weight = float(vpw.get(c.variant_type, 1.0))
            pref_weight = max(0.80, min(1.20, pref_weight))
            c.aggregate_score = min(1.0, max(0.0, raw_aggregate * pref_weight))

            if pref_weight != 1.0:
                if pref_weight > 1.0:
                    c.pros.append("historically_preferred")
                else:
                    c.cons.append("historically_underperforming")

        return candidates

    # ── 5) Synthesize final response ──────────────────────────────────

    @staticmethod
    def synthesize_final_response(
        candidates: list[ResponseCandidate],
        *,
        original_response: str,
    ) -> ResponseSynthesisResult:
        """Merge winner with strong fragments from other candidates.

        This is NOT a blind winner-take-all. The synthesis:
        1. Picks the highest-scoring candidate as the base
        2. Scans other candidates for unique strong paragraphs
        3. Appends non-redundant value from others to the base
        """
        started = time.monotonic()

        if not candidates:
            return ResponseSynthesisResult(
                final_response=original_response,
                synthesis_reason="no_candidates",
                synthesis_confidence=0.3,
            )

        # Sort by aggregate score descending
        ranked = sorted(candidates, key=lambda c: c.aggregate_score, reverse=True)
        winner = ranked[0]
        losers = ranked[1:]

        # Start with winner text
        final_text = winner.text.strip()
        used = [winner.variant_id]
        dropped = []
        all_pros = list(winner.pros)
        all_cons = list(winner.cons)

        # Extract unique strong paragraphs from losers
        winner_tokens = _tokenize_text(final_text)

        for loser in losers:
            if loser.aggregate_score < 0.25:
                dropped.append(loser.variant_id)
                all_cons.extend(loser.cons)
                continue

            # Find paragraphs in loser that add unique value
            loser_paragraphs = [
                p.strip() for p in loser.text.split("\n\n") if p.strip()
            ]
            merged_any = False

            for para in loser_paragraphs:
                para_tokens = _tokenize_text(para)
                # Paragraph must bring ≥30% new tokens to be worth merging
                if not para_tokens:
                    continue
                overlap = len(para_tokens & winner_tokens) / len(para_tokens)
                if overlap < 0.70 and len(para) > 30:
                    # This paragraph adds unique value
                    final_text += "\n\n" + para
                    winner_tokens.update(para_tokens)
                    merged_any = True

            if merged_any:
                used.append(loser.variant_id)
                all_pros.extend(loser.pros)
            else:
                dropped.append(loser.variant_id)

            all_cons.extend(loser.cons)

        # Deduplicate pros/cons
        all_pros = list(dict.fromkeys(all_pros))
        all_cons = list(dict.fromkeys(all_cons))

        duration_ms = (time.monotonic() - started) * 1000.0

        return ResponseSynthesisResult(
            winner_variant_id=winner.variant_id,
            winner_variant_type=winner.variant_type,
            used_variants=used,
            dropped_variants=dropped,
            final_response=final_text,
            synthesis_reason=_build_synthesis_reason(winner, losers, used),
            aggregate_pros=all_pros,
            aggregate_cons=all_cons,
            synthesis_confidence=winner.aggregate_score,
            synthesis_risk=winner.risk_score,
            candidates_evaluated=len(candidates),
            synthesis_duration_ms=duration_ms,
        )

    # ── 6) Full deliberation pipeline ─────────────────────────────────

    @classmethod
    async def run_deliberation(
        cls,
        *,
        decision_core: dict[str, Any],
        blocker_verdict: Any | None,
        original_response: str,
        original_messages: list[dict[str, Any]],
        provider_call_fn: Any,
        deliberation_history: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Full pipeline: trigger → generate → evaluate → synthesize.

        Returns (final_response_text, deliberation_metadata).
        If deliberation is not triggered, returns (original_response, {}).

        ``deliberation_history`` is the execution-driving signal from past
        experience, produced by _extract_deliberation_history(). When present:
          - Trigger decision uses should_suppress/should_force + trigger_bias
          - Candidate evaluation uses variant_preference_weights
        """
        dh = deliberation_history or {}
        should_run, reason_codes = cls.should_run_variants(
            decision_core=decision_core,
            blocker_verdict=blocker_verdict,
            response_text=original_response,
            deliberation_history=dh,
        )

        if not should_run:
            return original_response, {
                "response_variants_triggered": False,
                "response_variants_count": 0,
                "response_variants_reason_codes": [],
            }

        started = time.monotonic()
        engine = cls()

        # Build variant prompts
        variant_specs = engine.build_variant_specs(
            original_messages=original_messages,
            decision_core=decision_core,
            original_response=original_response,
        )

        # Generate variant responses
        candidates = await engine.generate_candidates(
            variant_specs=variant_specs,
            provider_call_fn=provider_call_fn,
            original_response=original_response,
        )

        # Evaluate candidates (with historical variant preference weights)
        vpw = dh.get("variant_preference_weights") or {}
        candidates = engine.evaluate_candidates(
            candidates,
            decision_core=decision_core,
            original_response=original_response,
            variant_preference_weights=vpw if vpw else None,
        )

        # Synthesize final response
        synthesis = engine.synthesize_final_response(
            candidates,
            original_response=original_response,
        )

        duration_ms = (time.monotonic() - started) * 1000.0

        # Build metadata for trace and experience write-back
        metadata: dict[str, Any] = {
            "response_variants_triggered": True,
            "response_variants_count": len(candidates),
            "response_variants_reason_codes": reason_codes,
            "response_variants_winner": synthesis.winner_variant_id,
            "response_variants_winner_type": synthesis.winner_variant_type,
            "response_variants_types": [c.variant_type for c in candidates],
            "response_variants_synthesis_used": synthesis.used_variants,
            "response_variants_dropped": synthesis.dropped_variants,
            "response_variants_confidence": round(synthesis.synthesis_confidence, 3),
            "response_variants_risk": round(synthesis.synthesis_risk, 3),
            "response_variants_summary": synthesis.synthesis_reason,
            "response_variants_duration_ms": round(duration_ms, 1),
            # Detailed per-candidate scores (for DEV view)
            "response_variants_scores": [
                {
                    "variant_id": c.variant_id,
                    "variant_type": c.variant_type,
                    "aggregate_score": round(c.aggregate_score, 3),
                    "clarity": round(c.clarity_score, 3),
                    "goal_fit": round(c.goal_fit_score, 3),
                    "risk": round(c.risk_score, 3),
                    "actionability": round(c.actionability_score, 3),
                    "style_fit": round(c.style_fit_score, 3),
                    "groundedness": round(c.groundedness_score, 3),
                    "confidence": round(c.confidence_estimate, 3),
                    "token_cost": c.token_cost,
                    "pros": c.pros,
                    "cons": c.cons,
                }
                for c in candidates
            ],
            # Experience write-back fields
            "response_variants_aggregate_pros": synthesis.aggregate_pros,
            "response_variants_aggregate_cons": synthesis.aggregate_cons,
            "response_variants_winner_scores": {
                "aggregate": round(synthesis.synthesis_confidence, 3),
                "risk": round(synthesis.synthesis_risk, 3),
            },
        }

        logger.info(
            "Deliberation complete: winner=%s confidence=%.2f risk=%.2f duration=%.0fms candidates=%d",
            synthesis.winner_variant_type,
            synthesis.synthesis_confidence,
            synthesis.synthesis_risk,
            duration_ms,
            len(candidates),
        )

        return synthesis.final_response, metadata


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_variant_id(variant_type: str) -> str:
    """Create unique variant ID."""
    ts = str(time.monotonic()).encode()
    h = hashlib.md5(ts + variant_type.encode(), usedforsecurity=False).hexdigest()[:8]
    return f"rv_{variant_type}_{h}"


def _tokenize_text(text: str) -> set[str]:
    """Simple word-level tokenization for overlap detection."""
    return set(re.findall(r"[\wąćęłńóśźż]{3,}", text.lower()))


def _extract_risk_value(risk_summary: str) -> float:
    """Extract numeric risk from summary string, with heuristic fallback."""
    if not risk_summary:
        return 0.0
    # Try to find a float
    m = re.search(r"(\d+\.?\d*)", risk_summary)
    if m:
        val = float(m.group(1))
        # Normalize to 0-1 if needed
        return val / 100.0 if val > 1.0 else val
    # Heuristic: keywords
    low_words = ("low", "niski", "minimal")
    high_words = ("high", "wysoki", "elevated", "critical")
    s = risk_summary.lower()
    if any(w in s for w in high_words):
        return 0.7
    if any(w in s for w in low_words):
        return 0.2
    return 0.35


def _compute_style_fit(variant_type: str, text: str) -> float:
    """Score how well the text fits the variant archetype."""
    word_count = len(text.split())
    has_list = bool(re.search(r"^\s*[-•*\d]+[.)]\s", text, re.MULTILINE))
    has_code = bool(re.search(r"```", text))

    if variant_type == "direct":
        # Direct should be concise
        if word_count < 60:
            return 0.85
        if word_count < 120:
            return 0.65
        return 0.4

    if variant_type == "contextual":
        # Contextual should be detailed
        if word_count > 100:
            return 0.8
        if word_count > 40:
            return 0.6
        return 0.35

    if variant_type == "actionable":
        # Actionable should have structure
        score = 0.4
        if has_list:
            score += 0.25
        if has_code:
            score += 0.15
        if word_count > 30:
            score += 0.1
        return min(1.0, score)

    return 0.5


def _build_synthesis_reason(
    winner: ResponseCandidate,
    losers: list[ResponseCandidate],
    used: list[str],
) -> str:
    """Build human-readable synthesis explanation."""
    parts = [f"Winner: {winner.variant_type} (score={winner.aggregate_score:.2f})"]
    merged_count = len(used) - 1
    if merged_count > 0:
        merged_types = [l.variant_type for l in losers if l.variant_id in used]
        parts.append(f"Merged fragments from: {', '.join(merged_types)}")
    if losers:
        drop_types = [l.variant_type for l in losers if l.variant_id not in used]
        if drop_types:
            parts.append(f"Dropped: {', '.join(drop_types)}")
    return ". ".join(parts)
