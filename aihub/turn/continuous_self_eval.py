#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Continuous self-evaluation metrics after each turn (no extra LLM required)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


@dataclass
class ContinuousSelfEval:
    hallucination_risk: float = 0.3
    retrieval_usefulness: float = 0.5
    memory_usefulness: float = 0.5
    planner_usefulness: float = 0.5
    reflection_usefulness: float = 0.5
    tool_usefulness: float = 0.5
    token_efficiency: float = 0.5
    confidence_calibration: float = 0.5
    answer_completeness: float = 0.5
    overall_quality: float = 0.5
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_continuous_self(
    *,
    message: str,
    response_text: str,
    trace: dict[str, Any] | None = None,
    decision_core: dict[str, Any] | None = None,
    ok: bool = True,
) -> ContinuousSelfEval:
    """Score turn quality dimensions from observables already in trace/decision."""
    tr = trace or {}
    dc = decision_core or {}
    codes: list[str] = ["CONTINUOUS_SELF_EVAL"]
    msg = message or ""
    resp = response_text or ""
    resp_n = len(resp)
    msg_n = max(1, len(msg))

    # --- hallucination risk ---
    grounding = str(tr.get("response_grounding_mode") or "")
    hall = 0.35
    if grounding in ("tools_verified", "web_verified", "prefetch_verified_in_thread"):
        hall = 0.15
        codes.append("CSE_GROUNDED")
    elif grounding in ("fallback", "blocker_hard_gate"):
        hall = 0.75
        codes.append("CSE_UNGROUNDED_FALLBACK")
    if tr.get("anti_hallucination_clamp_applied"):
        hall = max(hall, 0.55)
        codes.append("CSE_AH_CLAMP")
    if tr.get("used_fallback"):
        hall = max(hall, 0.7)
    if not ok:
        hall = max(hall, 0.65)
    # Action claims without tools → risk
    low_resp = resp.lower()
    if any(k in low_resp for k in ("wykonałem", "wykonalem", "zrobiłem", "zrobilem", "uruchomiłem")):
        tools_ok = int(tr.get("tool_calls_successful") or 0)
        if tools_ok <= 0:
            hall = max(hall, 0.8)
            codes.append("CSE_ACTION_CLAIM_NO_TOOLS")
    hall = _clamp(hall)

    # --- retrieval / memory usefulness ---
    pack_ids = []
    pack = tr.get("memory_context_pack") or {}
    if isinstance(pack, dict):
        pack_ids = list(pack.get("selected_ids") or [])
    if not pack_ids:
        pack_ids = list(tr.get("memory_context_pack_selected_ids") or [])
    mem_lookup = bool(tr.get("memory_lookup_happened") or tr.get("memory_substantive_in_prompt"))
    mem_u = 0.35
    if pack_ids:
        # Overlap between pack ids / content hints and response is a weak proxy.
        overlap = sum(1 for sid in pack_ids[:8] if sid and sid[:8] in resp)
        mem_u = _clamp(0.45 + 0.08 * min(4, len(pack_ids)) + 0.05 * overlap)
        codes.append("CSE_MEMORY_PACK_USED")
    elif mem_lookup:
        mem_u = 0.4
    else:
        # Recall intent without memory → poor usefulness
        if any(k in msg.lower() for k in ("pamiętasz", "pamietasz", "jak nazywa", "co mówiłem")):
            mem_u = 0.2
            codes.append("CSE_RECALL_WITHOUT_PACK")
        else:
            mem_u = 0.5  # memory not needed
            codes.append("CSE_MEMORY_NOT_REQUIRED")

    retrieval_u = mem_u
    if tr.get("controlled_web_triggered") and tr.get("controlled_web_ok"):
        retrieval_u = max(retrieval_u, 0.7 if tr.get("controlled_web_has_results") else 0.35)
        codes.append("CSE_WEB_RETRIEVAL")
    elif str(dc.get("web_decision") or "") == "required":
        retrieval_u = min(retrieval_u, 0.3)
        codes.append("CSE_WEB_REQUIRED_MISS")

    # --- planner usefulness ---
    planner_used = bool(tr.get("planner_used") or tr.get("planner_executed") or dc.get("planner_chat_plan"))
    planner_u = 0.5
    if planner_used:
        nodes = int(tr.get("planner_tasks_count") or 0)
        plan_bits = str(dc.get("planner_brief") or "")
        if nodes >= 2 or len(plan_bits) > 40:
            planner_u = 0.75 if any(k in resp.lower() for k in ("etap", "krok", "plan", "1.", "2.")) else 0.55
            codes.append("CSE_PLANNER_USED")
        else:
            planner_u = 0.4
            codes.append("CSE_PLANNER_THIN")
    else:
        planner_u = 0.55  # not applicable
        codes.append("CSE_PLANNER_NA")

    # --- reflection usefulness ---
    refl = tr.get("reflection_ran")
    if refl is False or tr.get("post_reflection_skipped"):
        # Skipped intentionally → high efficiency / neutral usefulness
        reflection_u = 0.6
        codes.append("CSE_REFLECTION_SKIPPED")
    elif tr.get("reflection_summary") or (isinstance(tr.get("post_reflection"), dict) and tr["post_reflection"].get("reflection_summary")):
        reflection_u = 0.65
        codes.append("CSE_REFLECTION_RAN")
    else:
        reflection_u = 0.5

    # --- tool usefulness ---
    tools_ok = int(tr.get("tool_calls_successful") or 0)
    tools_fail = int(tr.get("tool_failures") or 0)
    tools_req = int(tr.get("tool_calls_requested") or 0)
    if tools_req == 0 and not tr.get("used_tools"):
        tool_u = 0.55
        codes.append("CSE_TOOLS_NA")
    else:
        total = max(1, tools_ok + tools_fail)
        tool_u = _clamp(tools_ok / total)
        if tools_ok > 0 and grounding in ("tools_verified", "web_verified"):
            tool_u = max(tool_u, 0.75)
        codes.append("CSE_TOOLS_SCORED")

    # --- token efficiency ---
    usage_total = float(tr.get("usage_total_tokens") or 0)
    prompt_est = float((tr.get("prompt_budget") or {}).get("system_estimated_tokens") or 0) if isinstance(tr.get("prompt_budget"), dict) else 0.0
    profile = str(tr.get("budget_profile") or dc.get("budget_profile") or "")
    if profile in ("meta_light", "casual_light"):
        token_eff = 0.9 if resp_n < 1200 else 0.6
        codes.append("CSE_TOKEN_LIGHT")
    elif usage_total > 0:
        # Value density: response chars per token (higher better, capped).
        dens = resp_n / max(1.0, usage_total)
        token_eff = _clamp(0.25 + dens / 8.0)
        if prompt_est and prompt_est > 3000 and resp_n < 400:
            token_eff = min(token_eff, 0.35)
            codes.append("CSE_TOKEN_FAT_PROMPT")
    else:
        # Estimate from response vs message.
        ratio = resp_n / float(msg_n)
        if ratio > 8 and profile != "agentic":
            token_eff = 0.35
            codes.append("CSE_TOKEN_VERBOSE")
        elif ratio < 0.3 and "?" in msg:
            token_eff = 0.4
            codes.append("CSE_TOKEN_TOO_SHORT")
        else:
            token_eff = 0.6

    # --- confidence calibration ---
    pred = float(dc.get("strategy_confidence") or tr.get("strategy_confidence") or 0.5)
    # Proxy outcome quality
    critic = tr.get("response_critic_score")
    try:
        critic_f = float(critic) / 100.0 if critic is not None and float(critic) > 1.5 else (
            float(critic) if critic is not None else None
        )
    except Exception:
        critic_f = None
    outcome = 0.55
    if critic_f is not None:
        outcome = critic_f
    if not ok or tr.get("used_fallback"):
        outcome = min(outcome, 0.3)
    if tr.get("response_revision_happened"):
        outcome = min(outcome, 0.55)
    # Calibration: 1 - |pred - outcome|
    calib = _clamp(1.0 - abs(pred - outcome))
    codes.append("CSE_CALIBRATION")

    # --- answer completeness ---
    complete = 0.55
    if resp_n < 20:
        complete = 0.2
        codes.append("CSE_EMPTYISH")
    elif "?" in msg and resp_n >= 40:
        complete = 0.7
    if any(k in msg.lower() for k in ("plan", "etap", "krokami", "checklist")):
        if any(k in low_resp for k in ("1.", "2.", "etap", "krok", "- ")):
            complete = max(complete, 0.8)
            codes.append("CSE_STRUCTURED_PLAN")
        else:
            complete = min(complete, 0.4)
            codes.append("CSE_PLAN_INCOMPLETE")
    if tr.get("response_revision_happened"):
        complete = max(complete, 0.65)
    complete = _clamp(complete)

    overall = _clamp(
        0.18 * (1.0 - hall)
        + 0.12 * retrieval_u
        + 0.12 * mem_u
        + 0.08 * planner_u
        + 0.06 * reflection_u
        + 0.12 * tool_u
        + 0.12 * token_eff
        + 0.10 * calib
        + 0.10 * complete
    )

    return ContinuousSelfEval(
        hallucination_risk=hall,
        retrieval_usefulness=retrieval_u,
        memory_usefulness=mem_u,
        planner_usefulness=planner_u,
        reflection_usefulness=reflection_u,
        tool_usefulness=tool_u,
        token_efficiency=token_eff,
        confidence_calibration=calib,
        answer_completeness=complete,
        overall_quality=overall,
        reason_codes=codes,
    )
