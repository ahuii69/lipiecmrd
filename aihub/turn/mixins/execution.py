"""TurnOps mixin: ExecutionMixin."""
from __future__ import annotations

import aihub.turn._ops_ns as _ops_ns

# Method bodies resolve names via this module's globals (incl. _names).
globals().update({k: v for k, v in vars(_ops_ns).items() if k != '__name__'})
TurnOps = None  # late-bound by aihub.turn.ops

class ExecutionMixin:
    async def _execute_agent_handoff(
        self,
        *,
        turn: ChatTurnInput,
        decision_core: dict[str, Any],
        handoff_reason: str,
        started: float,
        psyche_snapshot: dict[str, Any],
        memory_used_trace: list[dict[str, Any]] | None = None,
        memory_lookup_flag: bool = False,
        blocker_verdict: BlockerVerdict | None = None,
        memory_context: dict[str, Any] | None = None,
        ctx: ChatTurnContext | None = None,
    ) -> ChatTurnResult:
        """Execute controlled handoff to agent runtime and normalize to ChatTurnResult."""
        errors: list[dict[str, Any]] = []

        try:
            if stream_session_active():
                await emit_status("tools", label_pl="Analizuję…")
            controller = _cr_hook("get_executive_controller", get_executive_controller)()
            fstr, freason = map_chat_execution_mode_to_force_strategy(decision_core)
            cycle = await controller.run_cycle(
                {
                    "text": turn.message,
                    "max_steps": 8,
                    "timeout_seconds": 20.0,
                    "force_strategy": fstr,
                    "force_strategy_reason": f"{freason};chat_runtime:agent_handoff",
                },
                mode="run",
                user_id=turn.user_id,
            )
            agent_response = _cr_hook("build_agent_cycle_response", build_agent_cycle_response)(
                cycle, include_debug=turn.include_debug
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Agent handoff failed user=%s error=%s", turn.user_id, exc)
            errors.append(
                {
                    "type": "agent_handoff_error",
                    "error": str(exc),
                    "handoff_reason": handoff_reason,
                }
            )
            # Return degraded result on handoff error
            handoff_err_trace = {
                "provider_calls": 0,
                "tool_iterations": 0,
                "used_tools": False,
                "used_fallback": False,
                "response_grounding_mode": "agent_handoff_error",
                "duration_ms": (time.monotonic() - started) * 1000.0,
                "provider": "executive_controller",
                "model": "planner+reasoning",
                "agent_handoff_triggered": True,
                "agent_handoff_reason": handoff_reason,
                "agent_handoff_error": str(exc),
                "effective_runtime_path": "agent_handoff_error",
                **TurnOps._decision_core_trace_escalation(decision_core),
                "experience_lookup_happened": decision_core.get(
                    "experience_lookup_happened", False
                ),
                "experience_matches_count": decision_core.get(
                    "experience_matches_count", 0
                ),
                "experience_influenced_strategy": decision_core.get(
                    "experience_influenced_strategy", False
                ),
                "experience_confidence_adjustment": decision_core.get(
                    "experience_confidence_adjustment"
                ),
                "experience_handoff_bias": decision_core.get("experience_handoff_bias"),
                "experience_blocker_reason": decision_core.get(
                    "experience_blocker_reason"
                ),
                "experience_signal_summary": decision_core.get(
                    "experience_signal_summary"
                ),
                # ── Policy Feedback Loop trace fields ──
                "policy_feedback_loaded": bool(
                    decision_core.get("policy_feedback_loaded")
                ),
                "policy_feedback_applied": bool(
                    decision_core.get("policy_feedback_applied")
                ),
                "policy_feedback_summary": decision_core.get(
                    "policy_feedback_summary", ""
                ),
                "policy_confidence_delta": decision_core.get(
                    "policy_confidence_delta", 0.0
                ),
                "policy_handoff_bias": decision_core.get("policy_handoff_bias", 0.0),
                "policy_blocker_sensitivity": decision_core.get(
                    "policy_blocker_sensitivity", 0.0
                ),
                "policy_simulation_risk_cal": decision_core.get(
                    "policy_simulation_risk_cal", 0.0
                ),
                "policy_strategy_adjustments": decision_core.get(
                    "policy_strategy_adjustments", {}
                ),
                "selected_strategy": decision_core["selected_strategy"],
                "reason_codes": list(decision_core.get("reason_codes") or []),
                "strategy_confidence": decision_core.get("strategy_confidence"),
                "degraded": True,
                "memory_lookup_happened": memory_lookup_flag,
                "psyche_snapshot_happened": False,
                "research_was_required": str(decision_core.get("web_decision") or "off")
                == "required",
                "agentic_executed": True,
                "tool_calls_count": 0,
                "experience_write_back_attempted": False,
                "experience_write_back_succeeded": False,
                **self._correction_trace_fields(ctx),
            }
            if memory_used_trace:
                handoff_err_trace["memory_used"] = memory_used_trace
            self._augment_memory_observability(
                handoff_err_trace, memory_used_trace, memory_context
            )
            handoff_err_trace["chat_handoff_evaluated"] = True
            handoff_err_trace["chat_handoff_executed"] = False
            handoff_err_trace["chat_handoff_skip_reason"] = "agent_handoff_error"
            trace_blocker_gate_outcome(
                handoff_err_trace, gate_evaluated=True, hard_applied=False
            )
            merge_canonical_decision_trace(
                handoff_err_trace,
                selected_route=ROUTE_AGENT_HANDOFF_ERROR,
                route_reason="agent_handoff_infrastructure_error",
                decision_intent="plan",
                deterministic_hit=False,
                vault_used=False,
                memory_retrieval_used=bool(memory_used_trace),
                web_required=str(decision_core.get("web_decision") or "off")
                == "required",
                planner_used=False,
                blocker_hard=False,
            )
            _handoff_err_msg = (
                "Plan/agent się wywalił po mojej stronie (tak, wiem, klasyk) — "
                "daj mu drugą szansę za moment albo uprość pytanie."
            )
            self._write_back_experience(
                turn=turn,
                response_text=_handoff_err_msg,
                grounding_mode="agent_handoff_error",
                tool_calls=[],
                tool_results=[],
                trace=handoff_err_trace,
                errors=errors,
                psyche_snapshot=psyche_snapshot,
                decision_core=decision_core,
            )
            if str(getattr(turn, "runtime_mode", "") or "").lower() == "audit":
                handoff_err_trace["psyche_snapshot_happened"] = False
                handoff_err_trace["experience_write_back_attempted"] = False
                handoff_err_trace["experience_write_back_succeeded"] = False
            self._run_runtime_experience_feedback(turn.user_id, handoff_err_trace)
            return ChatTurnResult(
                ok=False,
                response_text=_handoff_err_msg,
                model="agent_runtime",
                provider="executive_controller",
                tool_calls=[],
                tool_results=[],
                selected_mode=turn.mode or CHAT_DEFAULT_MODE,
                usage=ProviderUsage(
                    prompt_tokens=0, completion_tokens=0, total_tokens=0
                ),
                trace=handoff_err_trace,
                errors=errors,
                debug=None,
            )

        # Extract agent execution summary
        exec_summary = agent_response.get("execution_summary", {})
        action_summary = exec_summary.get("action_summary", "")
        agent_errors = agent_response.get("errors", [])
        agent_trace = agent_response.get("trace", {})

        user_rt = (agent_response.get("response_text") or "").strip()
        mode = turn.mode or CHAT_DEFAULT_MODE
        if mode in ("agent", "debug"):
            response_text = (
                user_rt
                or action_summary
                or "Wykonałem zadanie przez agent runtime (planner+reasoning)."
            )
        else:
            response_text = synthesize_chat_handoff_user_text(
                user_message=turn.message,
                internal_reply=user_rt,
                action_summary=str(action_summary or ""),
                cycle=cycle,
                agent_ok=bool(agent_response.get("ok", False)),
            )

        # Map agent trace to chat trace structure
        duration_ms = (time.monotonic() - started) * 1000.0
        trace = {
            "provider_calls": 0,  # No LLM provider used
            "tool_iterations": 0,
            "tool_calls_requested": 0,
            "tool_calls_executed": 0,
            "tool_calls_successful": 0,
            "tool_failures": 0,
            "used_tools": False,  # Agent runtime doesn't expose tool_calls in chat contract
            "used_fallback": False,
            "response_grounding_mode": "agent_handoff",
            "duration_ms": duration_ms,
            **self._correction_trace_fields(ctx),
            "provider": "executive_controller",
            "model": "planner+reasoning",
            # Decision core fields
            "selected_strategy": decision_core["selected_strategy"],
            **self._decision_core_trace_escalation(decision_core),
            "reason_codes": decision_core["reason_codes"],
            "strategy_confidence": decision_core["strategy_confidence"],
            "degraded": decision_core["strategy_degraded"],
            "selected_goal": decision_core.get("selected_goal"),
            # Agent handoff fields (NEW)
            "agent_handoff_triggered": True,
            "agent_handoff_reason": handoff_reason,
            "effective_runtime_path": "agent_handoff",
            "advisory_strategy": decision_core["selected_strategy"],
            "planner_executed": agent_response.get("planning_used", False),
            "reasoning_executed": agent_response.get("reasoning_used", False),
            # Agent execution details
            "agent_cycle_id": agent_trace.get("cycle_id", ""),
            "agent_executed_task_ids": agent_trace.get("executed_task_ids", []),
            "agent_runtime_generated_task_ids": agent_trace.get(
                "runtime_generated_task_ids", []
            ),
            "agent_steps_executed": exec_summary.get("steps_executed", 0),
            # Decision core auxiliary fields
            "simulation_ran": decision_core["simulation_ran"],
            "simulation_best_action": decision_core["simulation_best_action"],
            "simulation_variants_count": decision_core["simulation_variants_count"],
            "simulation_risk_summary": decision_core["simulation_risk_summary"],
            "policy_hints_loaded": decision_core["policy_hints_loaded"],
            "policy_profile_name": decision_core["policy_profile_name"],
            "consistency_check_ran": decision_core["consistency_check_ran"],
            "consistency_classification": decision_core["consistency_classification"],
            "contradictions_found": decision_core["contradictions_found"],
            "experience_lookup_happened": decision_core.get(
                "experience_lookup_happened", False
            ),
            "experience_matches_count": decision_core.get(
                "experience_matches_count", 0
            ),
            "experience_influenced_strategy": decision_core.get(
                "experience_influenced_strategy", False
            ),
            "experience_confidence_adjustment": decision_core.get(
                "experience_confidence_adjustment"
            ),
            "experience_handoff_bias": decision_core.get("experience_handoff_bias"),
            "experience_blocker_reason": decision_core.get("experience_blocker_reason"),
            "experience_signal_summary": decision_core.get("experience_signal_summary"),
            "memory_lookup_happened": memory_lookup_flag,
            "psyche_snapshot_happened": False,
            "research_was_required": str(decision_core.get("web_decision") or "off")
            == "required",
            "experience_write_back_attempted": False,
            "experience_write_back_succeeded": False,
            "agentic_executed": True,
            "tool_calls_count": int(exec_summary.get("steps_executed") or 0),
            # ── Controlled Web Orchestration V1 ──
            "controlled_web_decision": decision_core.get("web_decision", "off"),
            "controlled_web_decision_reason": decision_core.get(
                "web_decision_reason", "not_evaluated"
            ),
            "controlled_web_triggered": False,
            "controlled_web_reason": "agent_handoff",
            "controlled_web_tool": None,
            "controlled_web_ok": None,
            "controlled_web_has_results": None,
            "controlled_web_provider_info": None,
            "controlled_web_query": None,
            "controlled_web_source_count": 0,
            "controlled_web_freshness_needed": self._is_freshness_needed(
                decision_core.get("reason_codes", [])
            ),
            "reflection_ran": False,
            "reflection_summary": None,
            # ── Policy Feedback Loop trace fields ──
            "policy_feedback_loaded": bool(decision_core.get("policy_feedback_loaded")),
            "policy_feedback_applied": bool(
                decision_core.get("policy_feedback_applied")
            ),
            "policy_feedback_summary": decision_core.get("policy_feedback_summary", ""),
            "policy_confidence_delta": decision_core.get(
                "policy_confidence_delta", 0.0
            ),
            "policy_handoff_bias": decision_core.get("policy_handoff_bias", 0.0),
            "policy_blocker_sensitivity": decision_core.get(
                "policy_blocker_sensitivity", 0.0
            ),
            "policy_simulation_risk_cal": decision_core.get(
                "policy_simulation_risk_cal", 0.0
            ),
            "policy_strategy_adjustments": decision_core.get(
                "policy_strategy_adjustments", {}
            ),
        }

        # Expose existing agent-cycle fields on chat trace (UI observability; no logic change).
        if isinstance(agent_response, dict):
            if "strategy_source" in agent_response:
                trace["strategy_source"] = agent_response["strategy_source"]
            if "strategy_authority_external" in agent_response:
                trace["strategy_authority_external"] = bool(
                    agent_response["strategy_authority_external"]
                )
            _exec_strat = agent_response.get("strategy")
            if _exec_strat is not None and str(_exec_strat).strip():
                trace["executive_strategy"] = str(_exec_strat)

        if memory_used_trace:
            trace["memory_used"] = memory_used_trace
        self._augment_memory_observability(trace, memory_used_trace, memory_context)

        trace["chat_handoff_evaluated"] = True
        trace["chat_handoff_executed"] = True
        trace["chat_handoff_skip_reason"] = None
        trace_blocker_gate_outcome(trace, gate_evaluated=True, hard_applied=False)
        _planning_used = bool(agent_response.get("planning_used", False))
        _bv_snap = (
            blocker_verdict.model_dump()
            if blocker_verdict is not None
            else BlockerVerdict.allow().model_dump()
        )
        merge_canonical_executive_handoff_success(
            trace,
            decision_core=decision_core,
            memory_retrieval_used=bool(memory_used_trace),
            planning_used=_planning_used,
            blocker_verdict_snapshot=_bv_snap,
        )

        trace["agent_internal_response_text"] = user_rt or None
        trace["chat_handoff_synthesized"] = mode not in ("agent", "debug")

        # Map agent errors to chat errors
        for err in agent_errors:
            errors.append({"type": "agent_cycle_error", **err})

        self._write_back_experience(
            turn=turn,
            response_text=response_text,
            grounding_mode="agent_handoff",
            tool_calls=[],
            tool_results=[],
            trace=trace,
            errors=errors,
            psyche_snapshot=psyche_snapshot,
            decision_core=decision_core,
        )
        if str(getattr(turn, "runtime_mode", "") or "").lower() == "audit":
            trace["psyche_snapshot_happened"] = False
            trace["experience_write_back_attempted"] = False
            trace["experience_write_back_succeeded"] = False

        self._run_runtime_experience_feedback(turn.user_id, trace)

        result = ChatTurnResult(
            ok=agent_response.get("ok", False) and len(errors) == 0,
            response_text=response_text,
            model="planner+reasoning",
            provider="executive_controller",
            tool_calls=[],  # Agent doesn't expose tool_calls in chat contract
            tool_results=[],
            selected_mode=turn.mode or CHAT_DEFAULT_MODE,
            usage=ProviderUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            trace=trace,
            errors=errors,
            debug={"agent_response": agent_response} if turn.include_debug else None,
        )

        # Log event and cache trace
        _cr_hook("append_event", append_event)(
            turn.user_id,
            "chat.turn",
            {
                "ok": result.ok,
                "provider": "executive_controller",
                "model": "planner+reasoning",
                "trace": result.trace,
                "agent_handoff": True,
            },
        )
        _TRACE_CACHE[turn.user_id].append(result.trace)

        return result

    async def _provider_call(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ProviderToolSpec],
    ) -> ModelResponse:
        # Canonical provider path — timeout/retry/classification via ProviderExecutionService.
        # Legacy TypeError signature sniffing removed.
        from aihub.turn.models import RuntimeEnvironment
        from aihub.chat_contracts import ToolCallRequest as _TCR

        env = RuntimeEnvironment()
        active = getattr(self, "_active_turn_ctx", None)
        if active is not None:
            env = active.environment
        cancelled = bool(getattr(getattr(active, "cancellation", None), "cancelled", False))
        remaining = float(getattr(active, "remaining_s", None) or env.provider_total_timeout_s)
        exec_res = await self._provider_service.execute(
            messages=messages,
            tools=tools,
            environment=env,
            cancelled=cancelled,
            remaining_s=remaining,
            trace=getattr(self, "_active_trace_builder", None),
        )
        tool_calls = []
        for idx, tc in enumerate(exec_res.tool_calls or []):
            if isinstance(tc, dict):
                tool_calls.append(
                    _TCR(
                        tool_call_id=str(tc.get("tool_call_id") or f"tool-{idx}"),
                        name=str(tc.get("name") or "tool"),
                        arguments=dict(tc.get("arguments") or {}),
                    )
                )
        usage = exec_res.usage if isinstance(exec_res.usage, ProviderUsage) else ProviderUsage()
        if isinstance(exec_res.usage, dict):
            try:
                usage = ProviderUsage(**{k: exec_res.usage.get(k, 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")} | {"reporting_mode": exec_res.usage.get("reporting_mode", "unavailable")})
            except Exception:
                usage = ProviderUsage()
        return ModelResponse(
            provider=exec_res.provider or self._current_provider_name(),
            model=exec_res.model or LLM_MODEL_NAME,
            content=exec_res.content,
            finish_reason=exec_res.finish_reason or "stop",
            tool_calls=tool_calls,
            usage=usage,
            latency_ms=float(exec_res.latency_ms or 0.0),
        )
        # Unreachable legacy path retained below for reference during migration — never executed
        use_stream = stream_session_active() and LLM_STREAMING_ENABLED and not tools
        req = ProviderChatRequest(
            messages=messages,
            model=LLM_MODEL_NAME,
            tools=tools,
            stream=use_stream,
        )
        generate = getattr(self._provider, "generate")
        try:
            raw = await generate(req)
        except TypeError as exc:
            msg = str(exc)
            if "missing 1 required positional argument" not in msg:
                raise
            raw = await generate(self._provider, req)

        if isinstance(raw, ModelResponse):
            return raw
        if isinstance(raw, dict):
            message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
            content = str(raw.get("content") or message.get("content") or "")
            raw_tool_calls = raw.get("tool_calls") or message.get("tool_calls") or []
            tool_calls: list[ToolCallRequest] = []
            for idx, call in enumerate(raw_tool_calls):
                if isinstance(call, ToolCallRequest):
                    tool_calls.append(call)
                elif isinstance(call, dict):
                    tool_calls.append(
                        ToolCallRequest(
                            tool_call_id=str(
                                call.get("tool_call_id")
                                or call.get("id")
                                or f"tool-{idx}"
                            ),
                            name=str(call.get("name") or call.get("function", {}).get("name") or "tool"),
                            arguments=dict(
                                call.get("arguments")
                                or call.get("function", {}).get("arguments")
                                or {}
                            ),
                        )
                    )
            usage_obj = raw.get("usage")
            usage = usage_obj if isinstance(usage_obj, ProviderUsage) else ProviderUsage()
            return ModelResponse(
                provider=str(getattr(self._provider, "provider_name", "mock")),
                model=str(raw.get("model") or LLM_MODEL_NAME),
                content=content,
                finish_reason=str(raw.get("finish_reason") or raw.get("stop_reason") or "stop"),
                tool_calls=tool_calls,
                usage=usage,
                latency_ms=float(raw.get("latency_ms") or 0.0),
                raw_response_id=str(raw.get("raw_response_id") or raw.get("id") or ""),
            )
        if all(hasattr(raw, attr) for attr in ("content", "model", "provider")):
            raw_tool_calls = list(getattr(raw, "tool_calls", []) or [])
            tool_calls: list[ToolCallRequest] = []
            for idx, call in enumerate(raw_tool_calls):
                if isinstance(call, ToolCallRequest):
                    tool_calls.append(call)
                elif isinstance(call, dict):
                    tool_calls.append(
                        ToolCallRequest(
                            tool_call_id=str(call.get("tool_call_id") or call.get("id") or f"tool-{idx}"),
                            name=str(call.get("name") or call.get("function", {}).get("name") or "tool"),
                            arguments=dict(call.get("arguments") or call.get("function", {}).get("arguments") or {}),
                        )
                    )
            usage_obj = getattr(raw, "usage", None)
            if isinstance(usage_obj, ProviderUsage):
                usage = usage_obj
            elif usage_obj is not None:
                usage = ProviderUsage(
                    prompt_tokens=int(getattr(usage_obj, "prompt_tokens", 0) or 0),
                    completion_tokens=int(getattr(usage_obj, "completion_tokens", 0) or 0),
                    total_tokens=int(getattr(usage_obj, "total_tokens", 0) or 0),
                    reporting_mode=str(getattr(usage_obj, "reporting_mode", "unavailable") or "unavailable"),
                )
            else:
                usage = ProviderUsage()
            return ModelResponse(
                provider=str(getattr(raw, "provider", self._current_provider_name()) or self._current_provider_name()),
                model=str(getattr(raw, "model", LLM_MODEL_NAME) or LLM_MODEL_NAME),
                content=str(getattr(raw, "content", "") or getattr(raw, "text", "") or ""),
                finish_reason=str(getattr(raw, "finish_reason", "stop") or "stop"),
                tool_calls=tool_calls,
                usage=usage,
                latency_ms=float(getattr(raw, "latency_ms", 0.0) or 0.0),
                raw_response_id=str(getattr(raw, "raw_response_id", "") or getattr(raw, "id", "") or ""),
            )
        raise TypeError(f"provider.generate returned unsupported type: {type(raw).__name__}")

    @staticmethod
    def _sum_usage(parts: list[ProviderUsage]) -> ProviderUsage:
        if not parts:
            return ProviderUsage(reporting_mode="unavailable")

        modes = {str(p.reporting_mode or "unavailable") for p in parts}
        if modes == {"provider"}:
            reporting_mode = "provider"
        elif "provider" in modes:
            reporting_mode = "partial"
        else:
            reporting_mode = "unavailable"

        return ProviderUsage(
            prompt_tokens=sum(p.prompt_tokens for p in parts),
            completion_tokens=sum(p.completion_tokens for p in parts),
            total_tokens=sum(p.total_tokens for p in parts),
            reporting_mode=reporting_mode,
        )

    async def _provider_failure_fallback(
        self,
        turn: ChatTurnInput,
        *,
        reason: str,
        decision_core: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        # Dry, neutral fallback — never a personified "I'm alive / coffee" line (06.07 quality fix).
        fallback_text = dry_fallback_response(user_message=turn.message)

        try:
            controller = _cr_hook("get_executive_controller", get_executive_controller)()
            dc = decision_core if isinstance(decision_core, dict) else {}
            fstr, freason = map_chat_execution_mode_to_force_strategy(dc)
            cycle = await controller.run_cycle(
                {
                    "text": turn.message,
                    "max_steps": 4,
                    "timeout_seconds": 12.0,
                    "force_strategy": fstr,
                    "force_strategy_reason": f"{freason};chat_runtime:provider_fallback",
                },
                mode="run",
                user_id=turn.user_id,
            )
            normalized = _cr_hook("build_agent_cycle_response", build_agent_cycle_response)(
                cycle, include_debug=turn.include_debug
            )
            return fallback_text, {"reason": reason, "fallback_cycle": normalized}
        except Exception as exc:  # noqa: BLE001
            return (
                fallback_text,
                {
                    "reason": reason,
                    "fallback_cycle": None,
                    "fallback_error": str(exc),
                    "degraded": True,
                },
            )

    @staticmethod
    def _safe_preview(obj: Any, max_chars: int = 600) -> str:
        try:
            text = json.dumps(obj, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(obj)
        if len(text) > max_chars:
            return text[:max_chars] + " ...[TRUNCATED]"
        return text

    @staticmethod
    def _tokenize_for_similarity(text: str) -> set[str]:
        """Tokenize text for lightweight heuristic similarity matching."""
        raw = re.findall(r"[\wąćęłńóśźż]{3,}", (text or "").lower())
        stop = {
            "oraz",
            "który",
            "która",
            "które",
            "których",
            "przez",
            "jest",
            "było",
            "będzie",
            "tego",
            "that",
            "this",
            "with",
            "from",
            "have",
            "will",
            "into",
            "without",
        }
        return {t for t in raw if t not in stop}

    def _current_provider_name(self) -> str:
        return str(
            getattr(
                self._provider,
                "provider_name",
                getattr(self._provider, "name", "mock"),
            )
        )

