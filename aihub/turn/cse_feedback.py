#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Continuous self-eval feedback: priors that change the *next* turn."""

from __future__ import annotations

from typing import Any

from aihub.db import fetch_recent_events_by_type, append_event

CSE_PRIOR_EVENT = "learning.cse_prior"

_EMA_KEYS = (
    "hallucination_risk",
    "retrieval_usefulness",
    "memory_usefulness",
    "planner_usefulness",
    "reflection_usefulness",
    "tool_usefulness",
    "token_efficiency",
    "confidence_calibration",
    "answer_completeness",
    "overall_quality",
)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


def load_cse_prior(user_id: str) -> dict[str, Any] | None:
    if not user_id:
        return None
    rows = fetch_recent_events_by_type(user_id, CSE_PRIOR_EVENT, limit=1)
    if not rows:
        return None
    data = rows[0].get("data") or {}
    return data if isinstance(data, dict) else None


def merge_cse_prior(prev: dict[str, Any] | None, cse: dict[str, Any], *, alpha: float = 0.35) -> dict[str, Any]:
    """EMA-merge latest continuous self-eval into a rolling prior."""
    out: dict[str, Any] = dict(prev or {})
    samples = int(out.get("samples") or 0) + 1
    a = alpha if samples > 2 else max(alpha, 0.5)
    for k in _EMA_KEYS:
        if k not in cse:
            continue
        try:
            new_v = float(cse[k])
        except (TypeError, ValueError):
            continue
        old_v = float(out.get(k) if out.get(k) is not None else new_v)
        out[k] = _clamp(old_v * (1.0 - a) + new_v * a)
    out["samples"] = samples
    out["last_reason_codes"] = list(cse.get("reason_codes") or [])[:12]
    return out


def persist_cse_prior(user_id: str, cse: dict[str, Any] | None) -> dict[str, Any] | None:
    """Persist rolling CSE prior for the user (influences next turns)."""
    if not user_id or not isinstance(cse, dict) or not cse:
        return None
    merged = merge_cse_prior(load_cse_prior(user_id), cse)
    append_event(user_id, CSE_PRIOR_EVENT, merged)
    return merged


def apply_cse_prior_to_decision(
    decision_core: dict[str, Any],
    prior: dict[str, Any] | None,
    *,
    message: str = "",
) -> dict[str, Any]:
    """Mutate decision_core using rolling CSE prior — real behavioral influence."""
    if not prior or int(prior.get("samples") or 0) < 1:
        return decision_core
    codes = list(decision_core.get("reason_codes") or [])
    hall = float(prior.get("hallucination_risk") or 0.3)
    tok = float(prior.get("token_efficiency") or 0.5)
    mem = float(prior.get("memory_usefulness") or 0.5)
    calib = float(prior.get("confidence_calibration") or 0.5)
    complete = float(prior.get("answer_completeness") or 0.5)
    overall = float(prior.get("overall_quality") or 0.5)
    influenced = False

    decision_core["cse_prior"] = {
        "samples": prior.get("samples"),
        "hallucination_risk": hall,
        "token_efficiency": tok,
        "memory_usefulness": mem,
        "confidence_calibration": calib,
        "overall_quality": overall,
    }
    codes.append("CSE_PRIOR_LOADED")

    # High hallucination risk → prefer grounded paths + keep critic on.
    if hall >= 0.55 and int(prior.get("samples") or 0) >= 2:
        decision_core["cse_force_critic"] = True
        decision_core["skip_critic"] = False
        if str(decision_core.get("web_decision") or "off") == "off":
            # Soft prefer optional web for factual / current questions.
            low = (message or "").lower()
            if any(k in low for k in ("aktualn", "dziś", "dzis", "ceny", "wersja", "kiedy", "ile koszt")):
                decision_core["web_decision"] = "optional"
                codes.append("CSE_PRIOR_WEB_OPTIONAL")
                influenced = True
        codes.append("CSE_PRIOR_HIGH_HALLUCINATION_GUARD")
        influenced = True

    # Poor token efficiency → lean budget bias for next turn.
    if tok < 0.4 and int(prior.get("samples") or 0) >= 2:
        decision_core["cse_lean_budget"] = True
        decision_core["learning_length_directive"] = decision_core.get("learning_length_directive") or "short"
        codes.append("CSE_PRIOR_LEAN_BUDGET")
        influenced = True

    # Weak memory usefulness → on recall intents, force memory / contextual.
    low = (message or "").lower()
    recallish = any(
        k in low
        for k in ("pamiętasz", "pamietasz", "jak nazywa", "co mówiłem", "co mowilem", "zapamiętał")
    )
    if mem < 0.4 and recallish:
        decision_core["requires_memory"] = True
        if str(decision_core.get("selected_strategy") or "") in ("instant", "direct", "casual"):
            decision_core["selected_strategy"] = "contextual"
            codes.append("CSE_PRIOR_RECALL_ESCALATE_CONTEXTUAL")
            influenced = True
        decision_core["cse_boost_memory_pack"] = True
        codes.append("CSE_PRIOR_MEMORY_BOOST")
        influenced = True

    # Miscalibration → shrink reported confidence (feeds signals / adaptive).
    if calib < 0.45 and int(prior.get("samples") or 0) >= 2:
        raw = float(decision_core.get("strategy_confidence") or 0.7)
        decision_core["strategy_confidence"] = round(_clamp(raw * 0.85), 3)
        decision_core["cse_confidence_penalty"] = round(raw - float(decision_core["strategy_confidence"]), 3)
        codes.append("CSE_PRIOR_CONFIDENCE_PENALTY")
        influenced = True

    # Incomplete answers historically → prefer planner / structured on plan asks.
    if complete < 0.45 and any(k in low for k in ("plan", "etap", "krokami", "checklist")):
        decision_core["planner_recommended"] = True
        codes.append("CSE_PRIOR_PLANNER_ON_INCOMPLETE")
        influenced = True

    if overall < 0.4 and int(prior.get("samples") or 0) >= 3:
        decision_core["cse_force_reflection"] = True
        codes.append("CSE_PRIOR_FORCE_REFLECTION")
        influenced = True

    decision_core["cse_prior_influenced"] = influenced
    decision_core["reason_codes"] = codes
    return decision_core


def apply_cse_prior_to_signals(signals: Any, prior: dict[str, Any] | None) -> Any:
    """Adjust TurnSignals from rolling CSE prior (feeds dynamic budget / adaptive)."""
    if not prior or not signals:
        return signals
    samples = int(prior.get("samples") or 0)
    if samples < 1:
        return signals
    codes = list(getattr(signals, "reason_codes", []) or [])
    codes.append("CSE_PRIOR_IN_SIGNALS")

    hall = float(prior.get("hallucination_risk") or 0.3)
    tok = float(prior.get("token_efficiency") or 0.5)
    mem = float(prior.get("memory_usefulness") or 0.5)
    calib = float(prior.get("confidence_calibration") or 0.5)

    if hall >= 0.55:
        signals.uncertainty = _clamp(max(signals.uncertainty, 0.55))
        signals.expected_token_roi = _clamp(max(signals.expected_token_roi, 0.55))
        codes.append("CSE_SIG_HIGH_HALL_UNCERTAINTY")
    if tok < 0.4:
        signals.expected_token_roi = _clamp(min(signals.expected_token_roi, 0.35))
        signals.latency_budget_ms = min(float(signals.latency_budget_ms), 2500.0)
        codes.append("CSE_SIG_LEAN_ROI")
    if mem < 0.35:
        # Don't waste pack tokens when memory historically useless — unless recall already raised it.
        if signals.memory_usefulness < 0.6:
            signals.memory_usefulness = _clamp(min(signals.memory_usefulness, 0.35))
            codes.append("CSE_SIG_MEMORY_LEAN")
    elif mem >= 0.65:
        signals.memory_usefulness = _clamp(max(signals.memory_usefulness, 0.55))
        codes.append("CSE_SIG_MEMORY_TRUST")
    if calib < 0.45:
        signals.confidence = _clamp(signals.confidence * 0.9)
        signals.uncertainty = _clamp(max(signals.uncertainty, 1.0 - signals.confidence))
        codes.append("CSE_SIG_CALIB_DOWN")

    signals.reason_codes = codes
    return signals
