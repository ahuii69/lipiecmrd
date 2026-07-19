"""Adaptive learning engine: outcome, delayed feedback, causal, lessons, calibration."""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from aihub.adaptive_learning import store
from aihub.adaptive_learning.actions import apply_machine_actions, map_attribution_to_machine_action
from aihub.adaptive_learning.models import (
    CausalAttribution,
    ConfidenceCalibration,
    DelayedFeedbackEvent,
    FailurePattern,
    LearnedLesson,
    LearningTurnResult,
    LongHorizonTask,
    RuntimeSelfModel,
    SuccessPattern,
    TraitObservation,
    TurnOutcomeEvaluation,
    UserModelV2,
)

log = logging.getLogger(__name__)

# Anti-drift caps
_MAX_BIAS_DELTA_PER_TURN = 0.04
_MIN_SAMPLES_STRONG_BIAS = 5
_GLOBAL_LESSON_MIN_CONF = 0.55

_POS_FB = re.compile(
    r"(?iu)(?<!nie\s)(tak[,]?\s*dokładnie|właśnie\s+o\s+to(?!\s+chodzi)|pasuje|(?<!nie\s)działa|"
    r"teraz\s+jest\s+dobrze|super\s+odpowiedź|dokładnie\s+tak|"
    r"(?<![a-ząęćłńóśźżA-ZĄĘĆŁŃÓŚŹŻ])o\s+to\s+chodzi\w*|akceptuję|ok\s+tak)"
)
_NEG_FB = re.compile(
    r"(?iu)((?<![a-ząęćłńóśźżA-ZĄĘĆŁŃÓŚŹŻ])nie\s+o\s+to\s+chodzi\w*|"
    r"chodziło\s+mi\s+o|chodzilo\s+mi\s+o|miał[ea]m\s+na\s+myśli|"
    r"dalej\s+jest\s+źle|(?<![a-ząęćłńóśźżA-ZĄĘĆŁŃÓŚŹŻ])źle\b|"
    r"(?<![a-ząęćłńóśźżA-ZĄĘĆŁŃÓŚŹŻ])nie\s+to\b|"
    r"po\s+chuj\s+zrobiłe[sś]|(?<![a-ząęćłńóśźżA-ZĄĘĆŁŃÓŚŹŻ])nie\s+działa|"
    r"odrzu[cć]|odrzucam|kiepsko)"
)
_CONT_FB = re.compile(r"(?iu)\b(dalej|kontynuuj|następny\s+krok|lecimy)\b")


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def evaluate_turn_outcome(
    *,
    turn_id: str,
    user_id: str,
    session_id: str,
    message: str,
    response_text: str,
    trace: dict[str, Any],
    decision_core: dict[str, Any] | None = None,
    ok: bool = True,
    errors: list[dict[str, Any]] | None = None,
) -> TurnOutcomeEvaluation:
    dc = decision_core or {}
    tr = trace or {}
    errors = errors or []
    codes: list[str] = ["OUTCOME_EVALUATED"]

    critic = tr.get("response_critic_score")
    try:
        critic_f = float(critic) / 100.0 if critic is not None and float(critic) > 1.5 else (
            float(critic) if critic is not None else None
        )
    except Exception:
        critic_f = None

    tools_ok = int(tr.get("tool_calls_successful") or 0)
    tools_fail = int(tr.get("tool_failures") or 0)
    tools_total = tools_ok + tools_fail
    tool_score = 0.5 if tools_total == 0 else tools_ok / max(1, tools_total)

    web_used = bool(tr.get("controlled_web_triggered") and tr.get("controlled_web_ok"))
    web_req = str(dc.get("web_decision") or tr.get("controlled_web_decision") or "off") == "required"
    research_q = 0.5
    if web_req and web_used:
        research_q = 0.8 if tr.get("controlled_web_has_results") else 0.35
        codes.append("OUTCOME_RESEARCH_USED")
    elif web_req and not web_used:
        research_q = 0.2
        codes.append("OUTCOME_RESEARCH_MISS")

    duration = float(tr.get("duration_ms") or 0.0)
    if duration <= 0:
        latency_score = 0.5
    elif duration < 1500:
        latency_score = 0.9
    elif duration < 4000:
        latency_score = 0.7
    elif duration < 9000:
        latency_score = 0.45
    else:
        latency_score = 0.25

    grounding = 0.75 if str(tr.get("response_grounding_mode") or "") in (
        "tools_verified",
        "web_verified",
        "prefetch_verified_in_thread",
    ) else 0.45
    if tr.get("used_fallback"):
        grounding = min(grounding, 0.25)
        codes.append("OUTCOME_FALLBACK")
    if errors:
        grounding = min(grounding, 0.3)
        codes.append("OUTCOME_HAS_ERRORS")

    intent = "unknown"
    if tr.get("primary_intent"):
        intent = str(tr.get("primary_intent"))
    elif isinstance(tr.get("intent_ranking"), list) and tr.get("intent_ranking"):
        intent = str((tr.get("intent_ranking") or [{}])[0].get("label") or "unknown")
    elif dc.get("primary_intent"):
        intent = str(dc.get("primary_intent"))
    if intent == "unknown":
        intent = str(tr.get("pragmatics_primary_intent") or "unknown")

    intent_match = 0.55
    if critic_f is not None:
        intent_match = _clamp(0.35 + critic_f * 0.6)
    if ok and not tr.get("used_fallback"):
        intent_match = max(intent_match, 0.6)

    style_match = 0.55
    um_len = str(tr.get("user_model_length") or "")
    resp_n = len(response_text or "")
    if um_len == "short" and resp_n > 0:
        style_match = 0.8 if resp_n < 900 else 0.35
        codes.append("OUTCOME_STYLE_LENGTH_CHECK")
    verbosity_match = style_match

    correction_sig = 1.0 if tr.get("correction_detected") or (
        isinstance(tr.get("reason_codes"), list) and any(
            "CORRECTION" in str(c) for c in tr.get("reason_codes") or []
        )
    ) else 0.0
    rejection_sig = 0.0
    acceptance_sig = 0.0
    continuation_sig = 0.0
    satisfaction = 0.0
    if ok and critic_f is not None and critic_f >= 0.75 and not correction_sig:
        satisfaction = 0.35
        acceptance_sig = 0.25
    if ok and not errors and not tr.get("used_fallback"):
        satisfaction = max(satisfaction, 0.2)

    quality = _clamp(
        0.25 * grounding
        + 0.2 * intent_match
        + 0.15 * style_match
        + 0.15 * tool_score
        + 0.1 * research_q
        + 0.1 * (critic_f if critic_f is not None else 0.5)
        + 0.05 * latency_score
    )
    if not ok:
        quality = min(quality, 0.35)
        codes.append("OUTCOME_NOT_OK")

    # Prefer continuous self-eval when present on the trace.
    cse = tr.get("continuous_self_eval") if isinstance(tr.get("continuous_self_eval"), dict) else {}
    mem_u = float(cse.get("memory_usefulness") or tr.get("memory_usefulness") or 0.55)
    _planner_default = 0.55 if bool(dc.get("planner_recommended") or tr.get("planner_executed")) else 0.5
    planner_u = float(cse.get("planner_usefulness") if cse.get("planner_usefulness") is not None else _planner_default)
    tool_u = float(cse.get("tool_usefulness") if cse.get("tool_usefulness") is not None else tool_score)
    token_eff = float(cse.get("token_efficiency") or 0.5)
    if cse:
        quality = _clamp(0.7 * quality + 0.3 * float(cse.get("overall_quality") or quality))
        codes.append("OUTCOME_CSE_MERGED")

    reward = _clamp(
        quality
        + 0.15 * acceptance_sig
        + 0.1 * continuation_sig
        + 0.15 * satisfaction
        - 0.35 * correction_sig
        - 0.25 * rejection_sig
        - (0.15 if tr.get("used_fallback") else 0.0)
        + 0.05 * (token_eff - 0.5)
    , -1.0, 1.0)

    conf = _clamp(0.35 + (0.2 if tools_total or web_used else 0.1) + (0.15 if critic_f is not None else 0.0))
    if cse.get("confidence_calibration") is not None:
        conf = _clamp(0.5 * conf + 0.5 * float(cse["confidence_calibration"]))

    return TurnOutcomeEvaluation(
        turn_id=turn_id,
        user_id=user_id,
        session_id=session_id,
        request_id=str(tr.get("request_id") or dc.get("request_id") or ""),
        correlation_id=str(tr.get("correlation_id") or dc.get("correlation_id") or ""),
        runtime_mode=str(tr.get("runtime_mode") or "live"),
        primary_intent=intent,
        intent_confidence=float(dc.get("intent_confidence") or tr.get("intent_confidence") or 0.5),
        ambiguity_score=float(dc.get("cognitive_ambiguity") or tr.get("ambiguity_score") or 0.0),
        conversation_state=str(dc.get("conversation_state") or tr.get("conversation_state") or ""),
        selected_strategy=str(dc.get("selected_strategy") or tr.get("selected_strategy") or "contextual"),
        strategy_confidence=float(dc.get("strategy_confidence") or 0.5),
        planner_used=bool(dc.get("planner_recommended") or tr.get("planner_executed") or dc.get("escalation_use_reasoning")),
        reasoning_used=bool(dc.get("escalation_use_reasoning") or tr.get("reasoning_executed")),
        web_used=web_used,
        tools_used=tools_total > 0 or bool(tr.get("used_tools")),
        provider_used=str(tr.get("provider") or ""),
        provider_fallback_used=bool(tr.get("used_fallback")),
        critic_score=critic_f,
        critic_revision_happened=bool(tr.get("response_revision_happened")),
        response_critic_score=float(critic) if critic is not None else None,
        final_response_quality=quality,
        user_satisfaction_signal=satisfaction,
        immediate_user_signal=satisfaction,
        correction_signal=correction_sig,
        rejection_signal=rejection_sig,
        continuation_signal=continuation_sig,
        acceptance_signal=acceptance_sig,
        correction_detected=bool(correction_sig >= 0.5),
        rejection_detected=bool(rejection_sig >= 0.5),
        acceptance_detected=bool(acceptance_sig >= 0.5),
        continuation_detected=bool(continuation_sig >= 0.5),
        task_completion_signal=0.0,
        factual_grounding_score=grounding,
        style_match_score=style_match,
        intent_match_score=intent_match,
        verbosity_match_score=verbosity_match,
        memory_usefulness_score=mem_u,
        psyche_alignment_score=0.55,
        planner_quality_score=planner_u,
        tool_success_score=tool_u,
        tool_execution_score=tool_score,
        research_quality_score=research_q,
        latency_score=latency_score,
        cost_score=token_eff,
        overall_reward=reward,
        confidence=conf,
        reason_codes=codes,
        degraded=bool(tr.get("degraded") or tr.get("learning_degraded")),
        created_at=time.time(),
        message_preview=(message or "")[:240],
        response_preview=(response_text or "")[:240],
        metadata={
            "route_reason": tr.get("route_reason"),
            "provider": tr.get("provider"),
            "model": tr.get("model"),
            "request_id": tr.get("request_id") or dc.get("request_id"),
            "correlation_id": tr.get("correlation_id") or dc.get("correlation_id"),
            "critic_revision_happened": bool(tr.get("response_revision_happened")),
            "memory_usefulness_score": mem_u,
            "psyche_alignment_score": 0.55,
            "continuous_self_eval": cse or None,
            "hallucination_risk": cse.get("hallucination_risk"),
            "token_efficiency": token_eff,
            "answer_completeness": cse.get("answer_completeness"),
        },
    )


def detect_delayed_feedback(
    *,
    message: str,
    user_id: str,
    session_id: str,
    feedback_turn_id: str,
) -> DelayedFeedbackEvent | None:
    text = (message or "").strip()
    if len(text) < 3:
        return None
    polarity = "neutral"
    kind = "generic"
    codes: list[str] = []
    conf = 0.45
    explicit = "inferred"
    if _NEG_FB.search(text):
        polarity = "negative"
        kind = "correction" if "chodzi" in text.lower() or "nie o to" in text.lower() else "rejection"
        conf = 0.8
        codes.append("DELAYED_FB_NEGATIVE")
    elif _POS_FB.search(text):
        polarity = "positive"
        kind = "acceptance"
        conf = 0.75
        codes.append("DELAYED_FB_POSITIVE")
    elif _CONT_FB.search(text) and len(text.split()) <= 6:
        polarity = "positive"
        kind = "continuation"
        conf = 0.55
        codes.append("DELAYED_FB_CONTINUATION")
    else:
        return None

    recent = store.list_recent_outcomes(user_id=user_id, session_id=session_id, limit=12)
    candidates = [o for o in recent if o.turn_id != feedback_turn_id]
    if not candidates:
        return None

    lower = text.lower()
    first_ref = bool(
        re.search(
            r"(?iu)\b(pierwsz[yaei]|najpierw|ten\s+pierwszy\s+sposób|pierwsza\s+odpowiedź)\b",
            lower,
        )
    )
    prev_ref = bool(
        re.search(
            r"(?iu)\b(wcześniej|poprzedni|tamten|tamtą|tamta|to\s+co\s+napisałeś|wcześniejsz\w+)\b",
            lower,
        )
    )
    worked_ref = bool(re.search(r"(?iu)\b(zadziałał\w*|działał\w*|ten\s+sposób)\b", lower))
    if first_ref or prev_ref:
        explicit = "explicit"
        codes.append("DELAYED_FB_EXPLICIT_REF")
        conf = min(0.95, conf + 0.1)

    task = store.get_active_long_horizon_task(user_id=user_id, session_id=session_id)
    task_tokens: set[str] = set()
    if task is not None:
        blob = f"{task.title} {task.objective} {' '.join(task.accepted_decisions)}".lower()
        task_tokens = set(re.findall(r"[a-ząęćłńóśźż]{4,}", blob))

    ranked: list[tuple[float, Any]] = []
    n = len(candidates)
    for idx, o in enumerate(candidates):
        recency = 1.0 - (idx / max(1, n))
        score = 0.25 * recency
        prev = (o.message_preview or "").lower()
        resp = (o.response_preview or "").lower()
        shared = set(re.findall(r"[a-ząęćłńóśźż]{4,}", lower)) & set(
            re.findall(r"[a-ząęćłńóśźż]{4,}", prev + " " + resp)
        )
        score += min(0.45, 0.09 * len(shared))
        if o.primary_intent and o.primary_intent.replace("_", " ") in lower:
            score += 0.12
        if task_tokens:
            o_tok = set(re.findall(r"[a-ząęćłńóśźż]{4,}", prev))
            score += min(0.2, 0.05 * len(task_tokens & o_tok))
        if first_ref:
            score += 0.35 * (idx / max(1, n - 1)) if n > 1 else 0.35
            codes.append("DELAYED_FB_FIRST_REF")
        elif prev_ref and not worked_ref:
            score += 0.15 if idx >= 1 else 0.05
        elif worked_ref and not first_ref and shared:
            score += 0.2
        ranked.append((score, o))

    ranked.sort(key=lambda x: x[0], reverse=True)
    best_score, best = ranked[0]
    if best_score < 0.35 and not first_ref and not prev_ref:
        best = candidates[0]
        conf = min(conf, 0.55)
        codes.append("DELAYED_FB_DEFAULT_RECENT")
    elif best_score < 0.45:
        conf = min(conf, 0.55)
        codes.append("DELAYED_FB_LOW_TOPIC_OVERLAP")
    else:
        codes.append("DELAYED_FB_TARGET_RESOLVED")

    dims: list[str] = []
    if kind in ("correction", "rejection"):
        dims.extend(["intent_match", "strategy"])
    if polarity == "positive":
        dims.append("acceptance")

    return DelayedFeedbackEvent(
        feedback_id=str(uuid.uuid4()),
        feedback_turn_id=feedback_turn_id,
        target_turn_id=best.turn_id,
        user_id=user_id,
        session_id=session_id,
        feedback_type=kind,
        polarity=polarity,  # type: ignore[arg-type]
        kind=kind,
        confidence=conf,
        evidence=text[:200],
        affected_dimensions=dims,
        explicit_or_inferred=explicit,  # type: ignore[arg-type]
        reason_codes=list(dict.fromkeys(codes)),
        text_preview=text[:200],
        created_at=time.time(),
    )


def apply_delayed_feedback(event: DelayedFeedbackEvent) -> TurnOutcomeEvaluation | None:
    outcome = store.get_turn_outcome(event.target_turn_id)
    if outcome is None:
        return None
    if event.polarity == "positive":
        outcome.acceptance_signal = max(outcome.acceptance_signal, 0.85)
        outcome.acceptance_detected = True
        outcome.user_satisfaction_signal = max(outcome.user_satisfaction_signal, 0.8)
        outcome.delayed_user_signal = max(outcome.delayed_user_signal, 0.85)
        if event.kind == "continuation":
            outcome.continuation_signal = max(outcome.continuation_signal, 0.8)
            outcome.continuation_detected = True
        outcome.overall_reward = _clamp(outcome.overall_reward + 0.25, -1.0, 1.0)
        outcome.reason_codes = list(outcome.reason_codes) + ["DELAYED_POSITIVE_APPLIED"]
    else:
        outcome.rejection_signal = max(outcome.rejection_signal, 0.85)
        outcome.rejection_detected = True
        outcome.correction_signal = max(outcome.correction_signal, 0.7 if event.kind == "correction" else 0.4)
        outcome.correction_detected = event.kind == "correction" or outcome.correction_detected
        outcome.user_satisfaction_signal = min(outcome.user_satisfaction_signal, 0.15)
        outcome.delayed_user_signal = min(outcome.delayed_user_signal, -0.7)
        outcome.overall_reward = _clamp(outcome.overall_reward - 0.35, -1.0, 1.0)
        outcome.reason_codes = list(outcome.reason_codes) + ["DELAYED_NEGATIVE_APPLIED"]
    outcome.delayed_feedback_applied = True
    outcome.confidence = _clamp(max(outcome.confidence, event.confidence))
    outcome.updated_at = time.time()
    outcome.metadata = {
        **dict(outcome.metadata or {}),
        "delayed_feedback_turn_id": event.feedback_turn_id,
        "delayed_feedback_kind": event.kind,
        "delayed_feedback_explicit": event.explicit_or_inferred,
    }
    store.upsert_turn_outcome(outcome)
    store.insert_delayed_feedback(event)
    return outcome


def attribute_causes(
    *,
    outcome: TurnOutcomeEvaluation,
    trace: dict[str, Any],
    decision_core: dict[str, Any] | None = None,
) -> list[CausalAttribution]:
    dc = decision_core or {}
    items: list[CausalAttribution] = []
    reward = outcome.overall_reward
    sign: str = "positive" if reward >= 0.15 else ("negative" if reward <= -0.1 else "neutral")

    def add(
        factor: str,
        score: float,
        evidence: str,
        kind: str = "weakly_inferred",
        action: str = "",
    ) -> None:
        if kind == "inferred":
            kind = "strongly_inferred" if abs(score) >= 0.45 else "weakly_inferred"
        if kind == "uncertain":
            kind = "unknown"
        items.append(
            CausalAttribution(
                attribution_id=str(uuid.uuid4()),
                turn_id=outcome.turn_id,
                factor=factor,
                contribution_score=score,
                confidence=_clamp(0.35 + abs(score) * 0.4),
                evidence=evidence[:240],
                evidence_kind=kind,  # type: ignore[arg-type]
                attribution_type=kind,  # type: ignore[arg-type]
                positive_or_negative=sign if score != 0 else "neutral",  # type: ignore[arg-type]
                polarity=sign if score != 0 else "neutral",  # type: ignore[arg-type]
                corrective_action=action,
                scope="user",
            )
        )

    if outcome.correction_signal > 0.4:
        add("pragmatics interpretation", -0.55, "correction_signal present", "observed", "tighten intent ranking")
        add("intent ranking", -0.45, "primary intent likely wrong", "strongly_inferred", "escalate ambiguity")
    if outcome.web_used and outcome.research_quality_score >= 0.7 and reward > 0:
        add("web decision", 0.45, "web used with results", "observed")
        add("query rewrite", 0.3, "research path successful", "weakly_inferred")
    if outcome.web_used is False and "OUTCOME_RESEARCH_MISS" in outcome.reason_codes:
        add("web decision", -0.5, "required web missing", "observed", "prefer research strategy")
    if outcome.tools_used and outcome.tool_success_score < 0.4:
        add("tool choice", -0.5, f"tool_success={outcome.tool_success_score:.2f}", "observed", "reorder/avoid tool")
    if dc.get("selected_strategy") == "instant" and reward < 0:
        add("strategy choice", -0.4, "instant with poor reward", "strongly_inferred", "favor contextual/research")
    if outcome.planner_used and reward > 0.2:
        add("planner decision", 0.35, "planner_used with positive reward", "weakly_inferred")
    if outcome.planner_used and reward < -0.1:
        add("planner decision", -0.3, "planner_used with negative reward", "weakly_inferred", "simplify plan")
    if trace.get("response_revision_happened"):
        add("critic revision", 0.25 if reward >= 0 else -0.15, "revision happened", "observed")
    if outcome.verbosity_match_score < 0.4:
        add("verbosity", -0.35, "verbosity mismatch", "strongly_inferred", "apply user model length")
    if any("MEMORY" in str(c) for c in (trace.get("cognitive_influence_reason_codes") or [])):
        add("memory retrieval", 0.15 if reward >= 0 else -0.2, "memory influence codes present", "weakly_inferred")
    if not items:
        add("unknown mixture", 0.0, "insufficient evidence", "unknown")
    return items[:10]


def calibrate_confidence(
    *,
    raw: float,
    strategy: str,
    intent: str,
    user_id: str,
    ambiguity: float = 0.0,
    sample_hint: int | None = None,
) -> ConfidenceCalibration:
    rows = store.get_strategy_metric_rows(user_id=user_id, limit=30)
    matched = [
        r
        for r in rows
        if str(r.get("strategy") or "") == strategy
        and (not intent or not r.get("intent") or r.get("intent") == intent)
    ]
    samples = int(sum(int(r.get("samples") or 0) for r in matched)) if matched else 0
    if sample_hint is not None:
        samples = max(samples, sample_hint)
    if samples <= 0:
        cal = _clamp(raw * 0.85)
        return ConfidenceCalibration(
            raw_confidence=raw,
            calibrated_confidence=cal,
            calibration_delta=cal - raw,
            calibration_source="prior_low_n",
            calibration_sample_count=0,
        )
    success = sum(float(r.get("success_sum") or 0) for r in matched)
    alpha = 2.0
    smooth = (success + alpha) / (samples + 2 * alpha)
    blend = 0.55 * raw + 0.45 * smooth
    if ambiguity >= 0.55:
        blend -= 0.08
    if samples < _MIN_SAMPLES_STRONG_BIAS:
        blend = 0.7 * blend + 0.3 * 0.5
    cal = _clamp(blend)
    return ConfidenceCalibration(
        raw_confidence=raw,
        calibrated_confidence=cal,
        calibration_delta=cal - raw,
        calibration_source="strategy_history_smooth",
        calibration_sample_count=samples,
    )


def extract_lesson_candidates(
    *,
    outcome: TurnOutcomeEvaluation,
    attributions: list[CausalAttribution],
) -> list[LearnedLesson]:
    cands: list[LearnedLesson] = []
    ts = time.time()
    for attr in attributions:
        if abs(attr.contribution_score) < 0.35 or attr.evidence_kind in ("unknown", "uncertain"):
            continue
        if attr.attribution_type in ("unknown",) or attr.confidence < 0.4:
            continue
        if attr.evidence_kind == "weakly_inferred" and abs(attr.contribution_score) < 0.4:
            continue
        action, payload = map_attribution_to_machine_action(attr=attr, outcome=outcome)
        polarity = "avoid" if attr.contribution_score < 0 else "prefer"
        statement = (
            f"{polarity}: factor={attr.factor}; strategy={outcome.selected_strategy}; "
            f"intent={outcome.primary_intent}; action={action}"
        )
        scope = "user"
        conf = _clamp(0.35 + abs(attr.contribution_score) * 0.4)
        cands.append(
            LearnedLesson(
                lesson_id=str(uuid.uuid4()),
                user_id=outcome.user_id,
                scope=scope,  # type: ignore[arg-type]
                trigger_turn_id=outcome.turn_id,
                source_turn_ids=[outcome.turn_id],
                category=attr.factor.replace(" ", "_")[:60],
                statement=statement[:480],
                machine_action=action,
                machine_action_payload=payload,
                confidence=conf,
                evidence_count=1,
                positive_evidence_count=1 if attr.contribution_score > 0 else 0,
                negative_evidence_count=1 if attr.contribution_score < 0 else 0,
                applicable_intents=[outcome.primary_intent],
                applicable_strategies=[outcome.selected_strategy],
                success_rate=0.7 if attr.contribution_score > 0 else 0.3,
                created_at=ts,
                updated_at=ts,
                expires_at=ts + 86400 * 45,
                content_hash=store.content_hash(scope, outcome.user_id, action, statement),
            )
        )
    return cands[:6]


def update_strategy_metrics(outcome: TurnOutcomeEvaluation) -> None:
    key = f"u:{outcome.user_id}|s:{outcome.selected_strategy}|i:{outcome.primary_intent}"
    store.bump_metric(
        "strategy_metrics",
        key,
        columns={
            "user_id": outcome.user_id,
            "strategy": outcome.selected_strategy,
            "intent": outcome.primary_intent,
            "domain": "",
            "samples": 1,
            "success_sum": 1.0 if outcome.overall_reward >= 0.15 else 0.0,
            "correction_sum": float(outcome.correction_signal),
            "rejection_sum": float(outcome.rejection_signal),
            "critic_sum": float(outcome.response_critic_score or 0) / 100.0
            if outcome.response_critic_score and outcome.response_critic_score > 1.5
            else float(outcome.response_critic_score or 0),
            "latency_sum": 1.0 - float(outcome.latency_score),
            "reward_sum": float(outcome.overall_reward),
        },
    )
    # Soft global aggregate with lower weight via empty user_id — only on strong evidence
    if abs(outcome.overall_reward) >= 0.4 and outcome.confidence >= 0.55:
        gkey = f"u:|s:{outcome.selected_strategy}|i:"
        store.bump_metric(
            "strategy_metrics",
            gkey,
            columns={
                "user_id": "",
                "strategy": outcome.selected_strategy,
                "intent": "",
                "domain": "",
                "samples": 1,
                "success_sum": 1.0 if outcome.overall_reward >= 0.15 else 0.0,
                "correction_sum": float(outcome.correction_signal) * 0.5,
                "rejection_sum": float(outcome.rejection_signal) * 0.5,
                "critic_sum": 0.0,
                "latency_sum": 0.0,
                "reward_sum": float(outcome.overall_reward) * 0.5,
            },
        )


def update_planner_metrics(outcome: TurnOutcomeEvaluation) -> None:
    if not outcome.planner_used:
        return
    key = f"planner:{outcome.user_id}"
    store.bump_metric(
        "planner_metrics",
        key,
        columns={
            "user_id": outcome.user_id,
            "samples": 1,
            "plan_quality_sum": max(0.0, outcome.overall_reward),
            "step_count_sum": 1.0,
            "unnecessary_steps_sum": 1.0 if outcome.overall_reward < -0.1 else 0.0,
            "missing_steps_sum": 1.0 if outcome.correction_signal > 0.5 else 0.0,
            "execution_success_sum": 1.0 if outcome.overall_reward >= 0.15 else 0.0,
            "user_acceptance_sum": float(outcome.acceptance_signal),
            "correction_sum": float(outcome.correction_signal),
            "tool_order_quality_sum": float(outcome.tool_success_score),
            "completion_sum": float(outcome.task_completion_signal),
        },
    )


def update_tool_metrics_from_trace(
    *, user_id: str, trace: dict[str, Any], reward: float
) -> None:
    names = []
    for key in ("tool_names",):
        raw = trace.get(key)
        if isinstance(raw, list):
            names.extend(str(x) for x in raw)
    # Fallback from controlled web
    if trace.get("controlled_web_tool"):
        names.append(str(trace.get("controlled_web_tool")))
    fails = int(trace.get("tool_failures") or 0)
    oks = int(trace.get("tool_calls_successful") or 0)
    for name in names[:8]:
        short = name.split("(")[0][:80]
        key = f"tool:{user_id}:{short}"
        store.bump_metric(
            "tool_metrics",
            key,
            columns={
                "user_id": user_id,
                "tool_name": short,
                "samples": 1,
                "success_sum": 1.0 if oks >= fails and reward >= 0 else 0.0,
                "timeout_sum": 1.0 if "timeout" in short.lower() else 0.0,
                "malformed_sum": 0.0,
                "empty_sum": 1.0 if trace.get("controlled_web_has_results") is False else 0.0,
                "retry_success_sum": float(trace.get("research_fallback_count") or 0) * 0.0,
                "latency_sum": float(trace.get("duration_ms") or 0) / 1000.0,
                "usefulness_sum": max(0.0, reward),
                "side_effect_fail_sum": 0.0,
            },
        )


def update_provider_metrics_from_trace(
    *, user_id: str, trace: dict[str, Any], ok: bool, reward: float
) -> None:
    provider = str(trace.get("provider") or "unknown")
    key = f"prov:{user_id}:{provider}"
    store.bump_metric(
        "provider_metrics",
        key,
        columns={
            "user_id": user_id,
            "provider": provider,
            "samples": 1,
            "success_sum": 1.0 if ok and reward >= 0 else 0.0,
            "timeout_sum": 1.0 if any(
                "timeout" in str(e).lower()
                for e in (trace.get("errors") or [])
            ) else 0.0,
            "malformed_sum": 1.0 if trace.get("used_fallback") else 0.0,
            "critic_sum": float(trace.get("response_critic_score") or 0) / 100.0
            if trace.get("response_critic_score")
            else 0.0,
            "latency_sum": float(trace.get("duration_ms") or 0) / 1000.0,
            "tool_calling_sum": 1.0 if trace.get("used_tools") else 0.0,
        },
    )


def update_research_metrics_from_trace(
    *, user_id: str, trace: dict[str, Any], outcome: TurnOutcomeEvaluation
) -> None:
    variants = list(trace.get("research_query_variants") or [])
    q = str(trace.get("controlled_web_query") or (variants[0] if variants else "") or "")
    if not q and not outcome.web_used:
        return
    qh = store.content_hash(q)
    key = f"res:{user_id}:{qh}"
    store.bump_metric(
        "research_metrics",
        key,
        columns={
            "user_id": user_id,
            "query_hash": qh,
            "raw_query": (outcome.message_preview or "")[:200],
            "rewritten_query": q[:200],
            "samples": 1,
            "result_count_sum": float(trace.get("controlled_web_source_count") or 0),
            "useful_sum": 1.0 if outcome.research_quality_score >= 0.6 else 0.0,
            "source_quality_sum": float(outcome.research_quality_score),
            "grounding_sum": float(outcome.factual_grounding_score),
            "acceptance_sum": float(outcome.acceptance_signal),
            "correction_sum": float(outcome.correction_signal),
        },
    )


def update_self_model_from_outcome(outcome: TurnOutcomeEvaluation) -> RuntimeSelfModel:
    model = store.load_self_model()
    s = outcome.selected_strategy
    prev = float(model.strategy_success.get(s, 0.5))
    target = 1.0 if outcome.overall_reward >= 0.15 else 0.0
    # bounded EMA
    n = int(model.sample_counts.get(f"strategy:{s}", 0))
    alpha = 0.2 if n >= _MIN_SAMPLES_STRONG_BIAS else 0.08
    model.strategy_success[s] = _clamp(prev * (1 - alpha) + target * alpha)
    model.sample_counts[f"strategy:{s}"] = n + 1
    if outcome.planner_used:
        model.planner_success = _clamp(
            model.planner_success * (1 - alpha)
            + (1.0 if outcome.overall_reward >= 0.15 else 0.0) * alpha
        )
    if outcome.web_used:
        model.research_success = _clamp(
            model.research_success * (1 - alpha)
            + float(outcome.research_quality_score) * alpha
        )
    if outcome.overall_reward < -0.2:
        err = f"{outcome.primary_intent}:{outcome.selected_strategy}"
        if err not in model.typical_errors:
            model.typical_errors = (model.typical_errors + [err])[-12:]
        if outcome.primary_intent not in model.weak_domains:
            model.weak_domains = (model.weak_domains + [outcome.primary_intent])[-12:]
    if outcome.overall_reward > 0.35 and outcome.primary_intent not in model.strong_domains:
        model.strong_domains = (model.strong_domains + [outcome.primary_intent])[-12:]
    # Rolling hallucination risk by intent domain from CSE metadata.
    cse = (outcome.metadata or {}).get("continuous_self_eval") if isinstance(outcome.metadata, dict) else None
    if isinstance(cse, dict) and cse.get("hallucination_risk") is not None:
        domain = outcome.primary_intent or "unknown"
        prev_h = float(model.hallucination_risk_by_domain.get(domain, 0.3))
        model.hallucination_risk_by_domain[domain] = _clamp(
            prev_h * 0.7 + float(cse["hallucination_risk"]) * 0.3
        )
    if isinstance(cse, dict) and cse.get("token_efficiency") is not None:
        path = outcome.selected_strategy or "contextual"
        slot = dict(model.cost_latency_by_path.get(path) or {})
        prev_te = float(slot.get("token_efficiency", 0.5))
        slot["token_efficiency"] = _clamp(prev_te * 0.7 + float(cse["token_efficiency"]) * 0.3)
        slot["samples"] = int(slot.get("samples") or 0) + 1
        model.cost_latency_by_path[path] = slot
    model.version = int(model.version or 1) + 1
    store.save_self_model(model)
    return model


def _update_trait(
    trait: TraitObservation,
    *,
    value: Any,
    turn_id: str,
    positive: bool,
    min_conf_gate: float = 0.0,
) -> TraitObservation:
    # Single random sentence shouldn't rewrite: require either existing evidence or clear signal
    w = 0.15 if trait.evidence_count >= 2 else 0.08
    if trait.evidence_count == 0 and min_conf_gate > 0:
        w = min(w, 0.1)
    if isinstance(value, (int, float)) and isinstance(trait.value, (int, float)):
        trait.value = float(trait.value) * (1 - w) + float(value) * w
    else:
        if trait.evidence_count == 0 or trait.confidence < 0.35 or positive:
            trait.value = value
    trait.evidence_count += 1
    if positive:
        trait.positive_evidence += 1
    else:
        trait.negative_evidence += 1
    trait.confidence = _clamp(0.2 + 0.08 * trait.evidence_count - 0.05 * trait.negative_evidence)
    trait.last_updated = time.time()
    trait.source_turn_ids = (trait.source_turn_ids + [turn_id])[-12:]
    return trait


def update_user_model_v2_from_signals(
    *,
    user_id: str,
    turn_id: str,
    message: str,
    outcome: TurnOutcomeEvaluation,
    trace: dict[str, Any],
) -> tuple[UserModelV2, bool]:
    model = store.load_user_model_v2(user_id)
    changed = False
    msg = (message or "").lower()
    if re.search(r"(?iu)\b(krócej|zwięźlej|za długo|skr[oó]ć)\b", msg):
        model.preferred_verbosity = _update_trait(
            model.preferred_verbosity, value="short", turn_id=turn_id, positive=True
        )
        changed = True
    if re.search(r"(?iu)\b(dłużej|rozwi[nń]|więcej szczeg)\b", msg):
        model.preferred_verbosity = _update_trait(
            model.preferred_verbosity, value="long", turn_id=turn_id, positive=True
        )
        changed = True
    if re.search(r"(?iu)\b(punktami|krokami|krok po kroku)\b", msg):
        model.preferred_structure = _update_trait(
            model.preferred_structure,
            value="steps" if "krok" in msg else "bullets",
            turn_id=turn_id,
            positive=True,
        )
        changed = True
    if outcome.correction_signal > 0.5:
        model.correction_preference = _update_trait(
            model.correction_preference, value="direct", turn_id=turn_id, positive=True
        )
        changed = True
    if outcome.overall_reward > 0.35 and str(trace.get("user_model_length") or "") == "short":
        model.preferred_verbosity = _update_trait(
            model.preferred_verbosity, value="short", turn_id=turn_id, positive=True
        )
        changed = True
    if changed:
        model.version = int(model.version or 1) + 1
        store.save_user_model_v2(model)
    return model, changed


def maybe_update_long_horizon(
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    message: str,
    outcome: TurnOutcomeEvaluation,
    decision_core: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    dc = decision_core or {}
    msg = (message or "").strip()
    msg_l = msg.lower()
    task = store.get_active_long_horizon_task(user_id=user_id, session_id=session_id)

    # Explicit track / status intents must bind cross-session tasks by marker.
    track_intent = bool(
        re.search(
            r"(?iu)\b(śledź|sledz|długoterminow|dlugoterminow|track\s+this|long[\s-]?horizon)\b",
            msg_l,
        )
    )
    status_intent = bool(
        re.search(
            r"(?iu)\b(stan\s+zadania|stan\s+planu|następnym\s+krokiem|nastepnym\s+krokiem|next\s+step|progress)\b",
            msg_l,
        )
    )
    marker_hit = re.search(r"(?i)(Profile26-[A-Za-z0-9_-]+|Profile26)", msg)
    if task is None and (track_intent or status_intent or marker_hit):
        marker = marker_hit.group(1) if marker_hit else ""
        if marker:
            task = store.find_long_horizon_task_by_marker(user_id=user_id, marker=marker)
        if task is None:
            task = store.get_active_long_horizon_task(
                user_id=user_id, session_id="", allow_cross_session=True
            )
        if task is not None and session_id and task.session_id != session_id:
            # Rebind active task to the current session for continuity.
            task.session_id = session_id
            task.updated_at = time.time()
            store.save_long_horizon_task(task)
            store.append_task_event(
                task_id=task.task_id,
                turn_id=turn_id,
                event_type="task_session_rebound",
                payload={"session_id": session_id},
            )

    multi = bool(outcome.planner_used or dc.get("planner_recommended"))
    planish = bool(
        re.search(r"(?iu)\b(plan|etap|krok|najpierw|potem|wdroż)\b", msg)
        and len(msg.split()) >= 5
    )
    if task is None and (multi or planish or track_intent):
        title = msg[:120] or "multi-step task"
        if marker_hit:
            title = f"{marker_hit.group(1)}: {msg[:100]}"
        # Prefer structured pending steps for migration-style plans.
        pending = [msg[:160]] if msg else []
        if re.search(r"(?iu)migracj|rollback|weryfik", msg_l):
            pending = [
                "Przygotowanie / backup",
                "Konfiguracja nowego środowiska",
                "Migracja danych",
                "Weryfikacja + rollback plan",
            ]
        task = LongHorizonTask(
            task_id=str(uuid.uuid4()),
            user_id=user_id,
            session_id=session_id,
            title=title,
            objective=msg[:400],
            pending_steps=pending,
            current_stage="planning" if not track_intent else "tracked",
            status="active",
            confidence=0.7 if track_intent else 0.55,
            last_action="created" if not track_intent else "tracked_by_user",
            next_best_action=pending[0] if pending else "execute_first_step",
        )
        store.save_long_horizon_task(task)
        store.append_task_event(
            task_id=task.task_id,
            turn_id=turn_id,
            event_type="task_created",
            payload={"title": task.title, "track_intent": track_intent},
        )
        return task.task_id, True

    if task is None:
        return "", False

    updated = False
    if track_intent:
        if marker_hit and marker_hit.group(1).lower() not in (task.title or "").lower():
            task.title = f"{marker_hit.group(1)}: {(task.title or msg)[:100]}"
            updated = True
        task.last_action = "user_track_request"
        if not task.next_best_action and task.pending_steps:
            task.next_best_action = str(task.pending_steps[0])[:180]
            updated = True
        updated = True
        store.append_task_event(
            task_id=task.task_id,
            turn_id=turn_id,
            event_type="task_tracked",
            payload={"message": msg[:160]},
        )
    if status_intent:
        task.last_action = "status_query"
        updated = True
    if re.search(r"(?iu)\b(odrzucam|nie chcę|bez\s+\w+)\b", msg):
        if msg not in task.rejected_decisions:
            task.rejected_decisions = (task.rejected_decisions + [msg[:160]])[-20:]
            updated = True
            store.append_task_event(
                task_id=task.task_id, turn_id=turn_id, event_type="decision_rejected", payload={"text": msg[:160]}
            )
    if re.search(r"(?iu)\b(zostajemy|akceptuję|ok[,]?\s*robimy|decydujemy)\b", msg):
        if msg not in task.accepted_decisions:
            task.accepted_decisions = (task.accepted_decisions + [msg[:160]])[-20:]
            updated = True
            store.append_task_event(
                task_id=task.task_id, turn_id=turn_id, event_type="decision_accepted", payload={"text": msg[:160]}
            )
    if outcome.overall_reward >= 0.25 and outcome.planner_used:
        step = (outcome.message_preview or "")[:160]
        if step and step not in task.completed_steps:
            task.completed_steps = (task.completed_steps + [step])[-30:]
            task.pending_steps = [p for p in task.pending_steps if p != step]
            task.current_stage = "executing"
            if task.pending_steps:
                task.next_best_action = str(task.pending_steps[0])[:180]
            updated = True
    if re.search(r"(?iu)\b(anuluj\s+plan|porzuć|rezygnuję z planu)\b", msg):
        task.status = "abandoned"
        updated = True
        store.append_task_event(
            task_id=task.task_id, turn_id=turn_id, event_type="task_abandoned", payload={}
        )
    if updated:
        task.last_action = task.last_action or f"turn:{turn_id[:8]}"
        store.save_long_horizon_task(task)
    return task.task_id, updated


def record_failure_success(
    *,
    outcome: TurnOutcomeEvaluation,
    attributions: list[CausalAttribution],
    trace: dict[str, Any],
) -> tuple[bool, bool]:
    fail = False
    succ = False
    if outcome.overall_reward <= -0.15 or outcome.correction_signal > 0.5 or outcome.rejection_signal > 0.5:
        top_neg = next((a for a in attributions if a.contribution_score < 0), None)
        store.upsert_failure(
            FailurePattern(
                failure_id=str(uuid.uuid4()),
                user_id=outcome.user_id,
                category=(top_neg.factor if top_neg else "general_failure")[:80],
                trigger=outcome.message_preview or outcome.primary_intent,
                context=f"strategy={outcome.selected_strategy}",
                root_cause=(top_neg.evidence if top_neg else "negative_reward")[:280],
                evidence=f"reward={outcome.overall_reward:.2f}",
                affected_module=(top_neg.factor if top_neg else "runtime")[:80],
                corrective_action=(top_neg.corrective_action if top_neg else "revisit strategy")[:240],
                confidence=_clamp(0.45 + abs(outcome.overall_reward) * 0.3),
            )
        )
        fail = True
    if outcome.overall_reward >= 0.25 and outcome.correction_signal < 0.2:
        store.upsert_success(
            SuccessPattern(
                success_id=str(uuid.uuid4()),
                user_id=outcome.user_id,
                category="routing",
                pattern=f"strategy={outcome.selected_strategy};intent={outcome.primary_intent}",
                context=("web" if outcome.web_used else "no_web"),
                evidence=f"reward={outcome.overall_reward:.2f}",
                confidence=_clamp(0.45 + outcome.overall_reward * 0.3),
                success_rate=0.75,
            )
        )
        succ = True
    return fail, succ


def compute_learning_strategy_bias(
    *, user_id: str, intent: str = "", self_model: RuntimeSelfModel | None = None
) -> dict[str, float]:
    """Bounded per-user(+self-model) strategy deltas for decision_core."""
    rows = store.get_strategy_metric_rows(user_id=user_id, limit=40)
    bias = {"instant": 0.0, "contextual": 0.0, "research": 0.0, "agentic": 0.0}
    for r in rows:
        if str(r.get("user_id") or "") != user_id:
            continue
        strat = str(r.get("strategy") or "")
        if strat not in bias:
            continue
        if intent and r.get("intent") and r.get("intent") != intent:
            continue
        samples = int(r.get("samples") or 0)
        if samples < 2:
            continue
        rate = float(r.get("success_sum") or 0) / max(1, samples)
        corr = float(r.get("correction_sum") or 0) / max(1, samples)
        delta = (rate - 0.5) * 0.12 - corr * 0.08
        # sample damping
        if samples < _MIN_SAMPLES_STRONG_BIAS:
            delta *= 0.4
        bias[strat] = _clamp(bias[strat] + delta, -_MAX_BIAS_DELTA_PER_TURN * 3, _MAX_BIAS_DELTA_PER_TURN * 3)
    sm = self_model or store.load_self_model()
    for strat, rate in (sm.strategy_success or {}).items():
        if strat in bias:
            n = int(sm.sample_counts.get(f"strategy:{strat}", 0))
            if n < 3:
                continue
            bias[strat] = _clamp(
                bias[strat] + (float(rate) - 0.5) * 0.08,
                -0.15,
                0.15,
            )
    # Guardrail: never push research down hard for freshness-heavy domains via learning alone
    return bias


def apply_learning_influences_to_decision(
    *,
    decision_core: dict[str, Any],
    user_id: str,
    message: str,
    intent: str = "",
) -> dict[str, Any]:
    codes = list(decision_core.get("reason_codes") or [])
    self_model = store.load_self_model()
    lessons = store.list_active_lessons(user_id=user_id, limit=10)
    failures = store.list_relevant_failures(user_id=user_id, trigger_hint=message, limit=5)
    successes = store.list_success_patterns(user_id=user_id, limit=5)
    task = store.get_active_long_horizon_task(user_id=user_id, session_id=str(decision_core.get("session_id") or ""))
    um = store.load_user_model_v2(user_id)

    decision_core["self_model_loaded"] = True
    decision_core["learning_lessons_loaded"] = len(lessons)
    decision_core["learning_failures_loaded"] = len(failures)

    bias = compute_learning_strategy_bias(user_id=user_id, intent=intent, self_model=self_model)
    decision_core["learning_strategy_bias"] = bias

    strategy = str(decision_core.get("selected_strategy") or "contextual")
    conf_raw = float(decision_core.get("strategy_confidence") or 0.7)
    # Apply bounded bias
    best = strategy
    best_score = bias.get(strategy, 0.0)
    for s, d in bias.items():
        if d > best_score + 0.03 and d > 0.02:
            best = s
            best_score = d
    influenced = False
    if best != strategy and best_score >= 0.04:
        # Guardrail: do not demote research→instant when web required
        if not (
            str(decision_core.get("web_decision") or "off") == "required"
            and best == "instant"
        ):
            decision_core["selected_strategy"] = best
            codes.append(f"LEARN_STRATEGY_BIAS_{strategy.upper()}_TO_{best.upper()}")
            influenced = True
            strategy = best

    # Self-model weak domain → prefer research/contextual
    if intent and intent in (self_model.weak_domains or []) and strategy == "instant":
        if str(decision_core.get("web_decision") or "off") != "off":
            decision_core["selected_strategy"] = "research"
            codes.append("LEARN_SELF_MODEL_WEAK_DOMAIN_RESEARCH")
            influenced = True
        else:
            decision_core["selected_strategy"] = "contextual"
            codes.append("LEARN_SELF_MODEL_WEAK_DOMAIN_CONTEXTUAL")
            influenced = True

    # Failure memory
    for f in failures:
        if f.corrective_action and "research" in f.corrective_action.lower():
            if str(decision_core.get("web_decision") or "off") == "off":
                decision_core["web_decision"] = "optional"
            if strategy == "instant":
                decision_core["selected_strategy"] = "contextual"
                codes.append("LEARN_FAILURE_MEMORY_AVOID_INSTANT")
                influenced = True
        decision_core.setdefault("failure_memory_hints", []).append(
            {"category": f.category, "action": f.corrective_action, "recurrence": f.recurrence_count}
        )
        codes.append("LEARN_FAILURE_MEMORY_USED")

    if successes:
        codes.append("LEARN_SUCCESS_MEMORY_USED")
        decision_core["success_memory_count"] = len(successes)

    if lessons:
        codes.append("LEARN_LESSONS_APPLIED")
        decision_core["learning_lesson_statements"] = [l.statement[:160] for l in lessons[:4]]
        if apply_machine_actions(decision_core=decision_core, lessons=lessons, codes=codes):
            influenced = True
            strategy = str(decision_core.get("selected_strategy") or strategy)

    # User model V2 style into decision (for prompt/cognitive)
    decision_core["user_model_v2"] = {
        "verbosity": um.preferred_verbosity.value,
        "verbosity_confidence": um.preferred_verbosity.confidence,
        "structure": um.preferred_structure.value,
        "structure_confidence": um.preferred_structure.confidence,
        "planning": um.planning_preference.value,
    }
    if um.preferred_verbosity.value == "short" and um.preferred_verbosity.confidence >= 0.4:
        decision_core["learning_length_directive"] = "short"
        codes.append("LEARN_USER_MODEL_V2_SHORT")
    if um.planning_preference.value == "deep" and um.planning_preference.confidence >= 0.45:
        decision_core["planner_recommended"] = True
        codes.append("LEARN_USER_MODEL_V2_DEEP_PLAN")

    if task is not None:
        decision_core["long_horizon_task_id"] = task.task_id
        rejected = list(task.rejected_decisions[-20:])
        decision_core["long_horizon_rejected"] = rejected
        decision_core["long_horizon_accepted"] = list(task.accepted_decisions[-8:])
        decision_core["long_horizon_status"] = task.status
        decision_core["long_horizon_title"] = task.title
        decision_core["long_horizon_objective"] = task.objective
        decision_core["long_horizon_stage"] = task.current_stage
        decision_core["long_horizon_next_step"] = task.next_best_action
        decision_core["long_horizon_pending"] = list(task.pending_steps[:5])
        decision_core["long_horizon_completed"] = list(task.completed_steps[-5:])
        decision_core["long_horizon_brief"] = store.format_long_horizon_brief(task)
        msg_l = (message or "").lower()
        # Status / next-step questions about an active LHT must stay memory-aware.
        if re.search(
            r"(?iu)\b(stan\s+zadania|stan\s+planu|następnym\s+krokiem|nastepnym\s+krokiem|next\s+step)\b",
            msg_l,
        ) or ("profile26" in msg_l and "zadani" in msg_l):
            if str(decision_core.get("selected_strategy") or "") in ("instant", "direct", "casual"):
                decision_core["selected_strategy"] = "agentic"
                influenced = True
                codes.append("LEARN_LHT_STATUS_ESCALATE_AGENTIC")
            decision_core["planner_recommended"] = True
            decision_core["requires_memory"] = True
        if rejected:
            decision_core["rejected_decision_guard_applied"] = True
            decision_core["blocked_rejected_options"] = list(dict.fromkeys(rejected))[:10]
            codes.append("LEARN_LHT_REJECTED_GUARD")
            # Soft-block concrete known rejected options when referenced again
            for rej in rejected:
                rlow = str(rej).lower()
                keys = [
                    w
                    for w in re.findall(r"[a-ząęćłńóśźżA-Za-z]{4,}", rlow)
                    if w
                    not in {
                        "odrzucam",
                        "pomysł",
                        "opcją",
                        "opcja",
                        "bez",
                        "proszę",
                    }
                ]
                hit = any(k in msg_l for k in keys[:8])
                if hit and "jednak" not in msg_l and "zmieni" not in msg_l and "nowe" not in msg_l:
                    for k in keys[:3]:
                        decision_core.setdefault("learning_suppress_options", []).append(k)
                    codes.append("LEARN_REJECTED_OPTION_SUPPRESSED")
        codes.append("LEARN_LHT_ACTIVE")

    # Tool order from tool metrics
    tool_rows = store.get_tool_metric_rows(user_id=user_id, limit=20)
    if tool_rows:
        ranked = sorted(
            tool_rows,
            key=lambda r: (
                float(r.get("success_sum") or 0) / max(1, int(r.get("samples") or 1)),
            ),
            reverse=True,
        )
        order = []
        for r in ranked[:6]:
            fam = str(r.get("tool_name") or "").split(".", 1)[0]
            if fam and fam not in order:
                order.append(fam)
        if order:
            existing = list(decision_core.get("tool_order_hint") or [])
            decision_core["tool_order_hint"] = order + [x for x in existing if x not in order]
            codes.append("LEARN_TOOL_ORDER_METRICS")

    # Provider preference
    prov_rows = store.get_provider_metric_rows(user_id=user_id, limit=10)
    if prov_rows:
        best_p = max(
            prov_rows,
            key=lambda r: float(r.get("success_sum") or 0) / max(1, int(r.get("samples") or 1)),
        )
        decision_core["provider_learning_preference"] = {
            "provider": best_p.get("provider"),
            "samples": best_p.get("samples"),
            "success_rate": float(best_p.get("success_sum") or 0)
            / max(1, int(best_p.get("samples") or 1)),
        }
        codes.append("LEARN_PROVIDER_METRICS")

    cal = calibrate_confidence(
        raw=conf_raw,
        strategy=str(decision_core.get("selected_strategy") or strategy),
        intent=intent,
        user_id=user_id,
        ambiguity=float(decision_core.get("cognitive_ambiguity") or 0),
    )
    decision_core["strategy_confidence_raw"] = cal.raw_confidence
    decision_core["strategy_confidence"] = round(cal.calibrated_confidence, 3)
    decision_core["confidence_calibration_delta"] = round(cal.calibration_delta, 3)
    decision_core["confidence_calibration_source"] = cal.calibration_source
    decision_core["confidence_calibration_samples"] = cal.calibration_sample_count
    codes.append("LEARN_CONFIDENCE_CALIBRATED")

    # Continuous self-eval prior → real next-turn behavioral influence.
    try:
        from aihub.turn.cse_feedback import apply_cse_prior_to_decision, load_cse_prior

        prior = load_cse_prior(user_id)
        if prior:
            apply_cse_prior_to_decision(decision_core, prior, message=message)
            if decision_core.get("cse_prior_influenced"):
                influenced = True
            codes = list(decision_core.get("reason_codes") or codes)
    except Exception as cse_exc:
        log.debug("cse prior apply skipped: %s", cse_exc, exc_info=True)
        codes.append("CSE_PRIOR_APPLY_SKIPPED")

    decision_core["self_model_influenced_strategy"] = influenced
    decision_core["reason_codes"] = codes
    return decision_core


def process_turn_learning(
    *,
    turn_id: str,
    user_id: str,
    session_id: str,
    message: str,
    response_text: str,
    trace: dict[str, Any],
    decision_core: dict[str, Any] | None = None,
    ok: bool = True,
    errors: list[dict[str, Any]] | None = None,
    replay_mode: bool = False,
) -> LearningTurnResult:
    t0 = time.time()
    result = LearningTurnResult()
    if not user_id or str(user_id).startswith("audit"):
        result.degraded = True
        result.reason_codes.append("LEARN_SKIPPED_AUDIT_OR_EMPTY")
        return result
    try:
        store.ensure_ready()
        # Delayed feedback on THIS user message, targeting previous turn
        delayed = detect_delayed_feedback(
            message=message,
            user_id=user_id,
            session_id=session_id,
            feedback_turn_id=turn_id,
        )
        if delayed is not None:
            apply_delayed_feedback(delayed)
            result.delayed_feedback = delayed
            result.reason_codes.extend(delayed.reason_codes)

        outcome = evaluate_turn_outcome(
            turn_id=turn_id,
            user_id=user_id,
            session_id=session_id,
            message=message,
            response_text=response_text,
            trace=trace,
            decision_core=decision_core,
            ok=ok,
            errors=errors,
        )
        if not replay_mode:
            store.upsert_turn_outcome(outcome)
        result.outcome = outcome

        attrs = attribute_causes(outcome=outcome, trace=trace, decision_core=decision_core)
        result.attributions = attrs
        if not replay_mode:
            store.insert_causal(turn_id, user_id, attrs)

        cands = extract_lesson_candidates(outcome=outcome, attributions=attrs)
        result.lesson_candidates = len(cands)
        if not replay_mode:
            for les in cands:
                ok_p, why = store.upsert_lesson(les)
                if ok_p:
                    result.lessons_persisted += 1
                else:
                    result.lessons_rejected += 1
                    result.reason_codes.append(f"LESSON_REJECT:{why}")
            store.decay_lessons(limit=80)

        raw_conf = float((decision_core or {}).get("strategy_confidence") or 0.7)
        result.calibration = calibrate_confidence(
            raw=raw_conf,
            strategy=outcome.selected_strategy,
            intent=outcome.primary_intent,
            user_id=user_id,
        )

        if not replay_mode:
            update_strategy_metrics(outcome)
            update_planner_metrics(outcome)
            update_tool_metrics_from_trace(user_id=user_id, trace=trace, reward=outcome.overall_reward)
            update_provider_metrics_from_trace(
                user_id=user_id, trace=trace, ok=ok, reward=outcome.overall_reward
            )
            update_research_metrics_from_trace(user_id=user_id, trace=trace, outcome=outcome)
            update_self_model_from_outcome(outcome)
            result.self_model_updated = True
            # Persist CSE rolling prior so next turn can change behavior.
            try:
                from aihub.turn.cse_feedback import persist_cse_prior

                cse = {}
                if isinstance(trace, dict):
                    cse = dict(trace.get("continuous_self_eval") or {})
                if not cse and isinstance(outcome.metadata, dict):
                    cse = dict(outcome.metadata.get("continuous_self_eval") or {})
                if cse:
                    persist_cse_prior(user_id, cse)
                    result.reason_codes.append("CSE_PRIOR_PERSISTED")
            except Exception as cse_persist_exc:
                log.debug("cse prior persist skipped: %s", cse_persist_exc, exc_info=True)
            _, um_changed = update_user_model_v2_from_signals(
                user_id=user_id,
                turn_id=turn_id,
                message=message,
                outcome=outcome,
                trace=trace,
            )
            result.user_model_updated = um_changed
            fail, succ = record_failure_success(outcome=outcome, attributions=attrs, trace=trace)
            result.failure_recorded = fail
            result.success_recorded = succ
            tid, gupd = maybe_update_long_horizon(
                user_id=user_id,
                session_id=session_id,
                turn_id=turn_id,
                message=message,
                outcome=outcome,
                decision_core=decision_core,
            )
            result.long_horizon_task_id = tid
            result.goal_progress_updated = gupd

        result.reason_codes.append("LEARN_PIPELINE_OK")
    except Exception as exc:
        log.warning("process_turn_learning failed: %s", exc, exc_info=True)
        result.degraded = True
        result.reason_codes.append("LEARN_DEGRADED")
    result.timing_ms = (time.time() - t0) * 1000.0
    return result


def learning_trace_fields(result: LearningTurnResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "outcome_evaluation_happened": result.outcome is not None,
        "learning_degraded": result.degraded,
        "lesson_candidates_count": result.lesson_candidates,
        "lessons_generated_count": result.lesson_candidates,
        "lessons_persisted_count": result.lessons_persisted,
        "lessons_rejected_count": result.lessons_rejected,
        "causal_attribution_count": len(result.attributions),
        "self_model_loaded": True,
        "user_model_updated": result.user_model_updated,
        "failure_memory_used": result.failure_recorded,
        "success_memory_used": result.success_recorded,
        "failure_patterns_used": result.failure_recorded,
        "success_patterns_used": result.success_recorded,
        "goal_progress_updated": result.goal_progress_updated,
        "long_horizon_task_id": result.long_horizon_task_id,
        "task_state_id": result.long_horizon_task_id,
        "task_state_updated": result.goal_progress_updated,
        "learning_timing_ms": result.timing_ms,
        "learning_reason_codes": list(result.reason_codes)[:24],
        "replay_mode": False,
    }
    if result.outcome:
        out.update(
            {
                "outcome_overall_reward": result.outcome.overall_reward,
                "outcome_intent_match": result.outcome.intent_match_score,
                "outcome_style_match": result.outcome.style_match_score,
                "outcome_grounding_score": result.outcome.factual_grounding_score,
                "outcome_user_signal": result.outcome.user_satisfaction_signal,
                "immediate_feedback_detected": bool(
                    result.outcome.correction_detected
                    or result.outcome.acceptance_detected
                    or result.outcome.rejection_detected
                ),
            }
        )
    if result.delayed_feedback:
        out.update(
            {
                "delayed_feedback_detected": True,
                "delayed_feedback_target_turn_id": result.delayed_feedback.target_turn_id,
                "delayed_feedback_polarity": result.delayed_feedback.polarity,
            }
        )
    else:
        out["delayed_feedback_detected"] = False
    if result.calibration:
        out.update(
            {
                "raw_confidence": result.calibration.raw_confidence,
                "calibrated_confidence": result.calibration.calibrated_confidence,
                "confidence_raw": result.calibration.raw_confidence,
                "confidence_calibrated": result.calibration.calibrated_confidence,
                "confidence_calibration_delta": result.calibration.calibration_delta,
                "calibration_delta": result.calibration.calibration_delta,
            }
        )
    out["self_model_updated"] = result.self_model_updated
    return out
