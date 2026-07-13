"""TurnOps mixin: DecisionMixin."""
from __future__ import annotations

import aihub.turn._ops_ns as _ops_ns

# Method bodies resolve names via this module's globals (incl. _names).
globals().update({k: v for k, v in vars(_ops_ns).items() if k != '__name__'})
TurnOps = None  # late-bound by aihub.turn.ops

class DecisionMixin:
    def _pre_exec_decision_core(
        self,
        *,
        turn: ChatTurnInput,
        ctx: ChatTurnContext,
        psyche_snapshot: dict[str, Any],
        memory_v2_runtime_ctx: Any = None,
        psyche_v2_behavior_ctx: Any = None,
    ) -> dict[str, Any]:
        """Run strategy selection, simulation, policy build and consistency check
        BEFORE the provider call. Outputs drive tool filtering, system prompt
        injection and the full trace."""
        result: dict[str, Any] = {
            "selected_strategy": "instant",
            "reason_codes": [],
            "strategy_confidence": None,
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
            "experience_blocker_severity": None,
            "experience_recurring_failure_detected": False,
            "experience_recurring_failure_types": [],
            "experience_signal_summary": "not_evaluated",
            "experience_action_bias": {},
            # Controlled Web Orchestration V1
            "web_decision": "off",
            "web_decision_reason": "not_evaluated",
            "selector_output_snapshot": {},
            "strategy_short_explanation": "",
            "strategy_selected": {},
            "execution_mode": "direct",
            "escalation_path": {},
            "escalation_final_mode": "direct",
            "escalation_use_reasoning": False,
            "escalation_use_tools": False,
        }

        try:
            from aihub.vault.service import classify_vault_intent

            result["vault_intent"] = classify_vault_intent(turn.message or "")
        except Exception:
            result["vault_intent"] = None

        # 1. Strategy selection (bounded: 1 memory + 1 psyche call internally)
        try:
            from aihub.goal_engine import get_goal_engine
            from aihub.strategy_selector import select_strategy

            try:
                _active_goals = get_goal_engine().get_active_goals(turn.user_id)
                if _active_goals:
                    _max_urgency = max(g.urgency for g in _active_goals)
                    _active_goals_summary: dict | None = {
                        "active_count": len(_active_goals),
                        "max_urgency": _max_urgency,
                    }
                    _top = max(_active_goals, key=lambda g: g.urgency)
                    result["selected_goal"] = {
                        "goal_id": _top.goal_id,
                        "title": _top.title,
                        "urgency": _top.urgency,
                    }
                else:
                    _active_goals_summary = None
            except Exception:
                logger.debug("Decision core: active goals lookup failed", exc_info=True)
                _active_goals_summary = None

            selection = select_strategy(
                user_id=turn.user_id,
                user_text=turn.message or "",
                mode=ctx.mode,
                active_goals_summary=_active_goals_summary,
                history=list(turn.history or []),
            )
            result["selected_strategy"] = selection.selected_strategy
            result["reason_codes"] = list(selection.reason_codes)
            result["strategy_confidence"] = selection.confidence
            result["strategy_degraded"] = selection.degraded
            result["selector_output_snapshot"] = dict(selection.selector_output)
            result["strategy_short_explanation"] = selection.short_explanation or ""
            # Controlled Web Orchestration V1
            result["web_decision"] = selection.web_decision
            result["web_decision_reason"] = selection.web_decision_reason
        except Exception:
            logger.debug("Decision core: strategy selection failed", exc_info=True)
            result["reason_codes"] = ["SELECTOR_TIMEOUT_FALLBACK"]
            result["strategy_degraded"] = True

        # 1b. ExperienceMemory read-path (execution-driving, not trace-only)
        try:
            experience_signal = self._lookup_experience_signal(
                user_id=turn.user_id,
                message=turn.message or "",
                selected_strategy=result["selected_strategy"],
            )
            result["experience_lookup_happened"] = bool(
                experience_signal.get("lookup_happened", False)
            )
            result["experience_matches_count"] = int(
                experience_signal.get("matches_count", 0) or 0
            )
            result["experience_confidence_adjustment"] = experience_signal.get(
                "confidence_adjustment"
            )
            result["experience_handoff_bias"] = experience_signal.get("handoff_bias")
            result["experience_blocker_reason"] = experience_signal.get(
                "blocker_reason"
            )
            result["experience_blocker_severity"] = experience_signal.get(
                "blocker_severity"
            )
            result["experience_recurring_failure_detected"] = bool(
                experience_signal.get("recurring_failure_detected", False)
            )
            result["experience_recurring_failure_types"] = list(
                experience_signal.get("recurring_failure_types") or []
            )
            result["experience_signal_summary"] = str(
                experience_signal.get("experience_signal_summary") or "not_evaluated"
            )
            result["experience_action_bias"] = dict(
                experience_signal.get("action_bias") or {}
            )

            # Deliberation history: propagate to decision_core so run_deliberation() can consume it
            result["deliberation_history"] = (
                experience_signal.get("deliberation_history") or {}
            )

            recommended = experience_signal.get("recommended_strategy")
            if (
                isinstance(recommended, str)
                and recommended
                and recommended != result["selected_strategy"]
            ):
                result["selected_strategy"] = recommended
                result["experience_influenced_strategy"] = True
                result["reason_codes"].append("EXPERIENCE_STRATEGY_BIAS")

            conf_adjust = experience_signal.get("confidence_adjustment")
            if isinstance(conf_adjust, (int, float)):
                base_conf = float(result.get("strategy_confidence") or 0.7)
                result["strategy_confidence"] = round(
                    max(0.30, min(0.95, base_conf + float(conf_adjust))),
                    3,
                )
                if abs(float(conf_adjust)) >= 0.03:
                    result["reason_codes"].append("EXPERIENCE_CONFIDENCE_ADJUST")

            blocker_reason = experience_signal.get("blocker_reason")
            blocker_severity = float(experience_signal.get("blocker_severity") or 0.0)
            if blocker_reason and not skip_experience_blocker_escalation(
                turn.message or ""
            ):
                result["reason_codes"].append("EXPERIENCE_CAUTION")
                caution = f"[Experience caution: {blocker_reason}]"
                result["strategy_hints"] = (
                    (result["strategy_hints"] + " " + caution).strip()
                    if result["strategy_hints"]
                    else caution
                )
                if (
                    result["selected_strategy"] == "instant"
                    and blocker_severity >= 0.60
                ):
                    result["selected_strategy"] = "contextual"
                    result["experience_influenced_strategy"] = True
                    result["reason_codes"].append(
                        "EXPERIENCE_BLOCKER_CONTEXTUAL_UPGRADE"
                    )
        except Exception:
            logger.debug("Decision core: experience signal failed", exc_info=True)

        # 1c. V2 REAL INFLUENCE: Memory + Psyche affect strategy
        try:
            from aihub.runtime_memory_bridge import build_memory_v2_runtime_snapshot
            from aihub.runtime_psyche_bridge import build_psyche_v2_runtime_snapshot

            _mctx = memory_v2_runtime_ctx
            _pctx = psyche_v2_behavior_ctx
            if _mctx is not None and getattr(_mctx, "loaded", False):
                memory_v2_actionable_contradictions = len(_mctx.contradiction_alerts)
                memory_v2_contradictions_count = (
                    memory_v2_actionable_contradictions
                    + len(_mctx.transient_contradiction_hints)
                )
                memory_v2_match_count = len(_mctx.top_facts) + len(
                    _mctx.top_preferences
                )
            else:
                memory_v2_snapshot = build_memory_v2_runtime_snapshot(
                    turn.user_id, turn.message or ""
                )
                memory_v2_contradictions_count = memory_v2_snapshot.get(
                    "contradictions_count", 0
                )
                memory_v2_actionable_contradictions = memory_v2_snapshot.get(
                    "actionable_contradictions_count", memory_v2_contradictions_count
                )
                memory_v2_match_count = memory_v2_snapshot.get("match_count", 0)

            if _pctx is not None and getattr(_pctx, "loaded", False):
                psyche_v2_mode = _pctx.mode
                psyche_v2_relation_trust = float(_pctx.trust)
            else:
                psyche_v2_snapshot = build_psyche_v2_runtime_snapshot(turn.user_id)
                psyche_v2_mode = psyche_v2_snapshot.get("mode", "neutral")
                psyche_v2_relation_trust = psyche_v2_snapshot.get("relation_trust", 0.5)

            memory_influenced_strategy = False
            psyche_influenced_strategy = False

            if (
                memory_v2_actionable_contradictions > 0
                and result["selected_strategy"] == "instant"
            ):
                result["selected_strategy"] = "contextual"
                result["reason_codes"].append("MEMORY_V2_CONTRADICTIONS")
                memory_influenced_strategy = True
                logger.info(f"V2: contradictions → contextual (user={turn.user_id})")

            if memory_v2_match_count > 0:
                base_conf = float(result.get("strategy_confidence") or 0.7)
                result["strategy_confidence"] = min(0.95, base_conf + 0.1)
                result["reason_codes"].append("MEMORY_V2_CONTEXT_BOOST")

            if (
                psyche_v2_mode == "exploratory"
                and result["selected_strategy"] == "instant"
            ):
                result["selected_strategy"] = "contextual"
                result["reason_codes"].append("PSYCHE_V2_EXPLORATORY")
                psyche_influenced_strategy = True
                logger.info(f"V2: exploratory mode → contextual (user={turn.user_id})")

            if psyche_v2_mode == "cautious":
                base_conf = float(result.get("strategy_confidence") or 0.7)
                result["strategy_confidence"] = max(0.3, base_conf - 0.15)
                result["reason_codes"].append("PSYCHE_V2_CAUTIOUS")

            if psyche_v2_relation_trust < 0.3:
                base_conf = float(result.get("strategy_confidence") or 0.7)
                result["strategy_confidence"] = max(0.3, base_conf - 0.1)
                result["reason_codes"].append("PSYCHE_V2_LOW_TRUST")

            if _pctx is not None and getattr(_pctx, "loaded", False):
                cd = getattr(_pctx, "consistency_decision", "allow")
                if cd in ("dampen", "suppress"):
                    fc = float(result.get("strategy_confidence") or 0.7)
                    drop = 0.065 if cd == "suppress" else 0.038
                    result["strategy_confidence"] = max(0.33, fc - drop)
                    result["reason_codes"].append(
                        f"SELF_CONSISTENCY_CONF_{str(cd).upper()}"
                    )

            result["memory_influenced_strategy_chat"] = memory_influenced_strategy
            result["psyche_influenced_strategy_chat"] = psyche_influenced_strategy

        except Exception as v2_error:
            logger.debug(
                f"Decision core: V2 influence failed: {v2_error}", exc_info=True
            )

        # 2. Policy profile (user's history of action outcomes, window=50)
        try:
            from aihub.policy_engine import (
                build_policy_profile,
                compute_policy_feedback,
            )

            profile = build_policy_profile(turn.user_id, window=50)
            result["policy_hints_loaded"] = True
            result["policy_profile_name"] = (
                f"rel={profile.reliability_index:.2f}_refs={profile.total_reflections}"
            )
            result["policy_hints"] = profile.hints
            actionable = [
                h
                for h in profile.hints[:5]
                if h.signal in ("boost", "penalize", "avoid")
            ]
            if actionable:
                hints_text = "; ".join(
                    f"{h.action_type}={h.signal}" for h in actionable
                )
                result["strategy_hints"] = (
                    f"[Policy z historii: {hints_text}. "
                    f"Reliability={profile.reliability_index:.2f}]"
                )

            # ── PolicyFeedback: numeric deltas from reflection hindsight ──
            feedback = compute_policy_feedback(profile)
            result["policy_feedback"] = feedback
            result["policy_feedback_applied"] = feedback.applied
            result["policy_feedback_loaded"] = True
            result["policy_confidence_delta"] = feedback.confidence_delta
            result["policy_feedback_summary"] = feedback.summary or ""
            if feedback.applied:
                # 2a. confidence_delta → adjust strategy_confidence
                if abs(feedback.confidence_delta) >= 0.005:
                    base_conf = float(result.get("strategy_confidence") or 0.7)
                    new_conf = round(
                        max(0.20, min(0.95, base_conf + feedback.confidence_delta)),
                        3,
                    )
                    if new_conf != base_conf:
                        result["strategy_confidence"] = new_conf
                        result["reason_codes"].append("POLICY_FEEDBACK_CONFIDENCE")

                # 2b. handoff_bias → bias for handoff gating
                if abs(feedback.handoff_bias) >= 0.01:
                    existing_bias = float(result.get("experience_handoff_bias") or 0.0)
                    result["policy_handoff_bias"] = round(
                        max(-0.50, min(0.50, existing_bias + feedback.handoff_bias)),
                        4,
                    )
                    result["reason_codes"].append("POLICY_FEEDBACK_HANDOFF")
                else:
                    result["policy_handoff_bias"] = float(
                        result.get("experience_handoff_bias") or 0.0
                    )

                # 2c. blocker_sensitivity → tune blocker thresholds
                if abs(feedback.blocker_sensitivity) >= 0.01:
                    result["policy_blocker_sensitivity"] = feedback.blocker_sensitivity
                    result["reason_codes"].append("POLICY_FEEDBACK_BLOCKER")
                else:
                    result["policy_blocker_sensitivity"] = 0.0

                # 2d. simulation_risk_calibration → risk offset for simulation
                result["policy_simulation_risk_cal"] = (
                    feedback.simulation_risk_calibration
                )

                # 2e. strategy_adjustments → per-action-type score shift
                if feedback.strategy_adjustments:
                    result["policy_strategy_adjustments"] = dict(
                        feedback.strategy_adjustments
                    )
                    result["reason_codes"].append("POLICY_FEEDBACK_STRATEGY")
                    # Apply: if current strategy's action has negative delta ≤ -0.15,
                    # and another strategy's action has positive delta, switch.
                    _S2A_FB = {
                        "instant": "reason",
                        "contextual": "memory_search",
                        "research": "research",
                        "agentic": "action",
                    }
                    _A2S_FB = {v: k for k, v in _S2A_FB.items()}
                    cur_action = _S2A_FB.get(result["selected_strategy"], "reason")
                    cur_delta = feedback.strategy_adjustments.get(cur_action, 0.0)
                    if cur_delta <= -0.15:
                        best_alt = max(
                            (
                                (act, d)
                                for act, d in feedback.strategy_adjustments.items()
                                if act != cur_action and d > 0
                            ),
                            key=lambda x: x[1],
                            default=(None, 0.0),
                        )
                        if best_alt[0] and best_alt[0] in _A2S_FB:
                            result["selected_strategy"] = _A2S_FB[best_alt[0]]
                            result["reason_codes"].append(
                                f"POLICY_STRATEGY_SHIFT:{cur_action}->{best_alt[0]}"
                            )
                else:
                    result["policy_strategy_adjustments"] = {}
            else:
                result["policy_feedback"] = feedback
                result["policy_feedback_applied"] = False
                result["policy_confidence_delta"] = 0.0
                result["policy_feedback_summary"] = ""
                result["policy_handoff_bias"] = float(
                    result.get("experience_handoff_bias") or 0.0
                )
                result["policy_blocker_sensitivity"] = 0.0
                result["policy_simulation_risk_cal"] = 0.0
                result["policy_strategy_adjustments"] = {}

        except Exception:
            logger.debug("Decision core: policy profile failed", exc_info=True)

        # 3. Simulation (pre-execution, predictive — maps strategy→action type)
        _STRATEGY_TO_ACTION: dict[str, str] = {
            "instant": "reason",
            "contextual": "memory_search",
            "research": "research",
            "agentic": "action",
        }
        try:
            from aihub.simulation_engine import simulate_action

            strategy = result["selected_strategy"]
            action_type = _STRATEGY_TO_ACTION.get(strategy, "reason")
            # Pass psyche state so simulation can modulate confidence via energy/focus
            _psyche_compact: dict[str, Any] = {}
            if psyche_snapshot:
                _psyche_compact = {
                    "energy": float(psyche_snapshot.get("energy", 0.7)),
                    "focus": float(psyche_snapshot.get("focus", 0.65)),
                    "mood": float(psyche_snapshot.get("mood", 0.5)),
                }
            sim_context = {
                "policy_hints": [
                    {
                        "action_type": h.action_type,
                        "signal": h.signal,
                        "weight": h.weight,
                    }
                    for h in result["policy_hints"][:5]
                ],
                "web_triggered": result["web_decision"] != "off",
                "mode": ctx.mode,
                "psyche_state": _psyche_compact,
                "experience_signal": {
                    "action_bias": result.get("experience_action_bias", {}),
                    "blocker_reason": result.get("experience_blocker_reason"),
                    "summary": result.get("experience_signal_summary"),
                },
                "risk_calibration": float(
                    result.get("policy_simulation_risk_cal") or 0.0
                ),
            }
            sim_result = simulate_action(
                turn.user_id,
                action_type,
                {"message": (turn.message or "")[:200]},
                sim_context,
                max_variants=4,
            )
            result["simulation_ran"] = True
            result["simulation_variants_count"] = sim_result.variants_evaluated
            if sim_result.best_variant:
                bv = sim_result.best_variant
                # Apply policy risk calibration to simulation risk
                _risk_cal = float(result.get("policy_simulation_risk_cal") or 0.0)
                calibrated_risk = max(0.0, min(1.0, bv.risk + _risk_cal))
                result["simulation_best_action"] = bv.action_type
                result["simulation_risk_summary"] = (
                    f"risk={calibrated_risk:.2f} conf={bv.confidence:.2f} util={bv.utility:.2f}"
                )
                # Simulation-to-strategy bridge: override selected_strategy when
                # simulation strongly recommends a different action type.
                _ACTION_TO_STRATEGY: dict[str, str] = {
                    "memory_search": "contextual",
                    "research": "research",
                    "action": "agentic",
                    "reason": "instant",
                    "web_request": "research",
                }
                sim_suggested = _ACTION_TO_STRATEGY.get(bv.action_type)
                _current = result["selected_strategy"]
                if (
                    sim_suggested
                    and sim_suggested != _current
                    and bv.composite_score >= 0.72
                    and bv.confidence >= 0.60
                    and not result.get("strategy_degraded")
                ):
                    result["selected_strategy"] = sim_suggested
                    result["reason_codes"].append("SIMULATION_OVERRIDE")
                    result["strategy_confidence"] = round(bv.composite_score, 3)
        except Exception:
            logger.debug("Decision core: simulation failed", exc_info=True)

        # 4. Consistency check on incoming user message
        try:
            from aihub.consistency_engine import check_consistency

            verdict = check_consistency(turn.user_id, turn.message or "")
            result["consistency_check_ran"] = True
            result["consistency_classification"] = verdict.classification
            if verdict.classification == "conflict":
                result["contradictions_found"] = 1
                result["reason_codes"].append("CONSISTENCY_CONFLICT")
                # Reduce confidence — contradictory claims require careful handling
                _conf = result.get("strategy_confidence") or 0.7
                result["strategy_confidence"] = round(max(0.35, _conf * 0.80), 3)
                # Upgrade strategy so runtime uses context to resolve the contradiction
                _strat = result["selected_strategy"]
                if _strat == "instant":
                    result["selected_strategy"] = "contextual"
                    result["reason_codes"].append("CONSISTENCY_FORCED_CONTEXTUAL")
                note = "[Spójność: potencjalna sprzeczność — strategia upgraded, confidence −20%]"
                result["strategy_hints"] = (
                    (result["strategy_hints"] + " " + note).strip()
                    if result["strategy_hints"]
                    else note
                )
            elif verdict.classification in ("revision", "uncertain"):
                result["reason_codes"].append(
                    f"CONSISTENCY_{verdict.classification.upper()}"
                )
                # Mild caution: slight confidence reduction
                _conf = result.get("strategy_confidence") or 0.7
                result["strategy_confidence"] = round(max(0.4, _conf * 0.93), 3)
        except Exception:
            logger.debug("Decision core: consistency check failed", exc_info=True)

        self._local_non_research_guardrails(turn, result)
        self._finalize_escalation(result)
        result["user_turn_text"] = turn.message or ""
        return result

    def _finalize_escalation(self, decision_core: dict[str, Any]) -> None:
        """Derive execution_mode / escalation_path from final selected_strategy."""
        from aihub.decision_engine import decide_execution_path

        strat = str(decision_core.get("selected_strategy") or "instant")
        conf = decision_core.get("strategy_confidence")
        merged: dict[str, Any] = dict(
            decision_core.get("selector_output_snapshot") or {}
        )
        merged["strategy"] = strat
        if conf is not None:
            merged["confidence"] = float(conf)
        merged["requires_memory"] = strat in ("contextual", "agentic")
        merged["requires_research"] = strat == "research"
        merged["requires_planning"] = strat == "agentic"
        if not str(merged.get("reason") or "").strip():
            merged["reason"] = str(
                decision_core.get("strategy_short_explanation")
                or decision_core.get("strategy_hints")
                or ""
            )[:400]

        path = decide_execution_path(merged)
        decision_core["strategy_selected"] = merged
        decision_core["execution_mode"] = path["final_mode"]
        decision_core["escalation_path"] = dict(path)
        decision_core["escalation_final_mode"] = path["final_mode"]
        decision_core["escalation_use_reasoning"] = path["use_reasoning"]
        decision_core["escalation_use_tools"] = path["use_tools"]

    @staticmethod
    def _decision_core_trace_escalation(
        decision_core: dict[str, Any],
    ) -> dict[str, Any]:
        """Stable trace slice: strategy + escalation engine output (observability)."""
        out = {
            "strategy_selected": decision_core.get("strategy_selected", {}),
            "execution_mode": decision_core.get("execution_mode"),
            "escalation_path": decision_core.get("escalation_path", {}),
            "escalation_use_reasoning": bool(
                decision_core.get("escalation_use_reasoning", False)
            ),
            "escalation_use_tools": bool(
                decision_core.get("escalation_use_tools", False)
            ),
            "selector_output_snapshot": dict(
                decision_core.get("selector_output_snapshot") or {}
            ),
        }
        if decision_core.get("chat_handoff_evaluated"):
            out["chat_handoff_evaluated"] = True
            if "chat_handoff_executed" in decision_core:
                out["chat_handoff_executed"] = decision_core["chat_handoff_executed"]
            out["chat_handoff_skip_reason"] = decision_core.get(
                "chat_handoff_skip_reason"
            )
        return out

    @staticmethod
    def _evaluate_blocker_verdict(
        decision_core: dict[str, Any],
    ) -> BlockerVerdict:
        """Evaluate all decision_core signals into a single BlockerVerdict.

        Collects ALL matching signals (not first-match), then selects
        the highest-priority one as the winning verdict.  Lower-priority
        signals are recorded in contributing_signals for observability.

        Priority bands:
          P0 (hard_block):
            R1 – consistency_conflict + contradictions ≥ 1 + confidence < 0.40
            R2 – repeated_failure: experience blocker_severity ≥ 0.80
            R3 – degraded_runtime: degraded + confidence < 0.35
            R4 – policy_violation_internal: policy "avoid" signal with weight ≥ 0.70
          P1 (downgrade/reroute):
            R5 – high_risk_path: simulation risk ≥ 0.80 + strategy is agentic/research
            R6 – low_confidence_decision: confidence < 0.45 (not degraded)
          P2 (caution_pass):
            R7 – consistency_conflict (mild)
            R8 – repeated_failure (mild): experience blocker present
            R9 – degraded_runtime (mild): degraded but confidence ≥ 0.35
            R10– high_risk_path (mild): sim risk ≥ 0.65
            R11– contradictory_memory_state: experience matches with mixed outcomes
            R12– resource_exhaustion: rate-limit / tool failure hints

        Feedback loop:
          - If policy hints contain "avoid" for current action_type,
            escalate caution → hard_block.
          - If recent experience shows 3+ repeated failures of same type,
            escalate caution → hard_block.
          - If policy hints contain "boost" for current action_type,
            de-escalate hard → caution (unless consistency-based).
        """

        import time as _time

        _user_turn_for_block = str(decision_core.get("user_turn_text") or "")
        if is_image_generation_intent(_user_turn_for_block):
            return BlockerVerdict()

        # ── Extract raw signals ──────────────────────────────────────
        consistency_class = decision_core.get("consistency_classification") or ""
        contradictions = int(decision_core.get("contradictions_found") or 0)
        confidence = float(decision_core.get("strategy_confidence") or 0.7)
        degraded = bool(decision_core.get("strategy_degraded"))
        selected_strategy = str(decision_core.get("selected_strategy") or "instant")

        exp_blocker = decision_core.get("experience_blocker_reason") or ""
        exp_severity = float(decision_core.get("experience_blocker_severity") or 0.0)
        exp_recurring_types: list[str] = list(
            decision_core.get("experience_recurring_failure_types") or []
        )
        exp_recurring = bool(
            decision_core.get("experience_recurring_failure_detected", False)
        )

        sim_risk_raw = decision_core.get("simulation_risk_summary") or ""
        sim_ran = bool(decision_core.get("simulation_ran"))

        # Policy hints from decision_core (list of PolicyHint-like dicts)
        policy_hints: list[dict[str, Any]] = list(
            decision_core.get("policy_hints") or []
        )
        policy_profile_name = decision_core.get("policy_profile_name") or ""

        skip_exp = skip_experience_blocker_escalation(
            str(decision_core.get("user_turn_text") or "")
        )

        # Parse simulation risk from "risk=0.82 conf=0.45 util=0.33"
        sim_risk = 0.0
        sim_confidence = 0.0
        if sim_ran and "risk=" in sim_risk_raw:
            try:
                sim_risk = float(sim_risk_raw.split("risk=")[1].split()[0])
            except (ValueError, IndexError) as exc:
                logger.debug("Simulation risk parsing failed: %s", exc)
            try:
                sim_confidence = float(sim_risk_raw.split("conf=")[1].split()[0])
            except (ValueError, IndexError) as exc:
                logger.debug("Simulation risk parsing failed: %s", exc)

        # ── Policy feedback extraction ───────────────────────────────
        # Extract "avoid" and "penalize" signals relevant to the current
        # strategy.  These feed the escalation/de-escalation logic.
        _STRATEGY_TO_ACTION: dict[str, str] = {
            "instant": "reason",
            "contextual": "memory_search",
            "research": "research",
            "agentic": "action",
        }
        current_action_type = _STRATEGY_TO_ACTION.get(selected_strategy, "reason")

        policy_avoid_weight = 0.0
        policy_penalize_weight = 0.0
        policy_boost_weight = 0.0
        policy_avoid_reason = ""
        for hint in policy_hints:
            h_action = ""
            h_signal = ""
            h_weight = 0.0
            h_reason = ""
            if hasattr(hint, "action_type"):  # PolicyHint dataclass
                h_action = hint.action_type
                h_signal = hint.signal
                h_weight = hint.weight
                h_reason = getattr(hint, "reason", "")
            elif isinstance(hint, dict):
                h_action = str(hint.get("action_type") or "")
                h_signal = str(hint.get("signal") or "")
                h_weight = float(hint.get("weight") or 0.0)
                h_reason = str(hint.get("reason") or "")

            if h_action != current_action_type:
                continue
            if h_signal == "avoid":
                policy_avoid_weight = max(policy_avoid_weight, h_weight)
                policy_avoid_reason = h_reason
            elif h_signal == "penalize":
                policy_penalize_weight = max(policy_penalize_weight, h_weight)
            elif h_signal == "boost":
                policy_boost_weight = max(policy_boost_weight, h_weight)

        # ── Collect candidate verdicts ───────────────────────────────
        # Each candidate: (priority, severity_rank, verdict)
        # severity_rank: 3=hard, 2=caution, 1=info
        candidates: list[tuple[int, int, BlockerVerdict]] = []
        all_signals: list[str] = []

        # ── Policy blocker sensitivity adjustment ────────────────────
        # Positive value → more sensitive (lower thresholds → more blockers)
        # Negative value → less sensitive (higher thresholds → fewer blockers)
        _blocker_sens = float(decision_core.get("policy_blocker_sensitivity") or 0.0)
        # Clamp to [-0.15, +0.15] to prevent extreme shifts
        _blocker_sens = max(-0.15, min(0.15, _blocker_sens))
        # Confidence threshold adjustment: sensitivity up → threshold goes up
        # (easier to trigger low-confidence blockers)
        _conf_hard_thresh = 0.40 + _blocker_sens  # default 0.40
        _conf_caution_thresh = 0.45 + _blocker_sens  # default 0.45
        # Severity threshold adjustment: sensitivity up → threshold goes down
        # (easier to trigger severity-based blockers)
        _sev_hard_thresh = 0.80 - _blocker_sens  # default 0.80
        # Simulation risk threshold adjustment: sensitivity up → threshold goes down
        _risk_hard_thresh = 0.80 - _blocker_sens  # default 0.80
        _risk_caution_thresh = 0.65 - _blocker_sens  # default 0.65

        # ── P0: Hard gates ───────────────────────────────────────────

        # R1: Hard consistency conflict
        if (
            consistency_class == "conflict"
            and contradictions >= 1
            and confidence < _conf_hard_thresh
        ):
            sigs = [
                "consistency_classification",
                "contradictions_found",
                "strategy_confidence",
            ]
            all_signals.extend(sigs)
            candidates.append(
                (
                    0,
                    3,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="consistency_conflict",
                        blocker_scope="turn",
                        blocker_severity="hard",
                        hard=True,
                        resolution="hard_block",
                        reason=f"Sprzeczność w wypowiedzi (confidence={confidence:.2f}). "
                        f"Wymagane wyjaśnienie.",
                        source="consistency_engine",
                        recommended_action="Przeformułuj pytanie eliminując sprzeczne stwierdzenia.",
                        contributing_signals=sigs,
                        confidence=min(1.0, 1.0 - confidence),
                        user_message="Jest sprzeczność w treści — doprecyzuj krótko, o co chodzi.",
                        dev_message=f"consistency_conflict: class={consistency_class} "
                        f"contradictions={contradictions} conf={confidence:.3f}",
                        remediation_hint="Wyjaśnij sprzeczne stwierdzenia w pytaniu.",
                    ),
                )
            )

        # R2: Hard repeated failure
        if exp_blocker and exp_severity >= _sev_hard_thresh and not skip_exp:
            sigs = ["experience_blocker_reason", "experience_blocker_severity"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    0,
                    3,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="repeated_failure",
                        blocker_scope="turn",
                        blocker_severity="hard",
                        hard=True,
                        resolution="hard_block",
                        reason=f"Krytyczny wzorzec porażek: {exp_blocker} (severity={exp_severity:.2f}).",
                        source="experience_memory",
                        recommended_action="Zmień podejście lub potwierdź kontynuację.",
                        contributing_signals=sigs,
                        confidence=exp_severity,
                        user_message="Podobne tury wcześniej się wyłożyły — zmień pytanie albo potwierdź, że jedziemy dalej.",
                        dev_message=f"repeated_failure: reason={exp_blocker} sev={exp_severity:.2f} "
                        f"recurring_types={exp_recurring_types}",
                        remediation_hint="Zmień strategię lub parametry zapytania.",
                        escalated_from_history=True,
                        feedback_applied=True,
                        feedback_detail=f"Recurring failures ({exp_recurring_types}) escalated to hard.",
                    ),
                )
            )

        # R3: Hard degraded runtime
        if degraded and confidence < (_conf_hard_thresh - 0.05):
            sigs = ["strategy_degraded", "strategy_confidence"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    0,
                    3,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="degraded_runtime",
                        blocker_scope="turn",
                        blocker_severity="hard",
                        hard=True,
                        resolution="hard_block",
                        reason=f"Runtime zdegradowany, brak pewności (confidence={confidence:.2f}).",
                        source="strategy_selector",
                        recommended_action="Spróbuj za chwilę albo uprość zapytanie.",
                        contributing_signals=sigs,
                        confidence=min(1.0, 1.0 - confidence),
                        user_message="Backend jest niepewny — spróbuj za chwilę albo uprość zapytanie.",
                        dev_message=f"degraded_runtime: degraded={degraded} conf={confidence:.3f}",
                        remediation_hint="Sprawdź logi strategy_selector; restart może pomóc.",
                    ),
                )
            )

        # R4: Hard policy violation
        if policy_avoid_weight >= 0.70:
            sigs = ["policy_avoid", "policy_profile"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    0,
                    3,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="policy_violation_internal",
                        blocker_scope="session",
                        blocker_severity="hard",
                        hard=True,
                        resolution="hard_block",
                        reason=f"Polityka zabrania akcji '{current_action_type}' "
                        f"(avoid weight={policy_avoid_weight:.2f}). "
                        f"{policy_avoid_reason}",
                        source="policy_engine",
                        recommended_action="Użyj innej strategii lub skontaktuj się z operatorem.",
                        contributing_signals=sigs,
                        confidence=policy_avoid_weight,
                        user_message="To jest zablokowane ustawieniami.",
                        dev_message=f"policy_violation: avoid_weight={policy_avoid_weight:.2f} "
                        f"action={current_action_type} profile={policy_profile_name}",
                        feedback_applied=True,
                        escalated_from_history=True,
                        feedback_detail=f"Policy 'avoid' signal from reflection history "
                        f"(weight={policy_avoid_weight:.2f}).",
                    ),
                )
            )

        # ── P1: Downgrade / Reroute ─────────────────────────────────

        # R5: High risk path with downgrade
        if (
            sim_ran
            and sim_risk >= _risk_hard_thresh
            and selected_strategy in ("agentic", "research")
        ):
            downgrade_to = "contextual" if selected_strategy == "agentic" else "instant"
            sigs = ["simulation_risk_summary", "selected_strategy"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    1,
                    2,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="high_risk_path",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="downgrade",
                        reason=f"Symulacja: ryzyko={sim_risk:.2f} dla '{selected_strategy}'. "
                        f"Downgrade do '{downgrade_to}'.",
                        source="simulation_engine",
                        recommended_action=f"Strategia obniżona do {downgrade_to}.",
                        contributing_signals=sigs,
                        confidence=sim_risk,
                        user_message="Wybieram bezpieczniejsze podejście ze względu na złożoność pytania.",
                        dev_message=f"high_risk_path: risk={sim_risk:.2f} conf={sim_confidence:.2f} "
                        f"downgrade {selected_strategy}→{downgrade_to}",
                        next_best_action=downgrade_to,
                    ),
                )
            )

        # R6: Low confidence decision → reroute to simpler strategy
        if (
            confidence < _conf_caution_thresh
            and not degraded
            and selected_strategy in ("agentic", "research")
        ):
            reroute_to = "contextual"
            sigs = ["strategy_confidence", "selected_strategy"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    1,
                    2,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="low_confidence_decision",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="reroute",
                        reason=f"Niska pewność decyzji (confidence={confidence:.2f}) "
                        f"dla strategii '{selected_strategy}'. Reroute do '{reroute_to}'.",
                        source="strategy_selector",
                        recommended_action=f"Reroute do strategii {reroute_to}.",
                        contributing_signals=sigs,
                        confidence=min(1.0, 1.0 - confidence),
                        user_message="Koryguję podejście na bardziej bezpieczne.",
                        dev_message=f"low_confidence: conf={confidence:.3f} "
                        f"reroute {selected_strategy}→{reroute_to}",
                        next_best_action=reroute_to,
                    ),
                )
            )

        # ── P2: Caution pass ────────────────────────────────────────

        # R7: Mild consistency conflict
        if (
            consistency_class == "conflict"
            and contradictions >= 1
            and confidence >= 0.40
        ):
            sigs = ["consistency_classification", "contradictions_found"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    2,
                    2,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="consistency_conflict",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="caution_pass",
                        reason=f"Potencjalna sprzeczność (contradictions={contradictions}).",
                        source="consistency_engine",
                        recommended_action="Rozważ wyjaśnienie sprzecznych stwierdzeń.",
                        contributing_signals=sigs,
                        confidence=0.5 + (0.5 * (1.0 - confidence)),
                        user_message="Możliwa sprzeczność w pytaniu.",
                        dev_message=f"mild_consistency: class={consistency_class} "
                        f"contradictions={contradictions} conf={confidence:.3f}",
                    ),
                )
            )

        # R8: Mild experience blocker
        if exp_blocker and exp_severity < 0.80 and not skip_exp:
            sigs = ["experience_blocker_reason"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    2,
                    2,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="repeated_failure",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="caution_pass",
                        reason=f"Historia: {exp_blocker}.",
                        source="experience_memory",
                        recommended_action="Zachowaj ostrożność.",
                        contributing_signals=sigs,
                        confidence=max(0.3, exp_severity),
                        user_message="Z historii podobnych tur: ostrożniej w tej turze.",
                        dev_message=f"mild_experience: reason={exp_blocker} sev={exp_severity:.2f}",
                        feedback_applied=bool(exp_recurring),
                        feedback_detail=(
                            f"Recurring={exp_recurring}, types={exp_recurring_types}"
                            if exp_recurring
                            else ""
                        ),
                    ),
                )
            )

        # R9: Mild degraded runtime
        if degraded and confidence >= (_conf_hard_thresh - 0.05):
            sigs = ["strategy_degraded"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    2,
                    2,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="degraded_runtime",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="caution_pass",
                        reason=f"Runtime zdegradowany (confidence={confidence:.2f}).",
                        source="strategy_selector",
                        recommended_action="Wynik może być mniej precyzyjny.",
                        contributing_signals=sigs,
                        confidence=min(1.0, 1.0 - confidence),
                        user_message="Wynik może być mniej precyzyjny niż zwykle.",
                        dev_message=f"mild_degraded: degraded={degraded} conf={confidence:.3f}",
                    ),
                )
            )

        # R10: Mild sim risk
        if sim_ran and _risk_caution_thresh <= sim_risk < _risk_hard_thresh:
            sigs = ["simulation_risk_summary"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    2,
                    1,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="high_risk_path",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="caution_pass",
                        reason=f"Umiarkowane ryzyko symulacji (risk={sim_risk:.2f}).",
                        source="simulation_engine",
                        recommended_action="Rozważ uproszczenie zapytania.",
                        contributing_signals=sigs,
                        confidence=sim_risk,
                        user_message="Dość złożone pytanie — odpowiadam ostrożniej.",
                        dev_message=f"mild_sim_risk: risk={sim_risk:.2f} conf={sim_confidence:.2f}",
                    ),
                )
            )

        # R11: Contradictory memory state
        exp_matches = int(decision_core.get("experience_matches_count") or 0)
        exp_conf_adj = float(
            decision_core.get("experience_confidence_adjustment") or 0.0
        )
        if exp_matches >= 4 and abs(exp_conf_adj) < 0.02 and not skip_exp:
            # Many matches but net-zero signal → mixed outcomes
            sigs = ["experience_matches_count", "experience_confidence_adjustment"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    2,
                    1,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="contradictory_memory_state",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="caution_pass",
                        reason=f"Sprzeczne doświadczenia: {exp_matches} dopasowań z mieszanymi wynikami.",
                        source="experience_memory",
                        recommended_action="Wyniki mogą być niejednoznaczne.",
                        contributing_signals=sigs,
                        confidence=0.45,
                        user_message="Mieszane wyniki w historii podobnych tur.",
                        dev_message=f"contradictory_memory: matches={exp_matches} "
                        f"conf_adj={exp_conf_adj:.3f}",
                    ),
                )
            )

        # R12: Resource exhaustion signals (policy penalize with high weight)
        if policy_penalize_weight >= 0.60:
            sigs = ["policy_penalize", "policy_profile"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    2,
                    1,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="resource_exhaustion",
                        blocker_scope="session",
                        blocker_severity="caution",
                        hard=False,
                        resolution="caution_pass",
                        reason=f"Polityka penalizuje '{current_action_type}' "
                        f"(weight={policy_penalize_weight:.2f}). Zachowaj ostrożność.",
                        source="policy_engine",
                        recommended_action="Rozważ zmianę podejścia.",
                        contributing_signals=sigs,
                        confidence=policy_penalize_weight,
                        user_message="Ostrożniej przy tym typie akcji.",
                        dev_message=f"resource_exhaustion: penalize_weight={policy_penalize_weight:.2f} "
                        f"action={current_action_type}",
                        feedback_applied=True,
                        feedback_detail=f"Policy penalize from reflections "
                        f"(weight={policy_penalize_weight:.2f}).",
                    ),
                )
            )

        # ── No candidates → clean pass ──────────────────────────────
        if not candidates:
            return BlockerVerdict.allow()

        # ── Select winner (lowest priority number, then highest severity) ─
        candidates.sort(key=lambda c: (c[0], -c[1]))
        _, _, winner = candidates[0]

        # ── Feedback loop: escalation / de-escalation ────────────────
        # Escalation: recurring experience failures can upgrade caution → hard
        if (
            not winner.hard
            and exp_recurring
            and len(exp_recurring_types) >= 2
            and not skip_exp
        ):
            winner.blocker_severity = "hard"
            winner.hard = True
            winner.resolution = "hard_block"
            winner.escalated_from_history = True
            winner.feedback_applied = True
            winner.feedback_detail = (
                f"Escalated from caution to hard: {len(exp_recurring_types)} "
                f"recurring failure types ({', '.join(exp_recurring_types[:3])})"
            )
            winner.dev_message += (
                f" [ESCALATED: recurring failures ×{len(exp_recurring_types)}]"
            )

        # Escalation: policy "avoid" can upgrade caution → hard
        elif (
            not winner.hard
            and policy_avoid_weight >= 0.55
            and winner.blocker_type != "consistency_conflict"
            and not skip_exp
        ):
            winner.blocker_severity = "hard"
            winner.hard = True
            winner.resolution = "hard_block"
            winner.escalated_from_history = True
            winner.feedback_applied = True
            winner.feedback_detail = (
                f"Escalated by policy avoid signal (weight={policy_avoid_weight:.2f})"
            )
            winner.dev_message += (
                f" [ESCALATED: policy avoid w={policy_avoid_weight:.2f}]"
            )

        # De-escalation: policy "boost" can downgrade hard → caution
        elif (
            winner.hard
            and policy_boost_weight >= 0.65
            and winner.blocker_type
            not in ("consistency_conflict", "policy_violation_internal")
        ):
            winner.blocker_severity = "caution"
            winner.hard = False
            winner.resolution = "caution_pass"
            winner.deescalated_from_history = True
            winner.feedback_applied = True
            winner.feedback_detail = f"De-escalated by policy boost signal (weight={policy_boost_weight:.2f})"
            winner.dev_message += (
                f" [DE-ESCALATED: policy boost w={policy_boost_weight:.2f}]"
            )

        # ── Finalize metadata ────────────────────────────────────────
        # Merge all signals from all candidates for full observability
        unique_signals = list(dict.fromkeys(all_signals))
        winner.contributing_signals = unique_signals
        winner.signals_count = len(unique_signals)
        winner.timestamp = _time.time()

        return winner

    @staticmethod
    def _apply_strategy_to_tools(
        tools: list[ProviderToolSpec],
        strategy: str,
    ) -> list[ProviderToolSpec]:
        """Restrict available tools to those relevant for the selected strategy."""
        _WHITELIST: dict[str, list[str] | None] = {
            # instant: model-only; lightweight memory reads for grounding only
            "instant": ["memory.search", "memory.get_context"],
            # contextual: memory-heavy; exclude web/research/planner/agent
            "contextual": [
                "memory.",
                "psyche.",
                "goal.",
                "runtime.status",
                "system.health",
            ],
            # research: web-forward; include memory for context but skip heavy agentic tools
            "research": [
                "research.",
                "web.",
                "memory.search",
                "memory.get_context",
                "goal.",
                "psyche.",
                "runtime.",
            ],
            # agentic: full tool set — no restriction
            "agentic": None,
        }
        whitelist = _WHITELIST.get(strategy)
        if whitelist is None:
            return tools
        filtered = [t for t in tools if any(t.name.startswith(p) for p in whitelist)]
        # Safety: never return an empty tool list — fall back to full set
        return filtered if filtered else tools

    def _should_handoff_to_agent(
        self,
        *,
        decision_core: dict[str, Any],
        message: str,
    ) -> tuple[bool, str]:
        """Determine if chat should handoff to agent runtime (planner+reasoning).

        Returns: (should_handoff, reason_code)

        Criteria (evidence-based from decision_core):
        1. selected_strategy in {research, agentic}
        2. simulation best_action in {research, action} + confidence >= 0.70
        3. active goal with urgency >= 0.7
        4. operational keywords indicating multi-step planning
        """
        strategy = decision_core.get("selected_strategy", "instant")
        handoff_bias = decision_core.get("experience_handoff_bias")

        # Policy feedback handoff bias (from reflection hindsight)
        policy_hoff_bias = float(decision_core.get("policy_handoff_bias") or 0.0)

        # Merge: experience handoff bias + policy feedback bias
        effective_handoff_bias = float(handoff_bias or 0.0) + policy_hoff_bias

        esc_mode = str(decision_core.get("escalation_final_mode") or "direct")

        # Criterion 0 (before experience/planner handoff): web required/optional
        # must stay on chat LLM+tools so Brave/fetch run in chat trace, not only
        # in executive handoff (which does not populate chat tool_results).
        web_decision = decision_core.get("web_decision", "off")
        # Tylko jawna potrzeba webu trzyma wykonanie w czacie (trace narzędzi).
        # agentic + optional bez URL nie blokuje planera — wieloetapowe zadania mogą iść w handoff.
        web_overrides_handoff = strategy == "research" or (
            web_decision == "required" and strategy == "agentic"
        )
        if web_overrides_handoff:
            return (
                False,
                f"web_decision={web_decision}_overrides_handoff(strategy={strategy})",
            )

        # Agentic → executive agent runtime by default (planner+reasoning), unless
        # web/research keeps chat tools, or policy/experience strongly vetoes handoff.
        if strategy == "agentic" and not web_overrides_handoff:
            if effective_handoff_bias <= -0.25:
                return (
                    False,
                    f"agentic_veto_handoff_bias={effective_handoff_bias:.2f}",
                )
            return True, "strategy_agentic_escalation|escalation_final_mode=planner"

        # Experience-driven handoff only when escalation already chose planner
        # (agentic → planner+reasoning). Do not bypass strategy/escalation layer.
        if effective_handoff_bias >= 0.25 and esc_mode == "planner":
            return True, f"effective_handoff_bias={effective_handoff_bias:.2f}"

        # Criterion 1: Escalation engine — planner mode → agent runtime handoff
        if esc_mode == "planner":
            if effective_handoff_bias <= -0.25:
                return False, f"experience_veto_handoff_bias={effective_handoff_bias:.2f}"
            else:
                return True, f"escalation_final_mode=planner(strategy={strategy})"

        # Criterion 1b: experience can veto planner handoff
        if esc_mode == "planner" and effective_handoff_bias <= -0.25:
            veto_reason = f"effective_bias_against_handoff={effective_handoff_bias:.2f}"
        else:
            veto_reason = None

        # Criterion 2: Simulation suggests complex action + high confidence
        if esc_mode == "planner" and decision_core.get("simulation_ran"):
            best_action = decision_core.get("simulation_best_action")
            confidence = decision_core.get("strategy_confidence") or 0.0
            if best_action in {"research", "action"} and confidence >= 0.70:
                return True, f"simulation={best_action}_conf={confidence:.2f}"

        # Criterion 3: High-urgency active goal
        selected_goal = decision_core.get("selected_goal")
        if selected_goal and esc_mode == "planner":
            urgency = float(selected_goal.get("urgency", 0.0))
            if urgency >= 0.7:
                return True, f"goal_urgency={urgency:.2f}"

        # Criterion 4: Multi-step operational keywords (only with planner escalation)
        message_lower = (message or "").lower()
        operational_patterns = [
            "zaplanuj",
            "wykonaj",
            "zrób",
            "sprawdź wszystkie",
            "przeanalizuj całość",
            "znajdź wszystkie",
            "zbadaj szczegółowo",
            "wygeneruj plan",
        ]
        if esc_mode == "planner" and any(
            pattern in message_lower for pattern in operational_patterns
        ):
            return True, "multi_step_operational"

        if veto_reason is not None:
            return False, veto_reason

        return False, "standard_chat_sufficient"

