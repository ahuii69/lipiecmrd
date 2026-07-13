"""TurnOps mixin: ExperienceMixin."""
from __future__ import annotations

import aihub.turn._ops_ns as _ops_ns

# Method bodies resolve names via this module's globals (incl. _names).
globals().update({k: v for k, v in vars(_ops_ns).items() if not k.startswith('__')})
TurnOps = None  # late-bound by aihub.turn.ops

class ExperienceMixin:
    def _lookup_experience_signal(
        self,
        *,
        user_id: str,
        message: str,
        selected_strategy: str,
    ) -> dict[str, Any]:
        """Load, rank and aggregate user experiences into execution-driving signal."""
        base_signal: dict[str, Any] = {
            "lookup_happened": False,
            "matches_count": 0,
            "experience_signal_summary": "no_lookup",
            "recommended_strategy": None,
            "confidence_adjustment": None,
            "handoff_bias": None,
            "blocker_reason": None,
            "blocker_severity": None,
            "recurring_failure_detected": False,
            "caution_hints": [],
            "dominant_strategy_success": None,
            "dominant_strategy_failure": None,
            "recurring_failure_types": [],
            "action_bias": {},
        }

        try:
            recent = get_experiences_by_user(user_id, limit=120)
            base_signal["lookup_happened"] = True
        except Exception:  # noqa: BLE001
            logger.debug("Experience lookup failed for user=%s", user_id, exc_info=True)
            base_signal["experience_signal_summary"] = "lookup_failed"
            return base_signal

        if not recent:
            base_signal["experience_signal_summary"] = "no_history"
            return base_signal

        query_tokens = self._tokenize_for_similarity(message or "")
        ranked: list[tuple[float, dict[str, Any]]] = []
        now = time.time()

        for exp in recent:
            summary = str(exp.get("user_input_summary") or "")
            lesson = str(exp.get("short_lesson_learned") or "")
            seed = str(exp.get("reflection_seed") or "")
            failure_type = str(exp.get("failure_type") or "")
            reason_blob = " ".join(str(rc) for rc in (exp.get("reason_codes") or []))
            text_blob = (
                f"{summary} {lesson} {seed} {failure_type} {reason_blob}".strip()
            )

            candidate_tokens = self._tokenize_for_similarity(text_blob)
            overlap = 0.0
            if query_tokens and candidate_tokens:
                overlap = len(query_tokens & candidate_tokens) / max(
                    1, len(query_tokens)
                )

            created_at = float(exp.get("created_at") or 0.0)
            age_days = max(0.0, (now - created_at) / 86400.0) if created_at else 999.0
            recency = max(0.0, 1.0 - min(age_days, 45.0) / 45.0)

            strat_bonus = (
                0.12
                if str(exp.get("selected_strategy") or "") == selected_strategy
                else 0.0
            )
            quality_bonus = 0.06 if bool(exp.get("success", False)) else -0.03

            score = overlap * 0.62 + recency * 0.20 + strat_bonus + quality_bonus
            if score >= 0.18 and (overlap >= 0.10 or strat_bonus > 0):
                ranked.append((score, exp))

        ranked.sort(key=lambda item: item[0], reverse=True)
        ranked = ranked[:16]

        if not ranked:
            base_signal["experience_signal_summary"] = "no_similar_matches"
            return base_signal

        base_signal["matches_count"] = len(ranked)

        by_strategy: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "weight": 0.0,
                "success": 0.0,
                "failure": 0.0,
                "fallback": 0.0,
                "degraded": 0.0,
                "unmet_tools": 0.0,
                "unmet_research": 0.0,
            }
        )
        failure_counter: Counter[str] = Counter()

        total_weight = 0.0
        weighted_success = 0.0
        weighted_failure = 0.0
        handoff_weight = 0.0
        handoff_score = 0.0
        unmet_tool_need_weight = 0.0

        for score, exp in ranked:
            w = max(0.05, float(score))
            total_weight += w

            strategy = str(exp.get("selected_strategy") or "instant")
            strat_stats = by_strategy[strategy]
            strat_stats["weight"] += w

            success = bool(exp.get("success", False))
            if success:
                weighted_success += w
                strat_stats["success"] += w
            else:
                weighted_failure += w
                strat_stats["failure"] += w

            fallback_flag = bool(exp.get("fallback_flag", False))
            degraded_flag = bool(exp.get("degraded_flag", False))
            tools_needed = bool(exp.get("tools_needed", False))
            tools_executed = bool(exp.get("tools_executed", False))
            research_needed = bool(exp.get("research_needed", False))
            research_executed = bool(exp.get("research_executed", False))
            planner_executed = bool(exp.get("planner_executed", False))
            agentic_executed = bool(exp.get("agentic_executed", False))

            if fallback_flag:
                strat_stats["fallback"] += w
            if degraded_flag:
                strat_stats["degraded"] += w
            if tools_needed and not tools_executed:
                strat_stats["unmet_tools"] += w
                unmet_tool_need_weight += w
            if research_needed and not research_executed:
                strat_stats["unmet_research"] += w

            failure_type = str(exp.get("failure_type") or "").strip().lower()
            if not success and failure_type:
                failure_counter[failure_type] += 1

            if planner_executed or agentic_executed:
                handoff_weight += w
                handoff_score += w if success else -w

        if total_weight <= 0:
            base_signal["experience_signal_summary"] = "matches_without_weight"
            return base_signal

        success_rate = weighted_success / total_weight
        failure_rate = weighted_failure / total_weight

        confidence_adjust = (success_rate - failure_rate) * 0.18
        confidence_adjust -= (unmet_tool_need_weight / total_weight) * 0.08
        confidence_adjust = max(-0.24, min(0.18, confidence_adjust))

        handoff_bias: float | None = None
        if handoff_weight > 0:
            handoff_bias = max(
                -0.55, min(0.55, (handoff_score / handoff_weight) * 0.40)
            )
        elif unmet_tool_need_weight > 0:
            handoff_bias = max(
                0.0, min(0.35, (unmet_tool_need_weight / total_weight) * 0.35)
            )

        dominant_success: tuple[str, float] | None = None
        dominant_failure: tuple[str, float] | None = None
        for strategy, stats in by_strategy.items():
            sw = max(1e-6, stats["weight"])
            succ_rate = stats["success"] / sw
            fail_rate = stats["failure"] / sw
            if stats["weight"] >= 0.6 and succ_rate >= 0.62:
                if dominant_success is None or succ_rate > dominant_success[1]:
                    dominant_success = (strategy, succ_rate)
            if stats["weight"] >= 0.6 and fail_rate >= 0.58:
                if dominant_failure is None or fail_rate > dominant_failure[1]:
                    dominant_failure = (strategy, fail_rate)

        recommended_strategy: str | None = None
        selected_stats = by_strategy.get(selected_strategy)
        selected_fail_rate = 0.0
        if selected_stats and selected_stats["weight"] > 0:
            selected_fail_rate = selected_stats["failure"] / selected_stats["weight"]

        if (
            dominant_success
            and dominant_success[0] != selected_strategy
            and selected_fail_rate >= 0.45
        ):
            recommended_strategy = dominant_success[0]

        recurring_failure_types = [k for k, v in failure_counter.items() if v >= 2]
        blocker_reason: str | None = None
        blocker_severity: float | None = None
        caution_hints: list[str] = []
        if recurring_failure_types:
            top_failure = recurring_failure_types[0]
            blocker_reason = (
                f"Powtarzalne porażki typu '{top_failure}' w podobnych turach"
            )
            blocker_severity = 0.72
            caution_hints.append(f"recurring_failure:{top_failure}")
        elif (
            selected_fail_rate >= 0.65
            and selected_stats
            and selected_stats["weight"] >= 1.0
        ):
            blocker_reason = (
                f"Wysoki wskaźnik porażek dla strategii {selected_strategy} "
                f"({selected_fail_rate:.0%})"
            )
            blocker_severity = 0.62
            caution_hints.append("high_strategy_failure_rate")

        if skip_experience_blocker_escalation(message):
            recurring_failure_types = []
            blocker_reason = None
            blocker_severity = None
            caution_hints = []

        strategy_to_action = {
            "instant": "reason",
            "contextual": "memory_search",
            "research": "research",
            "agentic": "action",
        }
        action_bias: dict[str, dict[str, float]] = {}
        for strategy, stats in by_strategy.items():
            sw = max(1e-6, stats["weight"])
            succ_rate = stats["success"] / sw
            fail_rate = stats["failure"] / sw
            delta = max(-0.18, min(0.18, (succ_rate - fail_rate) * 0.22))
            action = strategy_to_action.get(strategy)
            if not action:
                continue
            action_bias[action] = {
                "confidence_delta": round(delta, 3),
                "risk_delta": round(-delta * 0.65, 3),
                "utility_delta": round(delta * 0.40, 3),
            }

        summary = (
            f"matches={len(ranked)} succ={success_rate:.2f} fail={failure_rate:.2f} "
            f"conf_adj={confidence_adjust:+.2f}"
        )

        # ── Deliberation history extraction (execution-driving read-path) ──
        # Extract deliberation outcomes from past experiences to bias future
        # deliberation trigger decisions and variant evaluation.
        deliberation_history = self._extract_deliberation_history(ranked)

        base_signal.update(
            {
                "experience_signal_summary": summary,
                "recommended_strategy": recommended_strategy,
                "confidence_adjustment": round(confidence_adjust, 3),
                "handoff_bias": (
                    round(handoff_bias, 3) if handoff_bias is not None else None
                ),
                "blocker_reason": blocker_reason,
                "blocker_severity": blocker_severity,
                "recurring_failure_detected": bool(recurring_failure_types),
                "caution_hints": caution_hints,
                "dominant_strategy_success": (
                    dominant_success[0] if dominant_success else None
                ),
                "dominant_strategy_failure": (
                    dominant_failure[0] if dominant_failure else None
                ),
                "recurring_failure_types": recurring_failure_types,
                "action_bias": action_bias,
                # Deliberation history — feeds trigger logic and evaluation
                "deliberation_history": deliberation_history,
            }
        )
        return base_signal

    @staticmethod
    def _extract_deliberation_history(
        ranked: list[tuple[float, dict[str, Any]]],
    ) -> dict[str, Any]:
        """Extract deliberation outcome patterns from past experiences.

        Produces execution-driving signal consumed by:
          - should_run_variants(): deliberation_trigger_bias
          - evaluate_candidates(): variant_type preference weights
          - synthesis: quality expectation baseline

        Returns:
          deliberation_count           – how many past matches had deliberation
          deliberation_success_rate    – weighted success rate of deliberated turns
          avg_quality_score            – average deliberation_outcome_quality.quality_score
          avg_confidence_gain          – average confidence_gain from deliberation
          dominant_winner_type         – most common winner variant_type
          winner_type_distribution     – {direct: N, contextual: N, actionable: N}
          deliberation_trigger_bias    – float: + favour trigger, - suppress trigger
          variant_preference_weights   – {type: weight} bias for evaluation (0.8–1.2)
          should_suppress_deliberation – bool: true if deliberation was consistently unhelpful
          should_force_deliberation    – bool: true if deliberation was consistently helpful
        """
        result: dict[str, Any] = {
            "deliberation_count": 0,
            "deliberation_success_rate": 0.0,
            "avg_quality_score": 0.0,
            "avg_confidence_gain": 0.0,
            "dominant_winner_type": None,
            "winner_type_distribution": {},
            "deliberation_trigger_bias": 0.0,
            "variant_preference_weights": {},
            "should_suppress_deliberation": False,
            "should_force_deliberation": False,
        }

        deliberated_exps: list[tuple[float, dict[str, Any]]] = []
        for score, exp in ranked:
            if exp.get("response_variants_triggered"):
                deliberated_exps.append((score, exp))

        if not deliberated_exps:
            return result

        result["deliberation_count"] = len(deliberated_exps)

        total_weight = 0.0
        weighted_success = 0.0
        quality_scores: list[float] = []
        confidence_gains: list[float] = []
        winner_types: Counter[str] = Counter()
        should_have_count = 0
        should_not_have_count = 0

        for score, exp in deliberated_exps:
            w = max(0.05, float(score))
            total_weight += w

            success = bool(exp.get("success", False))
            if success:
                weighted_success += w

            # Extract outcome quality if stored
            oq = exp.get("deliberation_outcome_quality")
            if isinstance(oq, dict):
                qs = float(oq.get("quality_score") or 0.0)
                quality_scores.append(qs)
                cg = float(oq.get("confidence_gain") or 0.0)
                confidence_gains.append(cg)
                if oq.get("should_have_deliberated"):
                    should_have_count += 1
                else:
                    should_not_have_count += 1
            else:
                # Fallback: use raw confidence as quality proxy
                conf = float(exp.get("response_variants_confidence") or 0.5)
                quality_scores.append(conf)

            winner = exp.get("response_variants_winner_type")
            if winner:
                winner_types[winner] += 1

        if total_weight > 0:
            result["deliberation_success_rate"] = round(
                weighted_success / total_weight, 3
            )

        if quality_scores:
            result["avg_quality_score"] = round(
                sum(quality_scores) / len(quality_scores), 4
            )
        if confidence_gains:
            result["avg_confidence_gain"] = round(
                sum(confidence_gains) / len(confidence_gains), 4
            )

        if winner_types:
            result["dominant_winner_type"] = winner_types.most_common(1)[0][0]
            result["winner_type_distribution"] = dict(winner_types)

        # ── Trigger bias: should we trigger more or less often? ──
        # Positive → favour triggering, Negative → suppress triggering
        trigger_bias = 0.0
        if should_have_count + should_not_have_count >= 2:
            ratio = should_have_count / (should_have_count + should_not_have_count)
            trigger_bias = round((ratio - 0.5) * 0.40, 4)  # [-0.20, +0.20]
        elif quality_scores:
            avg_q = sum(quality_scores) / len(quality_scores)
            trigger_bias = round((avg_q - 0.50) * 0.30, 4)  # [-0.15, +0.15]
        result["deliberation_trigger_bias"] = max(-0.20, min(0.20, trigger_bias))

        # ── Variant preference weights (bias evaluation scores) ──
        # Winner types that historically performed well get a slight boost
        variant_weights: dict[str, float] = {}
        total_wins = sum(winner_types.values()) or 1
        for vtype in ("direct", "contextual", "actionable"):
            win_count = winner_types.get(vtype, 0)
            # Base weight 1.0, adjust ±0.2 based on historical win rate
            win_rate = win_count / total_wins
            variant_weights[vtype] = round(
                max(0.80, min(1.20, 1.0 + (win_rate - 0.33) * 0.60)),
                3,
            )
        result["variant_preference_weights"] = variant_weights

        # ── Suppress/force deliberation ──
        if len(deliberated_exps) >= 3:
            if (
                result["deliberation_success_rate"] < 0.30
                and result["avg_quality_score"] < 0.40
            ):
                result["should_suppress_deliberation"] = True
            elif (
                result["deliberation_success_rate"] >= 0.75
                and result["avg_quality_score"] >= 0.60
            ):
                result["should_force_deliberation"] = True

        return result

    def _write_back_experience(
        self,
        *,
        turn: ChatTurnInput,
        response_text: str,
        grounding_mode: str,
        tool_calls: list[ToolCallRequest],
        tool_results: list[ToolCallResult],
        trace: dict[str, Any],
        errors: list[dict[str, Any]],
        psyche_snapshot: dict[str, Any],
        decision_core: dict[str, Any] | None = None,
    ) -> None:
        intent = self._infer_intent(turn.message, tool_calls)
        _dc = decision_core or {}
        metadata = {
            "session_id": turn.session_id,
            "memory_scope": "user",
            "mode": turn.mode,
            "grounding_mode": grounding_mode,
            "tool_names": [call.name for call in tool_calls],
            "tool_successes": len([r for r in tool_results if r.ok]),
            "tool_failures": len([r for r in tool_results if not r.ok]),
            "selected_strategy": _dc.get("selected_strategy"),
            "execution_mode": _dc.get("execution_mode"),
            "escalation_path": _dc.get("escalation_path"),
            "reason_codes": _dc.get("reason_codes", []),
            "simulation_best_action": _dc.get("simulation_best_action"),
            "simulation_risk_summary": _dc.get("simulation_risk_summary"),
            "consistency_classification": _dc.get("consistency_classification"),
            "policy_profile_name": _dc.get("policy_profile_name"),
            # Deliberation write-back (enriches experience for future retrieval)
            "response_variants_triggered": trace.get(
                "response_variants_triggered", False
            ),
            "response_variants_winner_type": trace.get("response_variants_winner_type"),
            "response_variants_confidence": trace.get("response_variants_confidence"),
            "response_variants_risk": trace.get("response_variants_risk"),
            "response_variants_count": trace.get("response_variants_count", 0),
            "response_variants_reason_codes": trace.get(
                "response_variants_reason_codes", []
            ),
            "response_variants_synthesis_used": trace.get(
                "response_variants_synthesis_used", []
            ),
            "response_variants_dropped": trace.get("response_variants_dropped", []),
            "response_variants_duration_ms": trace.get("response_variants_duration_ms"),
            "response_variants_aggregate_pros": trace.get(
                "response_variants_aggregate_pros", []
            ),
            "response_variants_aggregate_cons": trace.get(
                "response_variants_aggregate_cons", []
            ),
            # Structured per-variant scores for future outcome quality modeling
            "response_variants_scores": trace.get("response_variants_scores", []),
            # Outcome quality model: computed from winner scores + synthesis data
            "deliberation_outcome_quality": self._compute_deliberation_outcome_quality(
                trace
            ),
        }

        from aihub.experience_memory import (
            build_strategy_experience_record,
            latency_bucket_from_ms,
            merge_strategy_experience_into_metadata,
        )

        lat_ms = float(trace.get("duration_ms") or 0.0)
        _handoff = bool(trace.get("agent_handoff_triggered"))
        _agent_steps = int(trace.get("agent_steps_executed") or 0)
        _used_tools = len(tool_calls) > 0 or (
            _handoff
            and (
                bool(trace.get("planner_executed"))
                or bool(trace.get("reasoning_executed"))
                or _agent_steps > 0
            )
        )
        strat_exp = build_strategy_experience_record(
            user_input_summary=turn.message or "",
            selected_strategy=str(_dc.get("selected_strategy") or ""),
            final_mode=str(
                _dc.get("execution_mode") or _dc.get("escalation_final_mode") or ""
            ),
            success=len(errors) == 0
            and grounding_mode not in ("fallback", "web_required_ungrounded"),
            latency_bucket=latency_bucket_from_ms(lat_ms),
            used_tools=_used_tools,
            fallback_used=grounding_mode == "fallback",
            reflection_hint=str(trace.get("reflection_summary") or "")[:400],
        )
        metadata = merge_strategy_experience_into_metadata(metadata, strat_exp)
        _bv = trace.get("blocker_verdict")
        _blocker_t = None
        if isinstance(_bv, dict):
            _blocker_t = _bv.get("blocker_type")
        metadata["experience_turn_feedback"] = {
            "intent": intent,
            "selected_strategy": _dc.get("selected_strategy"),
            "escalation_final_mode": _dc.get("escalation_final_mode"),
            "web_need": str(_dc.get("web_decision") or "off"),
            "deterministic_hit": bool(trace.get("deterministic_hit", False)),
            "used_sources_count": int(trace.get("controlled_web_source_count") or 0),
            "latency_ms": round(lat_ms, 2),
            "fallback_used": grounding_mode == "fallback",
            "web_grounding_failed": grounding_mode == "web_required_ungrounded",
            "web_explicit_fail_applied": bool(trace.get("web_explicit_fail_applied")),
            "web_prefetch_executed": bool(trace.get("web_prefetch_executed")),
            "primary_error_type": next(
                (str(e.get("type") or "") for e in errors if e.get("type")),
                None,
            ),
            "blocker_type": _blocker_t,
            "blocker_verdict": (
                trace.get("blocker_verdict")
                if isinstance(trace.get("blocker_verdict"), dict)
                else None
            ),
            "grounding_mode": grounding_mode,
            "web_grounding_outcome": trace.get("web_grounding_outcome"),
            "web_final_grounding_outcome": trace.get("web_final_grounding_outcome"),
            "web_subsystem_operation": trace.get("web_subsystem_operation"),
            "planner_executed": bool(trace.get("planner_executed")),
            "reasoning_executed": bool(trace.get("reasoning_executed")),
        }

        trace.setdefault("experience_write_back_attempted", False)
        trace.setdefault("experience_write_back_succeeded", False)
        trace.setdefault("experience_episode_id", None)
        trace.setdefault("experience_fact_ids", [])
        trace.setdefault("experience_stm_ids", [])
        trace.setdefault("psyche_state_before", {})
        trace.setdefault("psyche_state_after", {})

        turn_id = str(
            getattr(getattr(self, "_active_turn_ctx", None), "turn_id", None)
            or getattr(turn, "turn_id", None)
            or uuid.uuid4()
        )
        reflection_payload = {
            "action_type": "chat_turn",
            "parameters": {
                "grounding_mode": grounding_mode,
                "tool_calls": len(tool_calls),
            },
            "confidence": 1.0 if len(errors) == 0 else 0.5,
            "execution_result": {"ok": len(errors) == 0, "response_len": len(response_text or "")},
            "decision_reasoning": "TurnCompleted",
            "context": {"turn_id": turn_id, "intent": intent},
        }

        try:
            from aihub.durable_jobs import execute_turn_completed_inline

            completed = execute_turn_completed_inline(
                turn_id=turn_id,
                user_id=turn.user_id,
                user_message=turn.message,
                assistant_message=response_text or "",
                intent=intent,
                metadata=metadata,
                reflection=reflection_payload,
            )
            write_result = completed.get("handlers", {}).get("memory") or {}
            psyche_result = completed.get("handlers", {}).get("psyche") or {}
            trace["experience_write_back_attempted"] = True
            trace["experience_write_back_succeeded"] = True
            trace["experience_episode_id"] = write_result.get("episode_id")
            trace["experience_fact_ids"] = write_result.get("fact_ids", [])
            trace["experience_stm_ids"] = write_result.get("stm_ids", [])
            trace["turn_completed_job_id"] = completed.get("job_id")
            trace["reflection_outcome_id"] = (
                (completed.get("handlers", {}).get("reflection") or {}).get("reflection_id")
            )

            _consistency_class = _dc.get("consistency_classification")
            _fact_ids = write_result.get("fact_ids", [])
            if _consistency_class in ("conflict", "revision") and _fact_ids:
                try:
                    from aihub.consistency_engine import (
                        apply_consistency_verdict,
                        check_consistency,
                    )

                    _cv = check_consistency(turn.user_id, turn.message or "")
                    if _cv and _cv.matched_node_id:
                        apply_consistency_verdict(turn.user_id, _fact_ids[0], _cv)
                except Exception:
                    logger.debug(
                        "Consistency apply_verdict on new facts failed", exc_info=True
                    )

            trace["psyche_snapshot_happened"] = True
            trace["psyche_state_before"] = self._compact_psyche_state(psyche_snapshot)
            trace["psyche_state_after"] = self._compact_psyche_state(psyche_result)
        except Exception as exc:  # noqa: BLE001
            trace["experience_write_back_attempted"] = True
            trace["experience_write_back_succeeded"] = False
            if psyche_snapshot:
                trace["psyche_state_before"] = self._compact_psyche_state(
                    psyche_snapshot
                )
            errors.append(
                {
                    "type": "memory_write_back_error",
                    "error": str(exc),
                }
            )
            errors.append(
                {
                    "type": "psyche_update_error",
                    "error": str(exc),
                }
            )

    def _run_runtime_experience_feedback(
        self, user_id: str, trace: dict[str, Any]
    ) -> None:
        """Recompute strategy confidence bias from experience_memory and persist per user."""
        from aihub.db import (
            default_strategy_decision_bias,
            get_strategy_decision_bias,
            save_strategy_decision_bias,
            user_has_persisted_strategy_bias,
        )
        from aihub.experience_analyzer import ExperienceAnalyzer
        from aihub.strategy_selector import compute_strategy_bias_from_metrics

        uid = (user_id or "").strip()
        before = (
            get_strategy_decision_bias(uid) if uid else default_strategy_decision_bias()
        )
        loaded_from = (
            "persisted" if uid and user_has_persisted_strategy_bias(uid) else "default"
        )
        trace["strategy_bias_before"] = dict(before)
        trace["strategy_bias_loaded_from"] = loaded_from

        active_mode = str(
            getattr(getattr(self, "_active_turn_ctx", None), "environment", None)
            and getattr(self._active_turn_ctx.environment, "mode", "")
            or ""
        ).lower()
        if not uid or active_mode == "audit" or active_mode.endswith("audit"):
            trace["strategy_bias_after"] = dict(before)
            trace["feedback_applied"] = False
            trace["strategy_bias_computed_from"] = "none"
            trace["strategy_bias_persisted_to"] = "skipped"
            trace["strategy_bias_source"] = "skipped"
            trace["strategy_bias_flow"] = [loaded_from, "none", "skipped"]
            trace.setdefault("agentic_executed", False)
            trace["experience_write_back"] = bool(
                trace.get("experience_write_back_succeeded")
            )
            trace["bias_updated"] = False
            return

        try:
            metrics = ExperienceAnalyzer().analyze_recent_experiences(uid, limit=100)
            trace["experience_feedback_metrics"] = metrics
            computed = compute_strategy_bias_from_metrics(metrics)
            save_strategy_decision_bias(uid, computed, metrics_snapshot=metrics)
            after = dict(computed)
            trace["strategy_bias_after"] = after
            trace["feedback_applied"] = after != before
            trace["strategy_bias_computed_from"] = "memory"
            trace["strategy_bias_persisted_to"] = "persisted"
            trace["strategy_bias_source"] = "persisted"
            trace["strategy_bias_flow"] = [loaded_from, "memory", "persisted"]
        except Exception:
            logger.exception("runtime experience feedback failed user=%s", uid)
            trace["strategy_bias_after"] = dict(before)
            trace["feedback_applied"] = False
            trace["strategy_bias_computed_from"] = "memory"
            trace["strategy_bias_persisted_to"] = "failed"
            trace["strategy_bias_source"] = "failed"
            trace["strategy_bias_flow"] = [loaded_from, "memory", "failed"]

        trace.setdefault("agentic_executed", False)
        trace["experience_write_back"] = bool(
            trace.get("experience_write_back_succeeded")
        )
        trace["bias_updated"] = bool(trace.get("feedback_applied"))

    @staticmethod
    def _compute_deliberation_outcome_quality(trace: dict[str, Any]) -> dict[str, Any]:
        """Compute outcome quality model for deliberation.

        Returns a structured dict that can be stored in experience and read back
        to bias future deliberation triggers and variant evaluation.

        Fields:
          quality_score: 0.0–1.0 — overall deliberation quality
          confidence_gain: float — how much confidence improved vs pre-deliberation
          synthesis_efficiency: float — ratio of used vs total candidates
          risk_level: str — "low" | "medium" | "high"
          variant_diversity: float — how different were the candidates
          should_have_deliberated: bool — was deliberation likely beneficial
        """
        triggered = trace.get("response_variants_triggered", False)
        if not triggered:
            return {
                "quality_score": 0.0,
                "confidence_gain": 0.0,
                "synthesis_efficiency": 0.0,
                "risk_level": "none",
                "variant_diversity": 0.0,
                "should_have_deliberated": False,
            }

        confidence = float(trace.get("response_variants_confidence") or 0.0)
        risk = float(trace.get("response_variants_risk") or 0.0)
        count = int(trace.get("response_variants_count") or 0)
        used = trace.get("response_variants_synthesis_used") or []
        dropped = trace.get("response_variants_dropped") or []
        scores = trace.get("response_variants_scores") or []

        # Pre-deliberation confidence from strategy_confidence
        pre_confidence = float(trace.get("strategy_confidence") or 0.5)
        confidence_gain = round(confidence - pre_confidence, 4)

        # Synthesis efficiency: how many candidates were actually useful
        total_candidates = max(1, count)
        synthesis_efficiency = round(len(used) / total_candidates, 3)

        # Variant diversity: std-dev of aggregate scores across candidates
        agg_scores = [float(s.get("aggregate_score", 0.0)) for s in scores]
        variant_diversity = 0.0
        if len(agg_scores) >= 2:
            mean_score = sum(agg_scores) / len(agg_scores)
            variance = sum((s - mean_score) ** 2 for s in agg_scores) / len(agg_scores)
            variant_diversity = round(variance**0.5, 4)

        # Risk level classification
        risk_level = "low" if risk < 0.3 else ("high" if risk >= 0.65 else "medium")

        # Quality score: weighted composite of confidence + efficiency - risk
        quality_score = round(
            max(
                0.0,
                min(
                    1.0,
                    confidence * 0.45
                    + synthesis_efficiency * 0.25
                    + (1.0 - risk) * 0.20
                    + variant_diversity * 0.10,
                ),
            ),
            4,
        )

        # Was deliberation worth it? Yes if quality_score >= 0.50 and confidence_gain > 0
        should_have_deliberated = quality_score >= 0.50 and confidence_gain > -0.05

        return {
            "quality_score": quality_score,
            "confidence_gain": confidence_gain,
            "synthesis_efficiency": synthesis_efficiency,
            "risk_level": risk_level,
            "variant_diversity": variant_diversity,
            "should_have_deliberated": should_have_deliberated,
        }

    def _post_exec_reflection(
        self,
        *,
        user_id: str,
        message: str,
        response_text: str,
        tool_calls: list[ToolCallRequest],
        tool_results: list[ToolCallResult],
        decision_core: dict[str, Any],
        blocker_verdict: "BlockerVerdict | None" = None,
        handoff_happened: bool = False,
    ) -> dict[str, Any]:
        """Reflect on the completed turn. Produces lesson + policy signal for experience memory.

        Returns a dict with reflection data including operational hindsight
        fields that feed the next turn's PolicyEngine.compute_feedback().
        """
        result: dict[str, Any] = {
            "reflection_ran": False,
            "reflection_summary": None,
            # Hindsight fields — neutral defaults (overwritten on success)
            "strategy_fit": "neutral",
            "handoff_hindsight": "na",
            "blocker_hindsight": "na",
            "confidence_hindsight": 0.0,
            "risk_hindsight": 0.0,
            "deliberation_hindsight": {},
        }
        try:
            from aihub.reflection_engine import ReflectionInput, reflect_on_action

            successes = sum(1 for r in tool_results if r.ok)
            failures = sum(1 for r in tool_results if not r.ok)
            tool_names = [tc.name for tc in tool_calls]
            action_type = "chat_turn_with_tools" if tool_calls else "chat_turn"
            confidence = decision_core.get("strategy_confidence") or (
                1.0 if failures == 0 else max(0.3, 1.0 - failures * 0.2)
            )

            # ── Build context with full decision_core data for hindsight ──
            # _compute_hindsight uses these to compare predicted vs actual.
            _sim_risk_raw = decision_core.get("simulation_risk_summary") or ""
            _sim_risk = 0.0
            if decision_core.get("simulation_ran") and "risk=" in _sim_risk_raw:
                try:
                    _sim_risk = float(_sim_risk_raw.split("risk=")[1].split()[0])
                except (ValueError, IndexError) as exc:
                    logger.debug("Simulation risk parsing failed in hindsight context: %s", exc)
            # Apply simulation risk calibration from feedback if present
            _sim_risk += float(decision_core.get("policy_simulation_risk_cal") or 0.0)

            _blocker_active = False
            _blocker_hard = False
            if blocker_verdict is not None:
                _blocker_active = blocker_verdict.blocker_active
                _blocker_hard = blocker_verdict.hard

            ref_input = ReflectionInput(
                user_id=user_id,
                action_type=action_type,
                parameters={
                    "message_excerpt": (message or "")[:200],
                    "tools": tool_names,
                    "strategy": decision_core.get("selected_strategy"),
                },
                confidence=confidence,
                execution_result={
                    "response_length": len(response_text or ""),
                    "tool_calls": len(tool_calls),
                    "successes": successes,
                    "failures": failures,
                    "tools_used": tool_names,
                },
                decision_reasoning=(
                    f"strategy={decision_core.get('selected_strategy')} "
                    f"codes={decision_core.get('reason_codes', [])} "
                    f"sim={decision_core.get('simulation_best_action')}"
                ),
                context={
                    "source": "chat_runtime_decision_core",
                    "consistency": decision_core.get("consistency_classification"),
                    # ── Fields consumed by _compute_hindsight ──
                    "selected_strategy": decision_core.get("selected_strategy"),
                    "strategy_confidence": float(
                        decision_core.get("strategy_confidence") or confidence
                    ),
                    "handoff_happened": handoff_happened,
                    "blocker_was_active": _blocker_active,
                    "blocker_was_hard": _blocker_hard,
                    "simulation_risk": _sim_risk,
                    # ── Deliberation fields for _compute_deliberation_hindsight ──
                    "response_variants_triggered": decision_core.get(
                        "response_variants_triggered", False
                    ),
                    "response_variants_confidence": decision_core.get(
                        "response_variants_confidence"
                    ),
                    "response_variants_risk": decision_core.get(
                        "response_variants_risk"
                    ),
                    "response_variants_synthesis_used": decision_core.get(
                        "response_variants_synthesis_used", []
                    ),
                    "deliberation_outcome_quality": decision_core.get(
                        "deliberation_outcome_quality", {}
                    ),
                },
            )
            reflection_output = reflect_on_action(ref_input)
            result["reflection_ran"] = True
            result["reflection_summary"] = reflection_output.lesson_learned
            # ── Propagate hindsight to trace ──
            result["strategy_fit"] = reflection_output.strategy_fit
            result["handoff_hindsight"] = reflection_output.handoff_hindsight
            result["blocker_hindsight"] = reflection_output.blocker_hindsight
            result["confidence_hindsight"] = reflection_output.confidence_hindsight
            result["risk_hindsight"] = reflection_output.risk_hindsight
            result["deliberation_hindsight"] = reflection_output.deliberation_hindsight
        except Exception:
            logger.debug("Post-exec reflection failed", exc_info=True)
        return result

    def _run_etap9_cognitive(
        self,
        *,
        user_id: str,
        message: str,
        tool_calls: list[ToolCallRequest],
        tool_results: list[ToolCallResult],
    ) -> dict[str, Any]:
        """Run ETAP 9B/9C cognitive engines on the completed turn.

        Returns dict with trace fields:
          consistency_check_ran, consistency_classification,
          reflection_ran, policy_hints_loaded,
          simulation_ran, simulation_best_action
        """
        result: dict[str, Any] = {
            "consistency_check_ran": False,
            "consistency_classification": None,
            "reflection_ran": False,
            "policy_hints_loaded": False,
            "simulation_ran": False,
            "simulation_best_action": None,
        }

        # ── 1. Consistency check on user message ──
        try:
            from aihub.consistency_engine import check_consistency

            verdict = check_consistency(user_id, message)
            result["consistency_check_ran"] = True
            if verdict:
                result["consistency_classification"] = verdict.classification
            else:
                result["consistency_classification"] = "no_prior_facts"
        except Exception:
            logger.debug("ETAP9 consistency check failed", exc_info=True)

        # ── 2. Policy hints load ──
        try:
            from aihub.policy_engine import build_policy_profile

            profile = build_policy_profile(user_id)
            result["policy_hints_loaded"] = True
            if profile.hints:
                logger.debug(
                    "ETAP9 loaded %d policy hints for %s",
                    len(profile.hints),
                    user_id,
                )
        except Exception:
            logger.debug("ETAP9 policy hints load failed", exc_info=True)

        # ── 3. Simulation ──
        try:
            from aihub.simulation_engine import simulate_action

            # Determine dominant action type from tool calls
            action_type = "query"
            if tool_calls:
                names = [tc.name for tc in tool_calls]
                if any("memory" in n or "add_fact" in n for n in names):
                    action_type = "memory_add"
                elif any("search" in n or "context" in n for n in names):
                    action_type = "memory_search"
                elif any("web" in n or "fetch" in n for n in names):
                    action_type = "web_fetch"
                elif any("reflect" in n or "psyche" in n for n in names):
                    action_type = "reflect"
                elif any("plan" in n or "task" in n for n in names):
                    action_type = "plan"

            sim_result = simulate_action(
                user_id,
                action_type,
                {"message": message[:200]},
                {"tool_count": len(tool_calls), "source": "chat_runtime"},
                max_variants=3,
            )
            result["simulation_ran"] = True
            if sim_result.best_variant:
                result["simulation_best_action"] = sim_result.best_variant.action_type
        except Exception:
            logger.debug("ETAP9 simulation failed", exc_info=True)

        # ── 4. Reflection on turn outcome ──
        try:
            from aihub.reflection_engine import ReflectionInput, reflect_on_action

            successes = sum(1 for r in tool_results if r.ok)
            failures = sum(1 for r in tool_results if not r.ok)
            tool_names = [tc.name for tc in tool_calls]

            exec_result = {
                "tool_calls": len(tool_calls),
                "successes": successes,
                "failures": failures,
                "tools_used": tool_names,
            }

            action_type = "chat_turn"
            if tool_calls:
                action_type = "chat_turn_with_tools"

            ref_input = ReflectionInput(
                user_id=user_id,
                action_type=action_type,
                parameters={"message_excerpt": message[:200], "tools": tool_names},
                confidence=1.0 if failures == 0 else max(0.3, 1.0 - failures * 0.2),
                execution_result=exec_result,
                decision_reasoning=f"chat turn: {len(tool_calls)} tools called",
                context={"source": "chat_runtime"},
            )
            reflect_on_action(ref_input)
            result["reflection_ran"] = True
        except Exception:
            logger.debug("ETAP9 reflection failed", exc_info=True)

        return result

    def _shape_response_text(
        self,
        *,
        turn: ChatTurnInput,
        ctx: ChatTurnContext,
        response_text: str,
        grounding_mode: str,
        used_fallback: bool,
        memory_v2_context=None,
        psyche_v2_context=None,
        anti_hallucination_trace: dict[str, Any] | None = None,
    ) -> str:
        text = (response_text or "").strip()
        is_cap_q = self._is_capability_question(turn.message)
        is_trace_q = self._is_trace_status_question(turn.message)

        # Apply Psyche V2 behavior shaping to final response
        if psyche_v2_context and psyche_v2_context.loaded and text:
            # Contradiction guard: add uncertainty markers when contradictions present
            if memory_v2_context and memory_v2_context.loaded:
                _ccons = getattr(psyche_v2_context, "consistency_decision", "allow")
                _guard_thr = 0.52 if _ccons != "suppress" else 0.62
                if (
                    memory_v2_context.contradiction_alerts
                    and psyche_v2_context.caution_bias > _guard_thr
                ):
                    if not any(
                        marker in text.lower()
                        for marker in [
                            "prawdopodobnie",
                            "może",
                            "wydaje się",
                            "uwaga",
                            "ostrożnie",
                        ]
                    ):
                        text = f"Uwaga, mam sprzeczne info w pamięci. {text}"

            # Pressure-driven structure: high pressure → more structured output
            if (
                psyche_v2_context.pressure > 0.6
                and psyche_v2_context.structuredness_bias > 0.6
            ):
                # If text is long and unstructured, don't rewrite but could add structure note
                if len(text) > 500 and "\n-" not in text and "\n1" not in text:
                    text = text  # preserve provider wording; structure is governed by prompt policy

            # High friction → precision marker
            if psyche_v2_context.friction > 0.6:
                # Friction means precision, avoid vague language
                text = text  # precision pressure is handled in prompt instructions

        if used_fallback:
            # Fallback path is injected by runtime itself and should be explicit.
            return text

        if grounding_mode in {"model_only", "unknown_not_verified"}:
            if is_cap_q:
                cap_names = [c.name for c in ctx.capabilities]
                cap_list = ", ".join(cap_names[:12]) if cap_names else "brak"
                text = (
                    f"Mam dostęp do capability: {cap_list}. "
                    "W tej konkretnej odpowiedzi nie uruchomiłem żadnego narzędzia — "
                    "to odpowiedź model-only. Jeśli chcesz, mogę teraz realnie odpalić odpowiednie narzędzia i sprawdzić temat."
                )
            elif self._has_unverified_tool_claim(text):
                rewritten = self._rewrite_unverified_claims(text)
                text = (
                    "Doprecyzuję bez ściemy: w tej turze nie uruchamiałem narzędzi runtime. "
                    "To odpowiedź oparta na samej rozmowie/modelu. " + rewritten
                )
            elif not text:
                text = (
                    "W tej turze nie mam zweryfikowanego wyniku z narzędzi. "
                    "Mogę to teraz sprawdzić runtime, jeśli chcesz."
                )

        if is_trace_q and grounding_mode != "fallback":
            suffix = "W tej turze odpowiedź poszła normalnym torem providera (bez fallbacku)."
            if grounding_mode == "tool_verified":
                suffix += " I tak — były realne wywołania narzędzi."
            elif grounding_mode == "unknown_not_verified":
                suffix += " Były próby narzędzi, ale bez potwierdzonego wyniku do weryfikacji."
            else:
                suffix += " Bez uruchamiania narzędzi (model-only)."

            if text:
                text = f"{text}\n\n{suffix}"
            else:
                text = suffix

        # Twardy override anty-halucynacyjny: liczby/cechy bez pokrycia w treści użytkownika.
        if not used_fallback and not is_cap_q and not is_trace_q:
            clamped, clamp_reason = clamp_ungrounded_speculative_reply(
                turn.message or "",
                text,
                history_user_messages=self._user_turn_texts_for_grounding(turn),
                skip_clamp=(grounding_mode == "tool_verified"),
            )
            if clamp_reason:
                text = clamped
                if anti_hallucination_trace is not None:
                    anti_hallucination_trace["applied"] = True
                    anti_hallucination_trace["reason"] = clamp_reason

        return text

