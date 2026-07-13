#!/usr/bin/env python3

"""TurnOps — composition of stage mixins + turn orchestration."""

from __future__ import annotations

import json
from typing import Any

from aihub.chat_contracts import ChatTurnInput, ChatTurnResult
from aihub.turn._ops_ns import WEB_REQUIRED_QUERY_KEYWORDS  # noqa: F401
import aihub.turn._ops_ns as _ops_ns
from aihub.turn._ops_ns import (  # noqa: F401
    LLM_MODEL_NAME,
    ToolRouter,
    _TRACE_CACHE,
    _cr_hook,
    append_event,
    copy,
    emit_memory_used,
    emit_status,
    get_default_provider,
    get_memory_core,
    get_psyche_core,
    get_tool_registry,
    logger,
    memory_results_count_for_trace,
    memory_truth_for_prompt,
    record_user_correction_turn,
    stream_session_active,
    time,
    uuid,
    web_grounding_in_prompt,
    augment_trace_context_truth,
    build_history_trace,
    merge_canonical_for_llm_path,
    merge_canonical_decision_trace,
    trace_blocker_gate_outcome,
    ROUTE_BLOCKED_HARD,
    ROUTE_AGENT_HANDOFF_ERROR,
    merge_canonical_executive_handoff_success,
    merge_canonical_web_required_ungrounded,
    llm_path_verified_research_grounding,
    CHAT_MAX_TOOL_ITERATIONS,
    LLM_TOOL_CALLING_ENABLED,
    LLM_STREAMING_ENABLED,
    ToolCallRequest,
    ToolCallResult,
    ProviderUsage,
    ModelResponse,
    ChatMessage,
    ChatTurnContext,
    BlockerVerdict,
    ProviderError,
    ProviderChatRequest,
    ToolExecutionContext,
    sanitize_user_message_for_llm,
    smart_clip_chat_history,
    build_attachment_prompt_block,
    summarize_attachments_for_user,
    MAX_FILES_PER_TURN,
    fetch_recent_session_attachment_ids,
    clamp_ungrounded_speculative_reply,
    dry_fallback_response,
    ResponseVariantsEngine,
    synthesize_chat_handoff_user_text,
    build_agent_cycle_response,
    get_executive_controller,
    map_chat_execution_mode_to_force_strategy,
    is_image_generation_intent,
    listing_copy_no_web_intent,
    short_followup_no_web_intent,
)
# Ensure underscore helpers used by run_turn_core body are bound.
from aihub.turn.mixins import (
    DecisionMixin,
    ExecutionMixin,
    ExperienceMixin,
    PromptContextMixin,
    WebMixin,
)

class TurnOps(
    ExperienceMixin,
    DecisionMixin,
    WebMixin,
    PromptContextMixin,
    ExecutionMixin,
):
    def __init__(self) -> None:
        self._provider = get_default_provider()
        self._tool_registry = get_tool_registry()
        self._tool_router = ToolRouter(self._tool_registry)
        from aihub.turn.provider_service import ProviderExecutionService
        from aihub.turn.tool_service import ToolExecutionService
        from aihub.turn.completion import TurnCompletionService
        self._provider_service = ProviderExecutionService(self._provider)
        self._tool_service = ToolExecutionService(self._tool_router)
        self._completion_service = TurnCompletionService()
        self._active_turn_ctx = None
        self._active_trace_builder = None
        # Managed hooks kept as attributes so runtime wiring is explicit and testable.
        self._memory_process_fn = get_memory_core().ingest_turn
        self._psyche_evolve_fn = get_psyche_core().evolve

    async def run_turn(self, turn: ChatTurnInput) -> ChatTurnResult:
        res: ChatTurnResult | None = None
        err: BaseException | None = None
        try:
            res = await self.run_turn_core(turn)
            self._apply_persona_guard(turn, res)
            return res
        except BaseException as exc:
            err = exc
            raise
        finally:
            if str(getattr(turn, "runtime_mode", "") or "").lower() != "audit":
                try:
                    from aihub.chat_session_transcript import persist_chat_turn_messages

                    persist_chat_turn_messages(turn, res, err)
                except Exception:
                    logger.exception("chat session transcript persist failed")

    async def run_turn_core(self, turn: ChatTurnInput) -> ChatTurnResult:
        started = time.monotonic()
        correction_turn_trace = record_user_correction_turn(turn)

        from aihub.chat_deterministic import (
            try_deterministic_turn,
            try_memory_fact_read_turn,
        )

        det = try_deterministic_turn(turn, started_monotonic=started)
        if det is not None:
            try:
                from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context

                det.trace.update(
                    self._final_behavior_trace_fields(
                        build_psyche_v2_behavior_context(turn.user_id)
                    )
                )
            except Exception as exc:
                logger.debug(
                    "deterministic trace: psyche behavior fields skipped: %s", exc
                )
                det.trace.update(self._final_behavior_trace_fields(None))
            det.trace.update(
                self._correction_trace_flat(correction_turn_trace, hints_chars=0)
            )
            _cr_hook("append_event", append_event)(
                turn.user_id,
                "chat.turn",
                {
                    "ok": True,
                    "provider": det.provider,
                    "model": det.model,
                    "trace": det.trace,
                    "tool_calls": [],
                    "tool_results": [],
                },
            )
            _TRACE_CACHE[turn.user_id].append(det.trace)
            return det

        # Jedno ``retrieve_context`` na turę — przed decision_core / LLM; krótki fakt bez modelu.
        ctx = self._build_context(turn, correction_turn_trace=correction_turn_trace)
        mem_fact = try_memory_fact_read_turn(
            turn, ctx.memory_context, started_monotonic=started
        )
        if mem_fact is not None:
            try:
                from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context

                mem_fact.trace.update(
                    self._final_behavior_trace_fields(
                        build_psyche_v2_behavior_context(turn.user_id)
                    )
                )
            except Exception as exc:
                logger.debug(
                    "memory_fact trace: psyche behavior fields skipped: %s", exc
                )
                mem_fact.trace.update(self._final_behavior_trace_fields(None))
            mem_fact.trace.update(
                self._correction_trace_flat(correction_turn_trace, hints_chars=0)
            )
            _cr_hook("append_event", append_event)(
                turn.user_id,
                "chat.turn",
                {
                    "ok": True,
                    "provider": mem_fact.provider,
                    "model": mem_fact.model,
                    "trace": mem_fact.trace,
                    "tool_calls": [],
                    "tool_results": [],
                },
            )
            _TRACE_CACHE[turn.user_id].append(mem_fact.trace)
            return mem_fact

        psyche_snapshot = copy.deepcopy(
            get_psyche_core().ensure_user(turn.user_id) or {}
        )

        # V2 Bridge Snapshots (read-only foundation)
        memory_v2_snapshot: dict[str, Any] = {}
        psyche_v2_snapshot: dict[str, Any] = {}
        identity_bridge_snapshot = None
        memory_v2_runtime_ctx = None
        psyche_v2_behavior_ctx = None
        try:
            from aihub.runtime_identity_bridge import (
                build_identity_bridge_snapshot as build_identity_snapshot,
            )
            from aihub.runtime_memory_bridge import (
                build_memory_v2_runtime_context,
                build_memory_v2_runtime_snapshot,
            )
            from aihub.runtime_psyche_bridge import (
                build_psyche_v2_behavior_context,
                build_psyche_v2_runtime_snapshot,
            )

            memory_v2_snapshot = build_memory_v2_runtime_snapshot(
                turn.user_id, turn.message
            )
            psyche_v2_snapshot = build_psyche_v2_runtime_snapshot(turn.user_id)
            identity_bridge_snapshot = build_identity_snapshot(
                turn.user_id, turn.message
            )

            # Production runtime contexts for behavior injection
            memory_v2_runtime_ctx = build_memory_v2_runtime_context(
                turn.user_id, turn.message
            )
            psyche_v2_behavior_ctx = build_psyche_v2_behavior_context(turn.user_id)
        except Exception as bridge_error:
            logger.warning(f"Failed to load V2 bridges: {bridge_error}")

        try:
            if memory_v2_runtime_ctx is not None or psyche_v2_behavior_ctx is not None:
                from aihub.psyche_v2_repository import ensure_psyche_profile
                from aihub.runtime_psyche_bridge import apply_consistency_to_contexts

                _prof = ensure_psyche_profile(turn.user_id)
                memory_v2_runtime_ctx, psyche_v2_behavior_ctx, _consistency = (
                    apply_consistency_to_contexts(
                        memory_v2_runtime_ctx,
                        psyche_v2_behavior_ctx,
                        _prof.core_caution,
                    )
                )
        except Exception as consistency_error:
            logger.debug(
                "Self-consistency pass skipped: %s", consistency_error, exc_info=True
            )

        mem_truth = memory_truth_for_prompt(ctx.memory_context)
        memory_lookup_flag = bool(mem_truth["memory_retrieval_has_rows"])
        memory_substantive_flag = bool(mem_truth["memory_substantive_in_prompt"])
        include_stm_in_memory_brief = len(turn.history or []) == 0
        memory_brief = self._build_memory_brief(
            ctx.memory_context,
            include_stm=include_stm_in_memory_brief,
        )
        memory_used_trace = self._build_memory_used_trace(
            ctx.memory_context,
            include_stm=include_stm_in_memory_brief,
        )
        if stream_session_active():
            await emit_status("thinking", label_pl="Analizuję…")
            await emit_status("memory", label_pl="Sprawdzam kontekst…")
            mem_total = memory_results_count_for_trace(ctx.memory_context)
            if memory_lookup_flag and mem_total > 0:
                await emit_memory_used(count=mem_total)
        psyche_brief = self._build_psyche_brief(psyche_snapshot)
        tools = self._build_provider_tools(ctx)
        tool_results: list[ToolCallResult] = []
        tool_calls: list[ToolCallRequest] = []
        provider_usages: list[ProviderUsage] = []
        errors: list[dict[str, Any]] = []

        controlled_web: dict[str, Any] = {
            "triggered": False,
            "reason": "not_required",
            "tool_name": None,
            "ok": None,
            "has_results": None,
            "provider_info": None,
            "query": None,
            "source_count": 0,
            "freshness_needed": False,
        }

        # ── Decision Core (pre-execution): strategy, simulation, policy, consistency ──
        # Runs BEFORE web prefetch so that web_decision drives execution.
        decision_core = self._pre_exec_decision_core(
            turn=turn,
            ctx=ctx,
            psyche_snapshot=psyche_snapshot,
            memory_v2_runtime_ctx=memory_v2_runtime_ctx,
            psyche_v2_behavior_ctx=psyche_v2_behavior_ctx,
        )
        tools = self._apply_strategy_to_tools(tools, decision_core["selected_strategy"])
        if not decision_core.get("escalation_use_tools"):
            tools = []

        # ── Blocker Verdict Gate ────────────────────────────────────────────
        blocker_verdict = self._evaluate_blocker_verdict(decision_core)

        if blocker_verdict.hard:
            # Hard blocker: return early, NO provider call.
            duration_ms = (time.monotonic() - started) * 1000.0
            blocker_trace = {
                "provider_calls": 0,
                "tool_iterations": 0,
                "used_tools": False,
                "used_fallback": False,
                "response_grounding_mode": "blocker_hard_gate",
                "duration_ms": duration_ms,
                **self._correction_trace_fields(ctx),
                "selected_strategy": decision_core["selected_strategy"],
                **self._decision_core_trace_escalation(decision_core),
                "reason_codes": decision_core["reason_codes"] + ["BLOCKER_HARD_GATE"],
                "strategy_confidence": decision_core["strategy_confidence"],
                "degraded": decision_core.get("strategy_degraded", False),
                "memory_lookup_happened": memory_lookup_flag,
                "psyche_snapshot_happened": bool(psyche_snapshot),
                "research_was_required": False,
                "agentic_executed": False,
                "tool_calls_count": 0,
                "experience_write_back_attempted": False,
                "experience_write_back_succeeded": False,
                "blocker_verdict": blocker_verdict.model_dump(),
                # ── Controlled Web Orchestration V1 ──
                "controlled_web_decision": decision_core.get("web_decision", "off"),
                "controlled_web_decision_reason": decision_core.get(
                    "web_decision_reason", "not_evaluated"
                ),
                "controlled_web_triggered": False,
                "controlled_web_reason": "blocker_hard_gate",
                "controlled_web_tool": None,
                "controlled_web_ok": None,
                "controlled_web_has_results": None,
                "controlled_web_provider_info": None,
                "controlled_web_query": None,
                "controlled_web_source_count": 0,
                "controlled_web_freshness_needed": self._is_freshness_needed(
                    decision_core.get("reason_codes", [])
                ),
                "experience_lookup_happened": decision_core.get(
                    "experience_lookup_happened", False
                ),
                "experience_matches_count": decision_core.get(
                    "experience_matches_count", 0
                ),
                "experience_influenced_strategy": decision_core.get(
                    "experience_influenced_strategy", False
                ),
                "experience_blocker_reason": decision_core.get(
                    "experience_blocker_reason"
                ),
                "experience_signal_summary": decision_core.get(
                    "experience_signal_summary"
                ),
                "consistency_check_ran": decision_core["consistency_check_ran"],
                "consistency_classification": decision_core[
                    "consistency_classification"
                ],
                "contradictions_found": decision_core["contradictions_found"],
                "simulation_ran": decision_core["simulation_ran"],
                "simulation_best_action": decision_core["simulation_best_action"],
                "selected_goal": decision_core.get("selected_goal"),
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
            }
            if memory_used_trace:
                blocker_trace["memory_used"] = memory_used_trace
            trace_blocker_gate_outcome(
                blocker_trace, gate_evaluated=True, hard_applied=True
            )
            blocker_trace["chat_handoff_evaluated"] = False
            _bt = blocker_verdict.blocker_type
            _bsrc = blocker_verdict.source or "unknown"
            merge_canonical_decision_trace(
                blocker_trace,
                selected_route=ROUTE_BLOCKED_HARD,
                route_reason=(
                    f"blocker_hard_gate|type={_bt}|source={_bsrc}|"
                    f"resolution={blocker_verdict.resolution}"
                ),
                decision_intent="blocked",
                deterministic_hit=False,
                vault_used=False,
                memory_retrieval_used=bool(memory_lookup_flag),
                web_required=str(decision_core.get("web_decision") or "off")
                == "required",
                planner_used=False,
                blocker_hard=True,
            )
            blocker_trace["memory_substantive_in_prompt"] = memory_substantive_flag
            blocker_trace["memory_stm_brief_included"] = include_stm_in_memory_brief
            augment_trace_context_truth(
                blocker_trace,
                mem_truth=memory_truth_for_prompt(ctx.memory_context),
                controlled_web={
                    "triggered": False,
                    "ok": None,
                    "has_results": None,
                },
                decision_core=decision_core,
                force_no_web_verified=True,
            )
            self._run_runtime_experience_feedback(turn.user_id, blocker_trace)
            _cr_hook("append_event", append_event)(
                turn.user_id,
                "chat.turn.blocked",
                {
                    "ok": False,
                    "blocker_type": blocker_verdict.blocker_type,
                    "blocker_reason": blocker_verdict.reason,
                    "blocker_source": blocker_verdict.source,
                    "blocker_resolution": blocker_verdict.resolution,
                    "user_message": blocker_verdict.user_message,
                    "trace": blocker_trace,
                },
            )
            result = ChatTurnResult(
                ok=False,
                response_text=blocker_verdict.user_message or blocker_verdict.reason,
                model="blocker_gate",
                provider="decision_core",
                tool_calls=[],
                tool_results=[],
                selected_mode=ctx.mode,
                usage=ProviderUsage(
                    prompt_tokens=0, completion_tokens=0, total_tokens=0
                ),
                trace=blocker_trace,
                errors=[
                    {
                        "type": "blocker_hard_gate",
                        "blocker_type": blocker_verdict.blocker_type,
                        "reason": blocker_verdict.reason,
                        "source": blocker_verdict.source,
                        "recommended_action": blocker_verdict.recommended_action,
                        "resolution": blocker_verdict.resolution,
                        "user_message": blocker_verdict.user_message,
                        "dev_message": blocker_verdict.dev_message,
                    }
                ],
            )
            _TRACE_CACHE[turn.user_id].append(result.trace)
            return result

        # ── Blocker Resolution: downgrade / reroute ─────────────────────────
        # These resolutions proceed with execution but modify the strategy.
        if blocker_verdict.blocker_active and blocker_verdict.resolution in (
            "downgrade",
            "reroute",
        ):
            new_strategy = blocker_verdict.next_best_action or "contextual"
            old_strategy = decision_core["selected_strategy"]
            if new_strategy != old_strategy:
                decision_core["selected_strategy"] = new_strategy
                decision_core["reason_codes"].append(
                    f"BLOCKER_{blocker_verdict.resolution.upper()}_"
                    f"{old_strategy.upper()}_TO_{new_strategy.upper()}"
                )
                logger.info(
                    "Blocker %s: strategy %s→%s for user=%s (type=%s)",
                    blocker_verdict.resolution,
                    old_strategy,
                    new_strategy,
                    turn.user_id,
                    blocker_verdict.blocker_type,
                )
                self._finalize_escalation(decision_core)
                tools = self._apply_strategy_to_tools(
                    self._build_provider_tools(ctx),
                    decision_core["selected_strategy"],
                )
                if not decision_core.get("escalation_use_tools"):
                    tools = []

        # ── Agent Handoff Gate ──────────────────────────────────────────────
        should_handoff, handoff_reason = self._should_handoff_to_agent(
            decision_core=decision_core,
            message=turn.message,
        )
        decision_core["chat_handoff_evaluated"] = True
        if should_handoff:
            decision_core.pop("chat_handoff_executed", None)
            decision_core.pop("chat_handoff_skip_reason", None)
        else:
            decision_core["chat_handoff_executed"] = False
            decision_core["chat_handoff_skip_reason"] = handoff_reason
        if should_handoff:
            if stream_session_active():
                await emit_status("tools", label_pl="Wykonuję kroki…")
            return await self._execute_agent_handoff(
                turn=turn,
                decision_core=decision_core,
                handoff_reason=handoff_reason,
                started=started,
                psyche_snapshot=psyche_snapshot,
                memory_used_trace=memory_used_trace,
                memory_lookup_flag=memory_lookup_flag,
                blocker_verdict=blocker_verdict,
                memory_context=ctx.memory_context,
                ctx=ctx,
            )

        # ── Controlled Web Prefetch (driven by web_decision) ───────────────
        # Runs AFTER decision_core and handoff gate, so web only fires for
        # the active chat path when strategy says it should.
        web_prefetch = await self._run_controlled_web_prefetch(
            turn=turn,
            ctx=ctx,
            web_decision=decision_core.get("web_decision", "off"),
        )
        if web_prefetch.get("triggered"):
            call_obj = web_prefetch.get("tool_call")
            result_obj = web_prefetch.get("tool_result")
            if isinstance(call_obj, ToolCallRequest):
                tool_calls.append(call_obj)
            if isinstance(result_obj, ToolCallResult):
                tool_results.append(result_obj)
                if not result_obj.ok:
                    errors.append(
                        {
                            "type": "controlled_web_error",
                            "error": result_obj.error or "unknown",
                            "tool": web_prefetch.get("tool_name"),
                        }
                    )
            controlled_web = {
                "triggered": True,
                "reason": web_prefetch.get("reason"),
                "tool_name": web_prefetch.get("tool_name"),
                "ok": result_obj.ok if isinstance(result_obj, ToolCallResult) else None,
                "has_results": self._assess_web_result_quality(result_obj),
                "provider_info": self._extract_web_provider_info(result_obj),
                "query": self._extract_web_query(
                    call_obj if isinstance(call_obj, ToolCallRequest) else None
                ),
                "source_count": self._count_web_sources(
                    result_obj if isinstance(result_obj, ToolCallResult) else None
                ),
                "freshness_needed": self._is_freshness_needed(
                    decision_core.get("reason_codes", [])
                ),
            }

        pre_messages = web_prefetch.get("messages") or []

        from aihub.chat_attachment_vision import enrich_image_attachments_for_turn

        effective_attached_ids = self._effective_attached_file_ids(turn)

        await enrich_image_attachments_for_turn(
            user_id=turn.user_id,
            session_id=turn.session_id,
            file_ids=list(effective_attached_ids),
        )

        attachment_block, attachment_meta = build_attachment_prompt_block(
            user_id=turn.user_id,
            session_id=turn.session_id,
            file_ids=list(effective_attached_ids),
        )
        attachments_summary = summarize_attachments_for_user(attachment_meta)

        first_turn_in_thread = len(turn.history or []) == 0
        history_rollup, hist_for_prompt = smart_clip_chat_history(turn.history)
        hist_smart_trim = {
            "chat_history_smart_trim_applied": bool(history_rollup),
            "chat_history_raw_tail_kept": len(hist_for_prompt),
            "chat_history_rollup_chars": len(history_rollup or ""),
        }
        user_llm_text, vault_user_redacted = sanitize_user_message_for_llm(turn.message)
        if (
            effective_attached_ids
            and int(attachment_meta.get("attachments_usable_count") or 0) == 0
        ):
            user_llm_text = (
                "[Priorytet: załączniki nie dostarczyły czytelnej treści do modelu. "
                "Odpowiedz krótko, co poszło nie tak (per plik), bez zgadywania treści "
                "ani formuł w stylu „może chodziło o…”.]\n\n" + user_llm_text
            )

        if self._web_required_grounding_unsatisfied(decision_core, controlled_web):
            return await self._finish_turn_web_required_ungrounded(
                turn=turn,
                ctx=ctx,
                started=started,
                decision_core=decision_core,
                blocker_verdict=blocker_verdict,
                controlled_web=controlled_web,
                tool_calls=tool_calls,
                tool_results=tool_results,
                errors=list(errors),
                memory_lookup_flag=memory_lookup_flag,
                memory_used_trace=memory_used_trace,
                include_stm_in_memory_brief=include_stm_in_memory_brief,
                psyche_snapshot=psyche_snapshot,
                attachment_meta=attachment_meta,
                attachments_summary=attachments_summary,
                hist_for_prompt_len=len(hist_for_prompt),
                vault_user_redacted=vault_user_redacted,
                hist_smart_trim=hist_smart_trim,
            )

        messages: list[ChatMessage] = [
            ChatMessage(
                role="system",
                content=self._build_system_prompt(
                    ctx,
                    memory_brief=memory_brief,
                    psyche_brief=psyche_brief,
                    decision_hints=decision_core["strategy_hints"],
                    correction_hints=str(
                        ctx.system_context.get("correction_hints_text") or ""
                    ),
                    memory_v2_context=memory_v2_runtime_ctx,
                    psyche_v2_context=psyche_v2_behavior_ctx,
                    files_context=attachment_block,
                    first_turn_in_thread=first_turn_in_thread,
                    history_rollup=history_rollup,
                    listing_sales_boost=listing_copy_no_web_intent(turn.message),
                ),
            ),
            *hist_for_prompt,
            *pre_messages,
            ChatMessage(role="user", content=user_llm_text),
        ]

        response_text = ""
        final_model = LLM_MODEL_NAME
        final_provider = self._current_provider_name()
        provider_call_count = 0
        usage_summary = self._sum_usage(provider_usages)

        for iteration in range(max(1, int(CHAT_MAX_TOOL_ITERATIONS)) + 1):
            provider_call_count += 1
            if stream_session_active():
                await emit_status("thinking", label_pl="Składam odpowiedź…")
            try:
                model_response = await self._provider_call(
                    messages=messages, tools=tools
                )
            except (ProviderError, Exception) as exc:
                from aihub.turn.errors import ProviderExecutionError as _PEE

                if not isinstance(exc, (ProviderError, _PEE)):
                    raise
                err_payload = (
                    exc.to_dict()
                    if isinstance(exc, ProviderError)
                    else {"message": str(exc), "code": getattr(exc.info, "code", "provider_error")}
                )
                errors.append({"type": "provider_error", **err_payload})
                fallback_text, fallback_trace = await self._provider_failure_fallback(
                    turn,
                    reason=str(getattr(exc, "message", None) or exc),
                    decision_core=decision_core,
                )
                if (
                    str(decision_core.get("web_decision") or "off") == "required"
                    and not llm_path_verified_research_grounding(
                        web_grounding_in_prompt(controlled_web), tool_results
                    )
                ):
                    fallback_text = self._web_required_ungrounded_user_message(
                        outcome=self._classify_web_required_failure(controlled_web)[0],
                        controlled_web=controlled_web,
                        errors=errors,
                    )

                # Diagnostic context (raw memory/psyche brief) must stay OUT of the user-facing
                # fallback text — dumping internal state read as low-quality/personified output
                # (06.07 response-quality fix). Keep it available only in debug mode.
                if turn.include_debug:
                    if memory_lookup_flag:
                        fallback_text += f"\n\n[Kontekst pamięci] {memory_brief[:900]}"
                    if psyche_brief != "BRAK DANYCH":
                        fallback_text += f"\n[Kontekst psyche] {psyche_brief}"
                web_any = next(
                    (
                        result
                        for result in tool_results
                        if any(
                            k in (result.name or "").lower()
                            for k in ("web", "research")
                        )
                    ),
                    None,
                )
                if web_any is not None:
                    web_payload = (
                        self._safe_preview(web_any.output, max_chars=700)
                        if web_any.ok
                        else f"błąd wykonania: {web_any.error or 'BRAK DANYCH'}"
                    )
                    fallback_text += f"\n\n[Controlled web] {web_payload}"

                # ── Fallback path: reflection (fail-soft) ──
                fallback_reflection = self._post_exec_reflection(
                    user_id=turn.user_id,
                    message=turn.message,
                    response_text=fallback_text,
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                    decision_core=decision_core,
                    blocker_verdict=blocker_verdict,
                    handoff_happened=False,
                )

                duration_ms = (time.monotonic() - started) * 1000.0
                usage_summary = self._sum_usage(provider_usages)
                trace = {
                    "provider_calls": provider_call_count,
                    "tool_iterations": iteration,
                    "fallback": fallback_trace,
                    "used_tools": len(tool_results) > 0,
                    "used_fallback": True,
                    "response_grounding_mode": "fallback",
                    "duration_ms": duration_ms,
                    **self._correction_trace_fields(ctx),
                    "provider": self._current_provider_name(),
                    "model": LLM_MODEL_NAME,
                    "usage_reporting_mode": usage_summary.reporting_mode,
                    "usage_total_tokens": usage_summary.total_tokens,
                    "selected_strategy": decision_core["selected_strategy"],
                    **self._decision_core_trace_escalation(decision_core),
                    "reason_codes": decision_core["reason_codes"],
                    "strategy_confidence": decision_core["strategy_confidence"],
                    "degraded": decision_core["strategy_degraded"],
                    "memory_lookup_happened": memory_lookup_flag,
                    "memory_results_count": memory_results_count_for_trace(
                        ctx.memory_context
                    ),
                    "psyche_snapshot_happened": False,
                    "research_was_required": self._has_research_tool(tool_calls),
                    "agentic_executed": False,
                    "tool_calls_count": len(tool_calls),
                    "experience_write_back_attempted": False,
                    "experience_write_back_succeeded": False,
                    # ── Controlled Web Orchestration V1 ──
                    "controlled_web_decision": decision_core.get("web_decision", "off"),
                    "controlled_web_decision_reason": decision_core.get(
                        "web_decision_reason", "not_evaluated"
                    ),
                    "controlled_web_triggered": bool(controlled_web.get("triggered")),
                    "controlled_web_reason": controlled_web.get("reason"),
                    "controlled_web_tool": controlled_web.get("tool_name"),
                    "controlled_web_ok": controlled_web.get("ok"),
                    "controlled_web_has_results": controlled_web.get("has_results"),
                    "controlled_web_provider_info": controlled_web.get("provider_info"),
                    "controlled_web_query": controlled_web.get("query"),
                    "controlled_web_source_count": controlled_web.get(
                        "source_count", 0
                    ),
                    "controlled_web_freshness_needed": controlled_web.get(
                        "freshness_needed", False
                    ),
                    "consistency_check_ran": decision_core["consistency_check_ran"],
                    "consistency_classification": decision_core[
                        "consistency_classification"
                    ],
                    # informational: count of detected contradictions, not execution-driving
                    "contradictions_found": decision_core["contradictions_found"],
                    "policy_hints_loaded": decision_core["policy_hints_loaded"],
                    "policy_profile_name": decision_core["policy_profile_name"],
                    "simulation_ran": decision_core["simulation_ran"],
                    "simulation_best_action": decision_core["simulation_best_action"],
                    "simulation_variants_count": decision_core[
                        "simulation_variants_count"
                    ],
                    # informational: human-readable risk string, not execution-driving
                    "simulation_risk_summary": decision_core["simulation_risk_summary"],
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
                    "experience_handoff_bias": decision_core.get(
                        "experience_handoff_bias"
                    ),
                    "experience_blocker_reason": decision_core.get(
                        "experience_blocker_reason"
                    ),
                    "experience_signal_summary": decision_core.get(
                        "experience_signal_summary"
                    ),
                    "reflection_ran": fallback_reflection["reflection_ran"],
                    "reflection_summary": fallback_reflection["reflection_summary"],
                    "selected_goal": decision_core.get("selected_goal"),
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
                    "policy_handoff_bias": decision_core.get(
                        "policy_handoff_bias", 0.0
                    ),
                    "policy_blocker_sensitivity": decision_core.get(
                        "policy_blocker_sensitivity", 0.0
                    ),
                    "policy_simulation_risk_cal": decision_core.get(
                        "policy_simulation_risk_cal", 0.0
                    ),
                    "policy_strategy_adjustments": decision_core.get(
                        "policy_strategy_adjustments", {}
                    ),
                    # ── Reflection hindsight fields ──
                    "reflection_strategy_fit": fallback_reflection.get(
                        "strategy_fit", "neutral"
                    ),
                    "reflection_handoff_hindsight": fallback_reflection.get(
                        "handoff_hindsight", "na"
                    ),
                    "reflection_blocker_hindsight": fallback_reflection.get(
                        "blocker_hindsight", "na"
                    ),
                    "reflection_confidence_hindsight": fallback_reflection.get(
                        "confidence_hindsight", 0.0
                    ),
                    "reflection_risk_hindsight": fallback_reflection.get(
                        "risk_hindsight", 0.0
                    ),
                    "attached_files": attachment_meta,
                    "attachments_summary": attachments_summary,
                    "blocker_verdict": blocker_verdict.model_dump(),
                }
                if memory_used_trace:
                    trace["memory_used"] = memory_used_trace
                self._augment_memory_observability(
                    trace, memory_used_trace, ctx.memory_context
                )
                trace_blocker_gate_outcome(
                    trace, gate_evaluated=True, hard_applied=False
                )
                merge_canonical_for_llm_path(
                    trace,
                    decision_core=decision_core,
                    grounding_mode="fallback",
                    memory_lookup_happened=memory_lookup_flag,
                    research_was_required=self._has_research_tool(tool_calls),
                    tool_calls=tool_calls,
                    web_verified_grounding_in_prompt=web_grounding_in_prompt(
                        controlled_web
                    ),
                    tool_results=tool_results,
                    used_fallback=True,
                    blocker_verdict_snapshot=blocker_verdict.model_dump(),
                )
                self._attach_web_observability_trace(
                    trace,
                    controlled_web=controlled_web,
                    tool_results=tool_results,
                    web_verified_in_prompt=web_grounding_in_prompt(controlled_web),
                )
                trace["memory_substantive_in_prompt"] = memory_substantive_flag
                trace.update(self._final_behavior_trace_fields(psyche_v2_behavior_ctx))
                trace.update({
                    "memory_v2_loaded": memory_v2_snapshot.get("loaded", False),
                    "memory_v2_match_count": memory_v2_snapshot.get("match_count", 0),
                    "memory_v2_reinforced_count": memory_v2_snapshot.get("reinforced_count", 0),
                    "memory_v2_suppressed_count": memory_v2_snapshot.get("suppressed_count", 0),
                    "memory_v2_contradictions_count": memory_v2_snapshot.get("contradictions_count", 0),
                    "memory_v2_actionable_contradictions_count": memory_v2_snapshot.get("actionable_contradictions_count", 0),
                    "memory_v2_transient_contradiction_count": memory_v2_snapshot.get("transient_contradiction_count", 0),
                    "memory_v2_procedures_count": memory_v2_snapshot.get("procedures_count", 0),
                    "memory_v2_top_reason_codes": memory_v2_snapshot.get("top_reason_codes", []),
                    "memory_v2_retrieval_explanation": memory_v2_snapshot.get("retrieval_strategy", ""),
                    "memory_v2_stability_tier_counts": (
                        dict(memory_v2_runtime_ctx.stability_tier_counts)
                        if memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded
                        else {}
                    ),
                    "memory_v2_procedure_confidence_raw": (
                        memory_v2_runtime_ctx.confidence_modifier_raw
                        if memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded
                        else 0.0
                    ),
                    "memory_v2_context_injected": bool(memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded),
                    "memory_v2_context_item_count": (
                        len(memory_v2_runtime_ctx.top_facts) + len(memory_v2_runtime_ctx.top_preferences)
                        if memory_v2_runtime_ctx
                        else 0
                    ),
                    "memory_v2_procedure_bias_applied": bool(
                        memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded and memory_v2_runtime_ctx.confidence_modifier > 0.6
                    ),
                    "memory_v2_contradiction_guard_applied": bool(
                        memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded and memory_v2_runtime_ctx.contradiction_alerts
                    ),
                    "psyche_v2_loaded": psyche_v2_snapshot.get("loaded", False),
                    "psyche_v2_mode": psyche_v2_snapshot.get("mode", "neutral"),
                    "psyche_v2_relation_trust": psyche_v2_snapshot.get("relation_trust", 0.5),
                    "psyche_v2_relation_friction": psyche_v2_snapshot.get("relation_friction", 0.0),
                    "psyche_v2_habit_biases": psyche_v2_snapshot.get("habit_biases", []),
                    "psyche_v2_behavior_style": psyche_v2_snapshot.get("behavior_policy", {}).get("directness", 0.5),
                    "psyche_v2_behavior_applied": bool(psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded),
                    "psyche_v2_style_mode": (
                        getattr(psyche_v2_behavior_ctx, "mode", "neutral")
                        if psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded
                        else "neutral"
                    ),
                    "psyche_v2_pressure_applied": bool(
                        psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded and getattr(psyche_v2_behavior_ctx, "pressure", 0.0) > 0.05
                    ),
                    "psyche_v2_relation_tone_applied": bool(
                        psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded and (getattr(psyche_v2_behavior_ctx, "warmth", 0.0) > 0.6 or getattr(psyche_v2_behavior_ctx, "friction", 0.0) > 0.4)
                    ),
                    "final_behavior_profile": (
                        {
                            "mode": getattr(psyche_v2_behavior_ctx, "mode", "neutral"),
                            "directness": psyche_v2_behavior_ctx.directness_bias,
                            "caution": psyche_v2_behavior_ctx.caution_bias,
                            "tool_bias": psyche_v2_behavior_ctx.tool_bias,
                            "web_bias": psyche_v2_behavior_ctx.web_bias,
                            "reassurance": psyche_v2_behavior_ctx.reassurance_bias,
                        }
                        if psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded
                        else {}
                    ),
                    "memory_v2_writeback_attempted": False,
                    "memory_v2_writeback_succeeded": False,
                    "memory_v2_new_items_count": 0,
                    "memory_v2_new_lessons_count": 0,
                    "psyche_v2_writeback_attempted": False,
                    "psyche_v2_writeback_succeeded": False,
                    "psyche_v2_event_applied": None,
                    "response_outcome_quality": "fallback",
                })
                trace["memory_stm_brief_included"] = include_stm_in_memory_brief
                trace["context_history_messages_attached"] = len(hist_for_prompt)
                trace["vault_user_message_redacted"] = vault_user_redacted
                trace.update(hist_smart_trim)
                augment_trace_context_truth(
                    trace,
                    mem_truth=mem_truth,
                    controlled_web=controlled_web,
                    decision_core=decision_core,
                )
                self._write_back_experience(
                    turn=turn,
                    response_text=fallback_text,
                    grounding_mode="fallback",
                    tool_calls=tool_calls,
                    tool_results=tool_results,
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
                _cr_hook("append_event", append_event)(
                    turn.user_id,
                    "chat.turn",
                    {
                        "ok": False,
                        "provider": self._current_provider_name(),
                        "model": LLM_MODEL_NAME,
                        "errors": errors,
                        "trace": trace,
                    },
                )
                result = ChatTurnResult(
                    ok=False,
                    response_text=fallback_text,
                    model=LLM_MODEL_NAME,
                    provider=self._current_provider_name(),
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                    selected_mode=ctx.mode,
                    usage=self._sum_usage(provider_usages),
                    trace=trace,
                    errors=errors,
                    debug={"context": ctx.model_dump()} if turn.include_debug else None,
                    attachments_summary=attachments_summary,
                )
                _TRACE_CACHE[turn.user_id].append(result.trace)
                return result

            final_model = model_response.model
            final_provider = model_response.provider
            provider_usages.append(model_response.usage)
            usage_summary = self._sum_usage(provider_usages)

            if model_response.tool_calls and iteration < max(
                1, int(CHAT_MAX_TOOL_ITERATIONS)
            ):
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=model_response.content,
                        tool_calls=model_response.tool_calls,
                    )
                )

                exec_ctx = ToolExecutionContext(
                    user_id=turn.user_id,
                    session_id=turn.session_id,
                    mode=ctx.mode,
                    include_debug=turn.include_debug,
                    policy_overrides=dict(turn.tool_policy_overrides or {}),
                )

                if stream_session_active():
                    await emit_status("tools", label_pl="Wykonuję kroki…")
                for call in model_response.tool_calls:
                    tool_calls.append(call)
                    tlabel = self._sse_tool_display_name(call.name)
                    if stream_session_active():
                        await emit_tool_event(tlabel, "start")
                    res = await self._tool_router.execute(call, exec_ctx)
                    if stream_session_active():
                        await emit_tool_event(tlabel, "done")
                    tool_results.append(res)
                    tool_payload = {
                        "ok": res.ok,
                        "output": res.output,
                        "error": res.error,
                    }
                    messages.append(
                        ChatMessage(
                            role="tool",
                            name=call.name,
                            tool_call_id=call.tool_call_id,
                            content=json.dumps(tool_payload, ensure_ascii=False),
                        )
                    )
                continue

            response_text = model_response.content or ""
            break

        if not response_text and tool_results:
            ok_results = [r for r in tool_results if r.ok]
            if ok_results:
                response_text = (
                    self._build_controlled_web_synthesis(
                        controlled_web=controlled_web,
                        tool_results=tool_results,
                    )
                    or "Narzędzia poszły, wyniki są — powiedz, jak je ułożyć w odpowiedź."
                )
            else:
                response_text = (
                    "Narzędzia w tej turze się potknęły — doprecyzuj, co dokładnie odpalić, "
                    "albo spróbuj jeszcze raz bez dramatu."
                )

        grounding_mode = self._classify_grounding_mode(
            used_fallback=False,
            tool_calls=tool_calls,
            tool_results=tool_results,
        )

        # ── Response Variants Deliberation ────────────────────────────
        # Conditionally generates up to 3 structurally distinct response
        # candidates, evaluates them, and synthesizes the final response.
        # Triggered only when the decision core signals uncertainty.
        deliberation_metadata: dict[str, Any] = {}
        if stream_session_active():
            await emit_status("finalizing", label_pl="Kończę odpowiedź…")
        try:
            # Build original messages as plain dicts for the engine
            original_msgs = [
                {
                    "role": m.role,
                    "content": m.content,
                    "name": m.name,
                    "tool_call_id": m.tool_call_id,
                }
                for m in messages
            ]
            (
                deliberated_text,
                deliberation_metadata,
            ) = await _cr_hook("ResponseVariantsEngine", ResponseVariantsEngine).run_deliberation(
                decision_core=decision_core,
                blocker_verdict=blocker_verdict,
                original_response=response_text,
                original_messages=original_msgs,
                provider_call_fn=self._provider_call,
                deliberation_history=decision_core.get("deliberation_history"),
            )
            if deliberation_metadata.get("response_variants_triggered"):
                response_text = deliberated_text
                logger.info(
                    "Deliberation replaced response_text: winner=%s confidence=%.2f",
                    deliberation_metadata.get("response_variants_winner_type", "?"),
                    deliberation_metadata.get("response_variants_confidence", 0.0),
                )
        except Exception:
            logger.warning(
                "Deliberation engine failed — using original response", exc_info=True
            )
            deliberation_metadata = {
                "response_variants_triggered": False,
                "response_variants_count": 0,
                "response_variants_reason_codes": [],
                "response_variants_error": True,
            }

        anti_hallucination_trace: dict[str, Any] = {}
        response_text = self._shape_response_text(
            turn=turn,
            ctx=ctx,
            response_text=response_text,
            grounding_mode=grounding_mode,
            used_fallback=False,
            memory_v2_context=memory_v2_runtime_ctx,
            psyche_v2_context=psyche_v2_behavior_ctx,
            anti_hallucination_trace=anti_hallucination_trace,
        )

        # ── Decision Core (post-execution): reflection on completed turn ──
        # Merge deliberation metadata into decision_core so _compute_deliberation_hindsight
        # sees the actual trigger/confidence/risk/winner data from this turn.
        for _dk in (
            "response_variants_triggered",
            "response_variants_confidence",
            "response_variants_risk",
            "response_variants_synthesis_used",
            "response_variants_winner_type",
        ):
            if _dk in deliberation_metadata:
                decision_core[_dk] = deliberation_metadata[_dk]
        # Compute and attach deliberation outcome quality for hindsight
        decision_core["deliberation_outcome_quality"] = (
            self._compute_deliberation_outcome_quality(deliberation_metadata)
        )

        post_reflection = self._post_exec_reflection(
            user_id=turn.user_id,
            message=turn.message,
            response_text=response_text,
            tool_calls=tool_calls,
            tool_results=tool_results,
            decision_core=decision_core,
            blocker_verdict=blocker_verdict,
            handoff_happened=False,
        )

        # Drugi przebieg kształtowania + refleksji na finalnym tekście (zachowanie produkcyjne;
        # pierwsza refleksja widzi wynik po 1. shape, druga — po ewentualnej korekcie stylu/guardów).
        response_text = self._shape_response_text(
            turn=turn,
            ctx=ctx,
            response_text=response_text,
            grounding_mode=grounding_mode,
            used_fallback=False,
            memory_v2_context=memory_v2_runtime_ctx,
            psyche_v2_context=psyche_v2_behavior_ctx,
            anti_hallucination_trace=anti_hallucination_trace,
        )
        for _dk in (
            "response_variants_triggered",
            "response_variants_confidence",
            "response_variants_risk",
            "response_variants_synthesis_used",
            "response_variants_winner_type",
        ):
            if _dk in deliberation_metadata:
                decision_core[_dk] = deliberation_metadata[_dk]
        decision_core["deliberation_outcome_quality"] = (
            self._compute_deliberation_outcome_quality(deliberation_metadata)
        )
        post_reflection = self._post_exec_reflection(
            user_id=turn.user_id,
            message=turn.message,
            response_text=response_text,
            tool_calls=tool_calls,
            tool_results=tool_results,
            decision_core=decision_core,
            blocker_verdict=blocker_verdict,
            handoff_happened=False,
        )

        duration_ms = (time.monotonic() - started) * 1000.0
        research_required = self._has_research_tool(tool_calls)
        usage_summary = self._sum_usage(provider_usages)

        # Build behavior injection trace
        memory_v2_context_injected = bool(
            memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded
        )
        memory_v2_context_item_count = (
            len(memory_v2_runtime_ctx.top_facts)
            + len(memory_v2_runtime_ctx.top_preferences)
            if memory_v2_runtime_ctx
            else 0
        )
        memory_v2_procedure_bias_applied = bool(
            memory_v2_runtime_ctx
            and memory_v2_runtime_ctx.loaded
            and memory_v2_runtime_ctx.confidence_modifier > 0.6
        )
        memory_v2_contradiction_guard_applied = bool(
            memory_v2_runtime_ctx
            and memory_v2_runtime_ctx.loaded
            and memory_v2_runtime_ctx.contradiction_alerts
            and psyche_v2_behavior_ctx
            and psyche_v2_behavior_ctx.caution_bias
            > 0.5  # Lowered from 0.6 for real triggering
        )

        psyche_v2_behavior_applied = bool(
            psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded
        )
        psyche_v2_style_mode = (
            psyche_v2_behavior_ctx.mode if psyche_v2_behavior_ctx else "neutral"
        )
        psyche_v2_pressure_applied = bool(
            psyche_v2_behavior_ctx
            and psyche_v2_behavior_ctx.loaded
            and psyche_v2_behavior_ctx.pressure > 0.5
        )
        psyche_v2_relation_tone_applied = bool(
            psyche_v2_behavior_ctx
            and psyche_v2_behavior_ctx.loaded
            and (
                psyche_v2_behavior_ctx.friction > 0.5
                or psyche_v2_behavior_ctx.warmth > 0.7
            )
        )

        final_behavior_profile = self._neutral_final_behavior_profile(
            mode=psyche_v2_style_mode
        )
        if psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded:
            final_behavior_profile = {
                "mode": psyche_v2_style_mode,
                "directness": psyche_v2_behavior_ctx.directness_bias,
                "verbosity": psyche_v2_behavior_ctx.verbosity_bias,
                "caution": psyche_v2_behavior_ctx.caution_bias,
                "pressure": psyche_v2_behavior_ctx.pressure,
                "trust": psyche_v2_behavior_ctx.trust,
                "friction": psyche_v2_behavior_ctx.friction,
                "warmth": psyche_v2_behavior_ctx.warmth,
                "autonomy": psyche_v2_behavior_ctx.autonomy_bias,
                "structuredness": psyche_v2_behavior_ctx.structuredness_bias,
                "tool_bias": psyche_v2_behavior_ctx.tool_bias,
                "web_bias": psyche_v2_behavior_ctx.web_bias,
                "reassurance": psyche_v2_behavior_ctx.reassurance_bias,
            }

        trace = {
            "provider_calls": provider_call_count,
            "tool_iterations": min(
                provider_call_count, max(1, int(CHAT_MAX_TOOL_ITERATIONS))
            ),
            "tool_calls_requested": len(tool_calls),
            "tool_calls_executed": len(tool_results),
            "tool_calls_successful": len([r for r in tool_results if r.ok]),
            "tool_failures": len([r for r in tool_results if not r.ok]),
            "used_tools": len(tool_results) > 0,
            "used_fallback": False,
            **self._correction_trace_fields(ctx),
            "anti_hallucination_clamp_applied": bool(
                anti_hallucination_trace.get("applied")
            ),
            "anti_hallucination_clamp_reason": anti_hallucination_trace.get("reason"),
            "response_grounding_mode": grounding_mode,
            "chat_thread_first_turn": first_turn_in_thread,
            "chat_history_message_count": len(turn.history or []),
            **build_history_trace(turn),
            "duration_ms": duration_ms,
            "provider": final_provider,
            "model": final_model,
            "usage_reporting_mode": usage_summary.reporting_mode,
            "usage_total_tokens": usage_summary.total_tokens,
            # ── Decision Core trace fields ──
            "selected_strategy": decision_core["selected_strategy"],
            **self._decision_core_trace_escalation(decision_core),
            "reason_codes": decision_core["reason_codes"],
            "strategy_confidence": decision_core["strategy_confidence"],
            "degraded": decision_core["strategy_degraded"],
            "memory_lookup_happened": memory_lookup_flag,
            "memory_results_count": memory_results_count_for_trace(ctx.memory_context),
            "psyche_snapshot_happened": False,
            "research_was_required": research_required,
            "agentic_executed": False,
            "tool_calls_count": len(tool_calls),
            "experience_write_back_attempted": False,
            "experience_write_back_succeeded": False,
            # ── Controlled Web Orchestration V1 ──
            "controlled_web_decision": decision_core.get("web_decision", "off"),
            "controlled_web_decision_reason": decision_core.get(
                "web_decision_reason", "not_evaluated"
            ),
            "controlled_web_triggered": bool(controlled_web.get("triggered")),
            "controlled_web_reason": controlled_web.get("reason"),
            "controlled_web_tool": controlled_web.get("tool_name"),
            "controlled_web_ok": controlled_web.get("ok"),
            "controlled_web_has_results": controlled_web.get("has_results"),
            "controlled_web_provider_info": controlled_web.get("provider_info"),
            "controlled_web_query": controlled_web.get("query"),
            "controlled_web_source_count": controlled_web.get("source_count", 0),
            "controlled_web_freshness_needed": controlled_web.get(
                "freshness_needed", False
            ),
            **self._web_stage_trace_fields(
                decision_core, controlled_web, explicit_fail_applied=False
            ),
            # Decision Core: consistency / policy / simulation / reflection
            "consistency_check_ran": decision_core["consistency_check_ran"],
            "consistency_classification": decision_core["consistency_classification"],
            # informational: count of detected contradictions, not execution-driving
            "contradictions_found": decision_core["contradictions_found"],
            "policy_hints_loaded": decision_core["policy_hints_loaded"],
            "policy_profile_name": decision_core["policy_profile_name"],
            "simulation_ran": decision_core["simulation_ran"],
            "simulation_best_action": decision_core["simulation_best_action"],
            "simulation_variants_count": decision_core["simulation_variants_count"],
            # informational: human-readable risk string, not execution-driving
            "simulation_risk_summary": decision_core["simulation_risk_summary"],
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
            "reflection_ran": post_reflection["reflection_ran"],
            "reflection_summary": post_reflection["reflection_summary"],
            "selected_goal": decision_core.get("selected_goal"),
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
            # ── Reflection hindsight fields ──
            "reflection_strategy_fit": post_reflection.get("strategy_fit", "neutral"),
            "reflection_handoff_hindsight": post_reflection.get(
                "handoff_hindsight", "na"
            ),
            "reflection_blocker_hindsight": post_reflection.get(
                "blocker_hindsight", "na"
            ),
            # ── Memory V2 + Psyche V2 + Identity Bridge (foundation) ──
            "memory_v2_loaded": memory_v2_snapshot.get("loaded", False),
            "memory_v2_match_count": memory_v2_snapshot.get("match_count", 0),
            "memory_v2_reinforced_count": memory_v2_snapshot.get("reinforced_count", 0),
            "memory_v2_suppressed_count": memory_v2_snapshot.get("suppressed_count", 0),
            "memory_v2_contradictions_count": memory_v2_snapshot.get(
                "contradictions_count", 0
            ),
            "memory_v2_actionable_contradictions_count": memory_v2_snapshot.get(
                "actionable_contradictions_count", 0
            ),
            "memory_v2_transient_contradiction_count": memory_v2_snapshot.get(
                "transient_contradiction_count", 0
            ),
            "memory_v2_stability_tier_counts": (
                dict(memory_v2_runtime_ctx.stability_tier_counts)
                if memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded
                else {}
            ),
            "memory_v2_procedure_confidence_raw": (
                memory_v2_runtime_ctx.confidence_modifier_raw
                if memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded
                else 0.0
            ),
            "self_consistency_decision": (
                psyche_v2_behavior_ctx.consistency_decision
                if psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded
                else "allow"
            ),
            "self_consistency_reasons": (
                list(psyche_v2_behavior_ctx.consistency_reasons)
                if psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded
                else []
            ),
            "memory_v2_procedures_count": memory_v2_snapshot.get("procedures_count", 0),
            "memory_v2_top_reason_codes": memory_v2_snapshot.get(
                "top_reason_codes", []
            ),
            "memory_v2_retrieval_explanation": memory_v2_snapshot.get(
                "retrieval_strategy", ""
            ),
            "psyche_v2_loaded": psyche_v2_snapshot.get("loaded", False),
            "psyche_v2_mode": psyche_v2_snapshot.get("mode", "neutral"),
            "psyche_v2_relation_trust": psyche_v2_snapshot.get("relation_trust", 0.5),
            "psyche_v2_relation_friction": psyche_v2_snapshot.get(
                "relation_friction", 0.0
            ),
            "psyche_v2_habit_biases": psyche_v2_snapshot.get("habit_biases", []),
            "psyche_v2_behavior_style": psyche_v2_snapshot.get(
                "behavior_policy", {}
            ).get("directness", 0.5),
            "identity_bridge_loaded": identity_bridge_snapshot is not None,
            # V2 Real Influence (decision impact)
            "memory_influenced_strategy": decision_core.get(
                "memory_influenced_strategy_chat", False
            ),
            "psyche_influenced_strategy": decision_core.get(
                "psyche_influenced_strategy_chat", False
            ),
            # ── V2 Behavior Injection (real runtime influence) ──
            "memory_v2_context_injected": memory_v2_context_injected,
            "memory_v2_context_item_count": memory_v2_context_item_count,
            "memory_v2_procedure_bias_applied": memory_v2_procedure_bias_applied,
            "memory_v2_contradiction_guard_applied": memory_v2_contradiction_guard_applied,
            "psyche_v2_behavior_applied": psyche_v2_behavior_applied,
            "psyche_v2_style_mode": psyche_v2_style_mode,
            "psyche_v2_pressure_applied": psyche_v2_pressure_applied,
            "psyche_v2_relation_tone_applied": psyche_v2_relation_tone_applied,
            "final_behavior_profile": final_behavior_profile,
            "reflection_confidence_hindsight": post_reflection.get(
                "confidence_hindsight", 0.0
            ),
            "reflection_risk_hindsight": post_reflection.get("risk_hindsight", 0.0),
            "reflection_deliberation_hindsight": post_reflection.get(
                "deliberation_hindsight", {}
            ),
            "attached_files": attachment_meta,
            "attachments_summary": attachments_summary,
            "blocker_verdict": blocker_verdict.model_dump(),
            # ── Response Variants Deliberation trace fields ──
            "response_variants_triggered": deliberation_metadata.get(
                "response_variants_triggered", False
            ),
            "response_variants_count": deliberation_metadata.get(
                "response_variants_count", 0
            ),
            "response_variants_reason_codes": deliberation_metadata.get(
                "response_variants_reason_codes", []
            ),
            "response_variants_winner": deliberation_metadata.get(
                "response_variants_winner"
            ),
            "response_variants_winner_type": deliberation_metadata.get(
                "response_variants_winner_type"
            ),
            "response_variants_synthesis_used": deliberation_metadata.get(
                "response_variants_synthesis_used", []
            ),
            "response_variants_dropped": deliberation_metadata.get(
                "response_variants_dropped", []
            ),
            "response_variants_confidence": deliberation_metadata.get(
                "response_variants_confidence"
            ),
            "response_variants_risk": deliberation_metadata.get(
                "response_variants_risk"
            ),
            "response_variants_summary": deliberation_metadata.get(
                "response_variants_summary"
            ),
            "response_variants_duration_ms": deliberation_metadata.get(
                "response_variants_duration_ms"
            ),
            "response_variants_scores": deliberation_metadata.get(
                "response_variants_scores", []
            ),
            "response_variants_error": deliberation_metadata.get(
                "response_variants_error", False
            ),
        }

        if memory_used_trace:
            trace["memory_used"] = memory_used_trace
        self._augment_memory_observability(trace, memory_used_trace, ctx.memory_context)

        trace_blocker_gate_outcome(trace, gate_evaluated=True, hard_applied=False)
        merge_canonical_for_llm_path(
            trace,
            decision_core=decision_core,
            grounding_mode=grounding_mode,
            memory_lookup_happened=memory_lookup_flag,
            research_was_required=research_required,
            tool_calls=tool_calls,
            web_verified_grounding_in_prompt=web_grounding_in_prompt(controlled_web),
            tool_results=tool_results,
            used_fallback=False,
            blocker_verdict_snapshot=blocker_verdict.model_dump(),
        )
        self._attach_web_observability_trace(
            trace,
            controlled_web=controlled_web,
            tool_results=tool_results,
            web_verified_in_prompt=web_grounding_in_prompt(controlled_web),
        )
        trace["memory_substantive_in_prompt"] = memory_substantive_flag
        trace["memory_stm_brief_included"] = include_stm_in_memory_brief
        trace["context_history_messages_attached"] = len(hist_for_prompt)
        trace["vault_user_message_redacted"] = vault_user_redacted
        trace.update(hist_smart_trim)
        augment_trace_context_truth(
            trace,
            mem_truth=mem_truth,
            controlled_web=controlled_web,
            decision_core=decision_core,
        )

        # ── V2 POST-RESPONSE WRITE-BACK: outcome → Memory V2 + Psyche V2 ──
        trace["memory_v2_writeback_attempted"] = False
        trace["memory_v2_writeback_succeeded"] = False
        trace["memory_v2_new_items_count"] = 0
        trace["memory_v2_new_lessons_count"] = 0
        trace["psyche_v2_writeback_attempted"] = False
        trace["psyche_v2_writeback_succeeded"] = False
        trace["psyche_v2_event_applied"] = None
        trace["response_outcome_quality"] = "success"

        if str(getattr(turn, "runtime_mode", "") or "").lower() != "audit":
            try:
                from aihub.memory_core import get_memory_core

                _psy_svc = get_psyche_core().v2_service

                # Determine outcome quality
                degraded = bool(decision_core.get("strategy_degraded", False))
                fallback = False
                if len(errors) > 0:
                    trace["response_outcome_quality"] = (
                        "blocked"
                        if any(e.get("blocker", False) for e in errors)
                        else "fallback"
                    )
                    fallback = True
                elif degraded:
                    trace["response_outcome_quality"] = "degraded"
                elif not response_text or len(response_text.strip()) < 10:
                    trace["response_outcome_quality"] = "fallback"
                    fallback = True

                # Memory V2 write-back — SAME turn_id as entire pipeline
                turn_id_for_wb = str(
                    getattr(getattr(self, "_active_turn_ctx", None), "turn_id", None)
                    or getattr(turn, "turn_id", None)
                    or uuid.uuid4()
                )
                memory_wb = get_memory_core().record_chat_outcome(
                    user_id=turn.user_id,
                    turn_id=turn_id_for_wb,
                    query_text=turn.message or "",
                    response_text=response_text,
                    strategy=decision_core["selected_strategy"],
                    grounding_mode=grounding_mode,
                    tool_calls_count=len(tool_calls),
                    tool_successes=len([r for r in tool_results if r.ok]),
                    tool_failures=len([r for r in tool_results if not r.ok]),
                    contradictions_present=memory_v2_snapshot.get(
                        "contradictions_count", 0
                    ),
                    memory_matches=memory_v2_snapshot.get("match_count", 0),
                    degraded=degraded,
                    fallback=fallback,
                )
                trace["memory_v2_writeback_attempted"] = memory_wb.get(
                    "attempted", False
                )
                trace["memory_v2_writeback_succeeded"] = memory_wb.get(
                    "succeeded", False
                )
                trace["memory_v2_new_items_count"] = memory_wb.get("new_items_count", 0)
                trace["memory_v2_new_lessons_count"] = memory_wb.get(
                    "new_lessons_count", 0
                )

                # Psyche V2 write-back
                outcome_kind = trace["response_outcome_quality"]
                psyche_wb = _psy_svc.apply_outcome_event(
                    user_id=turn.user_id,
                    outcome_kind=(
                        outcome_kind if outcome_kind != "blocked" else "failure"
                    ),
                    source_ref=turn_id_for_wb,
                    context={
                        "contradictions_present": memory_v2_snapshot.get(
                            "contradictions_count", 0
                        ),
                        "grounding_mode": grounding_mode,
                        "tool_calls_count": len(tool_calls),
                    },
                )
                trace["psyche_v2_writeback_attempted"] = psyche_wb.get(
                    "attempted", False
                )
                trace["psyche_v2_writeback_succeeded"] = psyche_wb.get(
                    "succeeded", False
                )
                trace["psyche_v2_event_applied"] = psyche_wb.get("event_applied")

                logger.info(
                    f"V2 chat write-back: memory={memory_wb.get('succeeded')} psyche={psyche_wb.get('succeeded')} user={turn.user_id}"
                )

            except Exception as v2_wb_error:
                logger.warning(
                    f"V2 chat write-back failed: {v2_wb_error}", exc_info=True
                )
                trace["memory_v2_writeback_attempted"] = True
                trace["psyche_v2_writeback_attempted"] = True

        self._write_back_experience(
            turn=turn,
            response_text=response_text,
            grounding_mode=grounding_mode,
            tool_calls=tool_calls,
            tool_results=tool_results,
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

        _cr_hook("append_event", append_event)(
            turn.user_id,
            "chat.turn",
            {
                "ok": len(errors) == 0,
                "provider": final_provider,
                "model": final_model,
                "trace": trace,
                "tool_calls": [tc.model_dump() for tc in tool_calls],
                "tool_results": [tr.model_dump() for tr in tool_results],
            },
        )

        result = ChatTurnResult(
            ok=len(errors) == 0,
            response_text=response_text,
            model=final_model,
            provider=final_provider,
            tool_calls=tool_calls,
            tool_results=tool_results,
            selected_mode=ctx.mode,
            usage=self._sum_usage(provider_usages),
            trace=trace,
            errors=errors,
            debug={"context": ctx.model_dump()} if turn.include_debug else None,
            attachments_summary=attachments_summary,
        )
        _TRACE_CACHE[turn.user_id].append(result.trace)

        return result

    async def _run_turn_core(self, turn: ChatTurnInput) -> ChatTurnResult:
        """Backward-compatible alias for tests."""
        return await self.run_turn_core(turn)





# Late-bind TurnOps into mixin modules (staticmethod bodies reference TurnOps).
import aihub.turn.mixins.decision as _m_decision
import aihub.turn.mixins.execution as _m_execution
import aihub.turn.mixins.experience as _m_experience
import aihub.turn.mixins.prompt_context as _m_prompt
import aihub.turn.mixins.web as _m_web

for _m in (_m_decision, _m_execution, _m_experience, _m_prompt, _m_web):
    _m.TurnOps = TurnOps
TurnOps.__module__ = "aihub.turn.ops"

_RUNTIME: TurnOps | None = None


def get_turn_ops() -> TurnOps:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = TurnOps()
    else:
        try:
            fresh_provider = get_default_provider()
        except Exception:
            fresh_provider = None
        if fresh_provider is not None:
            current = getattr(_RUNTIME, "_provider", None)
            current_key = (type(current), getattr(current, "provider_name", None), getattr(current, "name", None))
            fresh_key = (type(fresh_provider), getattr(fresh_provider, "provider_name", None), getattr(fresh_provider, "name", None))
            if current is None or current_key != fresh_key:
                _RUNTIME._provider = fresh_provider
    return _RUNTIME


def get_cached_chat_traces(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    traces = list(_TRACE_CACHE.get(user_id, []))
    return traces[-max(1, int(limit)) :]


def get_last_traces(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    return get_cached_chat_traces(user_id, limit=limit)