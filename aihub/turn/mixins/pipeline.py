"""Pipeline stages for TurnOps.run_turn_core (shared locals in g)."""
from __future__ import annotations

from typing import Any

import aihub.turn._ops_ns as _ops_ns
globals().update({k: v for k, v in vars(_ops_ns).items() if not k.startswith('__')})
TurnOps = None  # late-bound by aihub.turn.ops

from aihub.chat_contracts import ChatTurnInput, ChatTurnResult
from aihub.turn.errors import RuntimeInternalError

class PipelineMixin:
    async def run_turn_core(self, turn: ChatTurnInput) -> ChatTurnResult:
        g: dict[str, Any] = {"turn": turn}
        self._turn_max_completion_tokens = getattr(turn, "max_completion_tokens", None)
        await self._stage_prepare(g)
        if "__result__" in g:
            return g["__result__"]
        await self._stage_decision_blocker(g)
        if "__result__" in g:
            return g["__result__"]
        await self._stage_handoff(g)
        if "__result__" in g:
            return g["__result__"]
        await self._stage_web_setup(g)
        if "__result__" in g:
            return g["__result__"]
        await self._stage_provider_loop(g)
        if "__result__" in g:
            return g["__result__"]
        await self._stage_provider_post(g)
        if "__result__" in g:
            return g["__result__"]
        await self._stage_shape_deliberation(g)
        if "__result__" in g:
            return g["__result__"]
        await self._stage_build_success_trace(g)
        if "__result__" in g:
            return g["__result__"]
        await self._stage_writeback(g)
        if "__result__" in g:
            return g["__result__"]
        raise RuntimeInternalError(
            code="pipeline_no_result",
            category="internal",
            retryable=False,
            user_safe_message="Wewnętrzny błąd pipeline tury.",
            internal_detail="turn pipeline produced no result",
        )

    async def _stage_prepare(self, g):
        g['started'] = time.monotonic()
        g['correction_turn_trace'] = record_user_correction_turn(g['turn'])
        from aihub.chat_deterministic import try_deterministic_turn, try_memory_fact_read_turn
        g['det'] = try_deterministic_turn(g['turn'], started_monotonic=g['started'])
        if g['det'] is not None:
            try:
                from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context
                g['det'].trace.update(self._final_behavior_trace_fields(build_psyche_v2_behavior_context(g['turn'].user_id)))
            except Exception as exc:
                g['exc'] = exc
                logger.debug('deterministic trace: psyche behavior fields skipped: %s', exc)
                g['det'].trace.update(self._final_behavior_trace_fields(None))
            g['det'].trace.update(self._correction_trace_flat(g['correction_turn_trace'], hints_chars=0))
            _cr_hook('append_event', append_event)(g['turn'].user_id, 'chat.turn', {'ok': True, 'provider': g['det'].provider, 'model': g['det'].model, 'trace': g['det'].trace, 'tool_calls': [], 'tool_results': []})
            _TRACE_CACHE[g['turn'].user_id].append(g['det'].trace)
            g['__result__'] = g['det']
            return
        g['ctx'] = self._build_context(g['turn'], correction_turn_trace=g['correction_turn_trace'])
        g['mem_fact'] = try_memory_fact_read_turn(g['turn'], g['ctx'].memory_context, started_monotonic=g['started'])
        if g['mem_fact'] is not None:
            try:
                from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context
                g['mem_fact'].trace.update(self._final_behavior_trace_fields(build_psyche_v2_behavior_context(g['turn'].user_id)))
            except Exception as exc:
                g['exc'] = exc
                logger.debug('memory_fact trace: psyche behavior fields skipped: %s', exc)
                g['mem_fact'].trace.update(self._final_behavior_trace_fields(None))
            g['mem_fact'].trace.update(self._correction_trace_flat(g['correction_turn_trace'], hints_chars=0))
            _cr_hook('append_event', append_event)(g['turn'].user_id, 'chat.turn', {'ok': True, 'provider': g['mem_fact'].provider, 'model': g['mem_fact'].model, 'trace': g['mem_fact'].trace, 'tool_calls': [], 'tool_results': []})
            _TRACE_CACHE[g['turn'].user_id].append(g['mem_fact'].trace)
            g['__result__'] = g['mem_fact']
            return
        g['psyche_snapshot'] = copy.deepcopy(get_psyche_core().ensure_user(g['turn'].user_id) or {})
        g['memory_v2_snapshot']: dict[str, Any] = {}
        g['psyche_v2_snapshot']: dict[str, Any] = {}
        g['identity_bridge_snapshot'] = None
        g['memory_v2_runtime_ctx'] = None
        g['psyche_v2_behavior_ctx'] = None
        try:
            from aihub.strategy_selector import (
                is_assistant_meta_ask,
                meta_ask_refers_to_prior_conversation,
            )

            g['assistant_meta_ask'] = is_assistant_meta_ask(g['turn'].message or '')
            g['assistant_meta_ask_pure'] = bool(
                g['assistant_meta_ask']
                and not meta_ask_refers_to_prior_conversation(g['turn'].message or '')
            )
        except Exception:
            g['assistant_meta_ask'] = False
            g['assistant_meta_ask_pure'] = False
        if g.get('assistant_meta_ask'):
            # Cap completion budget for identity/meta asks.
            cur = self._turn_max_completion_tokens
            try:
                cur_i = int(cur) if cur is not None else 256
            except (TypeError, ValueError):
                cur_i = 256
            self._turn_max_completion_tokens = max(160, min(cur_i, 256))
        if g.get('assistant_meta_ask'):
            # Skip heavy memory V2 / identity retrieval; keep light psyche for tone.
            g['memory_v2_snapshot'] = {
                'loaded': False,
                'match_count': 0,
                'reinforced_count': 0,
                'suppressed_count': 0,
                'contradictions_count': 0,
                'actionable_contradictions_count': 0,
                'transient_contradiction_count': 0,
                'procedures_count': 0,
                'top_reason_codes': ['META_ASK_HEAVY_STAGES_SKIPPED'],
                'retrieval_strategy': 'skipped_meta_ask',
            }
            g['memory_v2_runtime_ctx'] = None
            g['identity_bridge_snapshot'] = None
            try:
                from aihub.runtime_psyche_bridge import (
                    build_psyche_v2_behavior_context,
                    build_psyche_v2_runtime_snapshot,
                )

                g['psyche_v2_snapshot'] = build_psyche_v2_runtime_snapshot(g['turn'].user_id)
                g['psyche_v2_behavior_ctx'] = build_psyche_v2_behavior_context(g['turn'].user_id)
            except Exception as bridge_error:
                g['bridge_error'] = bridge_error
                logger.debug('meta ask light psyche skipped: %s', bridge_error)
            if isinstance(g['ctx'].system_context, dict):
                g['ctx'].system_context['META_ASK_HEAVY_STAGES_SKIPPED'] = True
                g['ctx'].system_context['identity_bridge_snapshot'] = None
        else:
            try:
                from aihub.runtime_identity_bridge import build_identity_bridge_snapshot as build_identity_snapshot
                from aihub.runtime_memory_bridge import build_memory_v2_runtime_context, build_memory_v2_runtime_snapshot
                from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context, build_psyche_v2_runtime_snapshot
                g['memory_v2_snapshot'] = build_memory_v2_runtime_snapshot(g['turn'].user_id, g['turn'].message)
                g['psyche_v2_snapshot'] = build_psyche_v2_runtime_snapshot(g['turn'].user_id)
                g['identity_bridge_snapshot'] = build_identity_snapshot(g['turn'].user_id, g['turn'].message)
                g['memory_v2_runtime_ctx'] = build_memory_v2_runtime_context(g['turn'].user_id, g['turn'].message)
                g['psyche_v2_behavior_ctx'] = build_psyche_v2_behavior_context(g['turn'].user_id)
                if isinstance(g['ctx'].system_context, dict):
                    g['ctx'].system_context['identity_bridge_snapshot'] = g['identity_bridge_snapshot']
            except Exception as bridge_error:
                g['bridge_error'] = bridge_error
                logger.warning(f'Failed to load V2 bridges: {bridge_error}')
            try:
                if g['memory_v2_runtime_ctx'] is not None or g['psyche_v2_behavior_ctx'] is not None:
                    from aihub.psyche_v2_repository import ensure_psyche_profile
                    from aihub.runtime_psyche_bridge import apply_consistency_to_contexts
                    g['_prof'] = ensure_psyche_profile(g['turn'].user_id)
                    g['memory_v2_runtime_ctx'], g['psyche_v2_behavior_ctx'], g['_consistency'] = apply_consistency_to_contexts(g['memory_v2_runtime_ctx'], g['psyche_v2_behavior_ctx'], g['_prof'].core_caution)
            except Exception as consistency_error:
                g['consistency_error'] = consistency_error
                logger.debug('Self-consistency pass skipped: %s', consistency_error, exc_info=True)
        g['mem_truth'] = memory_truth_for_prompt(g['ctx'].memory_context)
        g['memory_lookup_flag'] = bool(g['mem_truth']['memory_retrieval_has_rows']) and not g.get('assistant_meta_ask_pure')
        g['memory_substantive_flag'] = bool(g['mem_truth']['memory_substantive_in_prompt']) and not g.get('assistant_meta_ask_pure')
        if g.get('assistant_meta_ask_pure'):
            g['memory_lookup_flag'] = False
            g['memory_substantive_flag'] = False
            g['memory_brief'] = ''
            g['memory_used_trace'] = []
            g['include_stm_in_memory_brief'] = False
            if isinstance(g['ctx'].system_context, dict):
                g['ctx'].system_context['memory_lookup_happened'] = False
                g['ctx'].system_context['memory_results_count'] = 0
        else:
            g['include_stm_in_memory_brief'] = len(g['turn'].history or []) == 0
            g['memory_brief'] = self._build_memory_brief(
                g['ctx'].memory_context,
                include_stm=g['include_stm_in_memory_brief'],
                correction_hints=str((g['ctx'].system_context or {}).get('correction_hints_text') or ''),
            )
            g['memory_used_trace'] = self._build_memory_used_trace(
                g['ctx'].memory_context,
                include_stm=g['include_stm_in_memory_brief'],
                correction_hints=str((g['ctx'].system_context or {}).get('correction_hints_text') or ''),
            )
        if stream_session_active():
            await emit_status('thinking', label_pl='Analizuję…')
            if not g.get('assistant_meta_ask_pure'):
                await emit_status('memory', label_pl='Sprawdzam kontekst…')
            g['mem_total'] = memory_results_count_for_trace(g['ctx'].memory_context) if not g.get('assistant_meta_ask_pure') else 0
            if g['memory_lookup_flag'] and g['mem_total'] > 0:
                await emit_memory_used(count=g['mem_total'])
        g['psyche_brief'] = self._build_psyche_brief(g['psyche_snapshot'])
        if isinstance(g['ctx'].system_context, dict):
            g['ctx'].system_context['memory_brief'] = g['memory_brief']
            g['ctx'].system_context['psyche_brief'] = g['psyche_brief']
        g['tools'] = self._build_provider_tools(g['ctx'])
        g['tool_results']: list[ToolCallResult] = []
        g['tool_calls']: list[ToolCallRequest] = []
        g['provider_usages']: list[ProviderUsage] = []
        g['errors']: list[dict[str, Any]] = []
        g['controlled_web']: dict[str, Any] = {'triggered': False, 'reason': 'not_required', 'tool_name': None, 'ok': None, 'has_results': None, 'provider_info': None, 'query': None, 'source_count': 0, 'freshness_needed': False}
        # Conversation pragmatics — before strategy selection
        try:
            from aihub.turn.pragmatics import analyze_pragmatics
            g['_active_tid'] = str(getattr(getattr(self, '_active_turn_ctx', None), 'turn_id', None) or getattr(g['turn'], 'turn_id', '') or '')
            g['pragmatics'] = analyze_pragmatics(
                raw_text=g['turn'].message or '',
                history=list(g['turn'].history or [])[:2] if g.get('assistant_meta_ask') else list(g['turn'].history or []),
                user_id=g['turn'].user_id,
                session_id=g['turn'].session_id,
                turn_id=g['_active_tid'],
                memory_brief=g.get('memory_brief') or '',
                psyche_brief=g.get('psyche_brief') or '',
            )
            g['ctx'].system_context['pragmatics'] = g['pragmatics'].model_dump()
            g['ctx'].system_context['pragmatics_obj'] = g['pragmatics']
            g['ctx'].system_context['pragmatics_response_mode'] = g['pragmatics'].response_mode
            if g['pragmatics'].needs_recent_history:
                g['pragmatics'].history_injected = True
                g['decision_core_pragmatics_history'] = True
        except Exception as pragmatics_error:
            g['pragmatics_error'] = pragmatics_error
            logger.warning('pragmatics analysis failed: %s', pragmatics_error, exc_info=True)
            g['pragmatics'] = None
            g['ctx'].system_context['pragmatics'] = {'degraded': True, 'reason_codes': ['PRAGMATICS_DEGRADED_FALLBACK']}
        # Cognitive Integration V2 — conversation state + user model + cross-module pack
        try:
            from aihub.turn.cognitive_integration import build_cognitive_influence_pack
            g['_corr_hints'] = str((g['ctx'].system_context or {}).get('correction_hints_text') or '')
            g['_sel_goal'] = None
            g['cognitive'] = build_cognitive_influence_pack(
                user_id=g['turn'].user_id,
                session_id=g['turn'].session_id,
                message=g['turn'].message or '',
                history=list(g['turn'].history or [])[:2] if g.get('assistant_meta_ask') else list(g['turn'].history or []),
                pragmatics=g.get('pragmatics'),
                memory_brief=g.get('memory_brief') or '',
                psyche_brief=g.get('psyche_brief') or '',
                memory_v2_ctx=None if g.get('assistant_meta_ask') else g.get('memory_v2_runtime_ctx'),
                psyche_v2_ctx=g.get('psyche_v2_behavior_ctx'),
                identity_snapshot=None if g.get('assistant_meta_ask') else g.get('identity_bridge_snapshot'),
                selected_goal=None,
                experience_signal_summary='',
                correction_hints=g['_corr_hints'],
                reflection_summary='',
            )
            g['ctx'].system_context['cognitive'] = g['cognitive'].model_dump()
            g['ctx'].system_context['cognitive_obj'] = g['cognitive']
        except Exception as cognitive_error:
            g['cognitive_error'] = cognitive_error
            logger.warning('cognitive integration failed: %s', cognitive_error, exc_info=True)
            g['cognitive'] = None
            g['ctx'].system_context['cognitive'] = {'degraded': True, 'influence_reason_codes': ['COG_DEGRADED_FALLBACK']}

    async def _stage_decision_blocker(self, g):
        g['decision_core'] = self._pre_exec_decision_core(turn=g['turn'], ctx=g['ctx'], psyche_snapshot=g['psyche_snapshot'], memory_v2_runtime_ctx=g['memory_v2_runtime_ctx'], psyche_v2_behavior_ctx=g['psyche_v2_behavior_ctx'])
        g['tools'] = self._apply_strategy_to_tools(g['tools'], g['decision_core']['selected_strategy'], tool_order_hint=list(g['decision_core'].get('tool_order_hint') or []))
        if not g['decision_core'].get('escalation_use_tools'):
            g['tools'] = []
        g['blocker_verdict'] = self._evaluate_blocker_verdict(g['decision_core'])
        if g['blocker_verdict'].hard:
            g['duration_ms'] = (time.monotonic() - g['started']) * 1000.0
            g['blocker_trace'] = {'provider_calls': 0, 'tool_iterations': 0, 'used_tools': False, 'used_fallback': False, 'response_grounding_mode': 'blocker_hard_gate', 'duration_ms': g['duration_ms'], **self._correction_trace_fields(g['ctx']), 'selected_strategy': g['decision_core']['selected_strategy'], **self._decision_core_trace_escalation(g['decision_core']), 'reason_codes': g['decision_core']['reason_codes'] + ['BLOCKER_HARD_GATE'], 'strategy_confidence': g['decision_core']['strategy_confidence'], 'degraded': g['decision_core'].get('strategy_degraded', False), 'memory_lookup_happened': g['memory_lookup_flag'], 'psyche_snapshot_happened': bool(g['psyche_snapshot']), 'research_was_required': False, 'agentic_executed': False, 'tool_calls_count': 0, 'experience_write_back_attempted': False, 'experience_write_back_succeeded': False, 'blocker_verdict': g['blocker_verdict'].model_dump(), 'controlled_web_decision': g['decision_core'].get('web_decision', 'off'), 'controlled_web_decision_reason': g['decision_core'].get('web_decision_reason', 'not_evaluated'), 'controlled_web_triggered': False, 'controlled_web_reason': 'blocker_hard_gate', 'controlled_web_tool': None, 'controlled_web_ok': None, 'controlled_web_has_results': None, 'controlled_web_provider_info': None, 'controlled_web_query': None, 'controlled_web_source_count': 0, 'controlled_web_freshness_needed': self._is_freshness_needed(g['decision_core'].get('reason_codes', [])), 'experience_lookup_happened': g['decision_core'].get('experience_lookup_happened', False), 'experience_matches_count': g['decision_core'].get('experience_matches_count', 0), 'experience_influenced_strategy': g['decision_core'].get('experience_influenced_strategy', False), 'experience_blocker_reason': g['decision_core'].get('experience_blocker_reason'), 'experience_signal_summary': g['decision_core'].get('experience_signal_summary'), 'consistency_check_ran': g['decision_core']['consistency_check_ran'], 'consistency_classification': g['decision_core']['consistency_classification'], 'contradictions_found': g['decision_core']['contradictions_found'], 'simulation_ran': g['decision_core']['simulation_ran'], 'simulation_best_action': g['decision_core']['simulation_best_action'], 'selected_goal': g['decision_core'].get('selected_goal'), 'policy_feedback_loaded': bool(g['decision_core'].get('policy_feedback_loaded')), 'policy_feedback_applied': bool(g['decision_core'].get('policy_feedback_applied')), 'policy_feedback_summary': g['decision_core'].get('policy_feedback_summary', ''), 'policy_confidence_delta': g['decision_core'].get('policy_confidence_delta', 0.0), 'policy_handoff_bias': g['decision_core'].get('policy_handoff_bias', 0.0), 'policy_blocker_sensitivity': g['decision_core'].get('policy_blocker_sensitivity', 0.0), 'policy_simulation_risk_cal': g['decision_core'].get('policy_simulation_risk_cal', 0.0), 'policy_strategy_adjustments': g['decision_core'].get('policy_strategy_adjustments', {})}
            if g['memory_used_trace']:
                g['blocker_trace']['memory_used'] = g['memory_used_trace']
            trace_blocker_gate_outcome(g['blocker_trace'], gate_evaluated=True, hard_applied=True)
            g['blocker_trace']['chat_handoff_evaluated'] = False
            g['_bt'] = g['blocker_verdict'].blocker_type
            g['_bsrc'] = g['blocker_verdict'].source or 'unknown'
            merge_canonical_decision_trace(g['blocker_trace'], selected_route=ROUTE_BLOCKED_HARD, route_reason=f"blocker_hard_gate|type={g['_bt']}|source={g['_bsrc']}|resolution={g['blocker_verdict'].resolution}", decision_intent='blocked', deterministic_hit=False, vault_used=False, memory_retrieval_used=bool(g['memory_lookup_flag']), web_required=str(g['decision_core'].get('web_decision') or 'off') == 'required', planner_used=False, blocker_hard=True)
            g['blocker_trace']['memory_substantive_in_prompt'] = g['memory_substantive_flag']
            g['blocker_trace']['memory_stm_brief_included'] = g['include_stm_in_memory_brief']
            augment_trace_context_truth(g['blocker_trace'], mem_truth=memory_truth_for_prompt(g['ctx'].memory_context), controlled_web={'triggered': False, 'ok': None, 'has_results': None}, decision_core=g['decision_core'], force_no_web_verified=True)
            self._run_runtime_experience_feedback(g['turn'].user_id, g['blocker_trace'])
            _cr_hook('append_event', append_event)(g['turn'].user_id, 'chat.turn.blocked', {'ok': False, 'blocker_type': g['blocker_verdict'].blocker_type, 'blocker_reason': g['blocker_verdict'].reason, 'blocker_source': g['blocker_verdict'].source, 'blocker_resolution': g['blocker_verdict'].resolution, 'user_message': g['blocker_verdict'].user_message, 'trace': g['blocker_trace']})
            g['result'] = ChatTurnResult(ok=False, response_text=g['blocker_verdict'].user_message or g['blocker_verdict'].reason, model='blocker_gate', provider='decision_core', tool_calls=[], tool_results=[], selected_mode=g['ctx'].mode, usage=ProviderUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0), trace=g['blocker_trace'], errors=[{'type': 'blocker_hard_gate', 'blocker_type': g['blocker_verdict'].blocker_type, 'reason': g['blocker_verdict'].reason, 'source': g['blocker_verdict'].source, 'recommended_action': g['blocker_verdict'].recommended_action, 'resolution': g['blocker_verdict'].resolution, 'user_message': g['blocker_verdict'].user_message, 'dev_message': g['blocker_verdict'].dev_message}])
            _TRACE_CACHE[g['turn'].user_id].append(g['result'].trace)
            g['__result__'] = g['result']
            return
        if g['blocker_verdict'].blocker_active and g['blocker_verdict'].resolution in ('downgrade', 'reroute'):
            g['new_strategy'] = g['blocker_verdict'].next_best_action or 'contextual'
            g['old_strategy'] = g['decision_core']['selected_strategy']
            if g['new_strategy'] != g['old_strategy']:
                g['decision_core']['selected_strategy'] = g['new_strategy']
                g['decision_core']['reason_codes'].append(f"BLOCKER_{g['blocker_verdict'].resolution.upper()}_{g['old_strategy'].upper()}_TO_{g['new_strategy'].upper()}")
                logger.info('Blocker %s: strategy %s→%s for user=%s (type=%s)', g['blocker_verdict'].resolution, g['old_strategy'], g['new_strategy'], g['turn'].user_id, g['blocker_verdict'].blocker_type)
                self._finalize_escalation(g['decision_core'])
                g['tools'] = self._apply_strategy_to_tools(self._build_provider_tools(g['ctx']), g['decision_core']['selected_strategy'], tool_order_hint=list(g['decision_core'].get('tool_order_hint') or []))
                if not g['decision_core'].get('escalation_use_tools'):
                    g['tools'] = []

    async def _stage_handoff(self, g):
        g['should_handoff'], g['handoff_reason'] = self._should_handoff_to_agent(decision_core=g['decision_core'], message=g['turn'].message)
        g['decision_core']['chat_handoff_evaluated'] = True
        if g['should_handoff']:
            g['decision_core'].pop('chat_handoff_executed', None)
            g['decision_core'].pop('chat_handoff_skip_reason', None)
        else:
            g['decision_core']['chat_handoff_executed'] = False
            g['decision_core']['chat_handoff_skip_reason'] = g['handoff_reason']
        if g['should_handoff']:
            if stream_session_active():
                await emit_status('tools', label_pl='Wykonuję kroki…')
            g['__result__'] = await self._execute_agent_handoff(turn=g['turn'], decision_core=g['decision_core'], handoff_reason=g['handoff_reason'], started=g['started'], psyche_snapshot=g['psyche_snapshot'], memory_used_trace=g['memory_used_trace'], memory_lookup_flag=g['memory_lookup_flag'], blocker_verdict=g['blocker_verdict'], memory_context=g['ctx'].memory_context, ctx=g['ctx'])
            return

    async def _stage_web_setup(self, g):
        g['web_prefetch'] = await self._run_controlled_web_prefetch(turn=g['turn'], ctx=g['ctx'], web_decision=g['decision_core'].get('web_decision', 'off'))
        if g['web_prefetch'].get('triggered'):
            g['call_obj'] = g['web_prefetch'].get('tool_call')
            g['result_obj'] = g['web_prefetch'].get('tool_result')
            if isinstance(g['call_obj'], ToolCallRequest):
                g['tool_calls'].append(g['call_obj'])
            if isinstance(g['result_obj'], ToolCallResult):
                g['tool_results'].append(g['result_obj'])
                if not g['result_obj'].ok:
                    g['errors'].append({'type': 'controlled_web_error', 'error': g['result_obj'].error or 'unknown', 'tool': g['web_prefetch'].get('tool_name')})
            g['controlled_web'] = {'triggered': True, 'reason': g['web_prefetch'].get('reason'), 'tool_name': g['web_prefetch'].get('tool_name'), 'ok': g['result_obj'].ok if isinstance(g['result_obj'], ToolCallResult) else None, 'has_results': self._assess_web_result_quality(g['result_obj']), 'provider_info': self._extract_web_provider_info(g['result_obj']), 'query': self._extract_web_query(g['call_obj'] if isinstance(g['call_obj'], ToolCallRequest) else None), 'source_count': self._count_web_sources(g['result_obj'] if isinstance(g['result_obj'], ToolCallResult) else None), 'freshness_needed': self._is_freshness_needed(g['decision_core'].get('reason_codes', []))}
        g['pre_messages'] = g['web_prefetch'].get('messages') or []
        from aihub.chat_attachment_vision import enrich_image_attachments_for_turn
        g['effective_attached_ids'] = self._effective_attached_file_ids(g['turn'])
        await enrich_image_attachments_for_turn(user_id=g['turn'].user_id, session_id=g['turn'].session_id, file_ids=list(g['effective_attached_ids']))
        g['attachment_block'], g['attachment_meta'] = build_attachment_prompt_block(user_id=g['turn'].user_id, session_id=g['turn'].session_id, file_ids=list(g['effective_attached_ids']))
        g['attachments_summary'] = summarize_attachments_for_user(g['attachment_meta'])
        g['first_turn_in_thread'] = len(g['turn'].history or []) == 0
        g['history_rollup'], g['hist_for_prompt'] = smart_clip_chat_history(g['turn'].history)
        g['hist_smart_trim'] = {'chat_history_smart_trim_applied': bool(g['history_rollup']), 'chat_history_raw_tail_kept': len(g['hist_for_prompt']), 'chat_history_rollup_chars': len(g['history_rollup'] or '')}
        g['user_llm_text'], g['vault_user_redacted'] = sanitize_user_message_for_llm(g['turn'].message)
        if g['effective_attached_ids'] and int(g['attachment_meta'].get('attachments_usable_count') or 0) == 0:
            g['user_llm_text'] = '[Priorytet: załączniki nie dostarczyły czytelnej treści do modelu. Odpowiedz krótko, co poszło nie tak (per plik), bez zgadywania treści ani formuł w stylu „może chodziło o…”.]\n\n' + g['user_llm_text']
        if self._web_required_grounding_unsatisfied(g['decision_core'], g['controlled_web']):
            g['__result__'] = await self._finish_turn_web_required_ungrounded(turn=g['turn'], ctx=g['ctx'], started=g['started'], decision_core=g['decision_core'], blocker_verdict=g['blocker_verdict'], controlled_web=g['controlled_web'], tool_calls=g['tool_calls'], tool_results=g['tool_results'], errors=list(g['errors']), memory_lookup_flag=g['memory_lookup_flag'], memory_used_trace=g['memory_used_trace'], include_stm_in_memory_brief=g['include_stm_in_memory_brief'], psyche_snapshot=g['psyche_snapshot'], attachment_meta=g['attachment_meta'], attachments_summary=g['attachments_summary'], hist_for_prompt_len=len(g['hist_for_prompt']), vault_user_redacted=g['vault_user_redacted'], hist_smart_trim=g['hist_smart_trim'])
            return
        g['messages']: list[ChatMessage] = [ChatMessage(role='system', content=self._build_system_prompt(g['ctx'], memory_brief=g['memory_brief'], psyche_brief=g['psyche_brief'], decision_hints=g['decision_core']['strategy_hints'], correction_hints=str(g['ctx'].system_context.get('correction_hints_text') or ''), memory_v2_context=g['memory_v2_runtime_ctx'], psyche_v2_context=g['psyche_v2_behavior_ctx'], files_context=g['attachment_block'], first_turn_in_thread=g['first_turn_in_thread'], history_rollup=g['history_rollup'], listing_sales_boost=listing_copy_no_web_intent(g['turn'].message))), *g['hist_for_prompt'], *g['pre_messages'], ChatMessage(role='user', content=g['user_llm_text'])]
        g['response_text'] = ''
        g['final_model'] = LLM_MODEL_NAME
        g['final_provider'] = self._current_provider_name()
        g['provider_call_count'] = 0
        g['usage_summary'] = self._sum_usage(g['provider_usages'])

    async def _stage_provider_loop(self, g):
        for g['iteration'] in range(max(1, int(CHAT_MAX_TOOL_ITERATIONS)) + 1):
            g['provider_call_count'] += 1
            if stream_session_active():
                await emit_status('thinking', label_pl='Składam odpowiedź…')
            try:
                g['model_response'] = await self._provider_call(messages=g['messages'], tools=g['tools'])
            except (ProviderError, Exception) as exc:
                g['exc'] = exc
                from aihub.turn.errors import ProviderExecutionError as _PEE
                if not isinstance(exc, (ProviderError, _PEE)):
                    raise
                g['err_payload'] = exc.to_dict() if isinstance(exc, ProviderError) else {'message': str(exc), 'code': getattr(exc.info, 'code', 'provider_error')}
                g['errors'].append({'type': 'provider_error', **g['err_payload']})
                g['fallback_text'], g['fallback_trace'] = await self._provider_failure_fallback(g['turn'], reason=str(getattr(exc, 'message', None) or exc), decision_core=g['decision_core'])
                if str(g['decision_core'].get('web_decision') or 'off') == 'required' and (not llm_path_verified_research_grounding(web_grounding_in_prompt(g['controlled_web']), g['tool_results'])):
                    g['fallback_text'] = self._web_required_ungrounded_user_message(outcome=self._classify_web_required_failure(g['controlled_web'])[0], controlled_web=g['controlled_web'], errors=g['errors'])
                if g['turn'].include_debug:
                    if g['memory_lookup_flag']:
                        g['fallback_text'] += f"\n\n[Kontekst pamięci] {g['memory_brief'][:900]}"
                    if g['psyche_brief'] != 'BRAK DANYCH':
                        g['fallback_text'] += f"\n[Kontekst psyche] {g['psyche_brief']}"
                g['web_any'] = next((g['result'] for g['result'] in g['tool_results'] if any((g['k'] in (g['result'].name or '').lower() for g['k'] in ('web', 'research')))), None)
                if g['web_any'] is not None:
                    g['web_payload'] = self._safe_preview(g['web_any'].output, max_chars=700) if g['web_any'].ok else f"błąd wykonania: {g['web_any'].error or 'BRAK DANYCH'}"
                    g['fallback_text'] += f"\n\n[Controlled web] {g['web_payload']}"
                g['fallback_reflection'] = self._post_exec_reflection(user_id=g['turn'].user_id, message=g['turn'].message, response_text=g['fallback_text'], tool_calls=g['tool_calls'], tool_results=g['tool_results'], decision_core=g['decision_core'], blocker_verdict=g['blocker_verdict'], handoff_happened=False)
                g['duration_ms'] = (time.monotonic() - g['started']) * 1000.0
                g['usage_summary'] = self._sum_usage(g['provider_usages'])
                g['_provider_trace'] = {}
                merge_provider_trace_from_builder(g['_provider_trace'], getattr(self, '_active_trace_builder', None))
                g['_fb_provider'] = g['_provider_trace'].get('provider_selected_final') or self._current_provider_name()
                g['_fb_model'] = g['_provider_trace'].get('provider_final_model') or LLM_MODEL_NAME
                g['trace'] = {'provider_calls': g['provider_call_count'], 'tool_iterations': g['iteration'], 'fallback': g['fallback_trace'], 'used_tools': len(g['tool_results']) > 0, 'used_fallback': True, 'response_grounding_mode': 'fallback', 'duration_ms': g['duration_ms'], **self._correction_trace_fields(g['ctx']), 'provider': g['_fb_provider'], 'model': g['_fb_model'], 'usage_reporting_mode': g['usage_summary'].reporting_mode, 'usage_total_tokens': g['usage_summary'].total_tokens, 'selected_strategy': g['decision_core']['selected_strategy'], **self._decision_core_trace_escalation(g['decision_core']), 'reason_codes': g['decision_core']['reason_codes'], 'strategy_confidence': g['decision_core']['strategy_confidence'], 'degraded': g['decision_core']['strategy_degraded'], 'memory_lookup_happened': g['memory_lookup_flag'], 'memory_results_count': memory_results_count_for_trace(g['ctx'].memory_context), 'psyche_snapshot_happened': False, 'research_was_required': self._has_research_tool(g['tool_calls']), 'agentic_executed': False, 'tool_calls_count': len(g['tool_calls']), 'experience_write_back_attempted': False, 'experience_write_back_succeeded': False, 'controlled_web_decision': g['decision_core'].get('web_decision', 'off'), 'controlled_web_decision_reason': g['decision_core'].get('web_decision_reason', 'not_evaluated'), 'controlled_web_triggered': bool(g['controlled_web'].get('triggered')), 'controlled_web_reason': g['controlled_web'].get('reason'), 'controlled_web_tool': g['controlled_web'].get('tool_name'), 'controlled_web_ok': g['controlled_web'].get('ok'), 'controlled_web_has_results': g['controlled_web'].get('has_results'), 'controlled_web_provider_info': g['controlled_web'].get('provider_info'), 'controlled_web_query': g['controlled_web'].get('query'), 'controlled_web_source_count': g['controlled_web'].get('source_count', 0), 'controlled_web_freshness_needed': g['controlled_web'].get('freshness_needed', False), 'consistency_check_ran': g['decision_core']['consistency_check_ran'], 'consistency_classification': g['decision_core']['consistency_classification'], 'contradictions_found': g['decision_core']['contradictions_found'], 'policy_hints_loaded': g['decision_core']['policy_hints_loaded'], 'policy_profile_name': g['decision_core']['policy_profile_name'], 'simulation_ran': g['decision_core']['simulation_ran'], 'simulation_best_action': g['decision_core']['simulation_best_action'], 'simulation_variants_count': g['decision_core']['simulation_variants_count'], 'simulation_risk_summary': g['decision_core']['simulation_risk_summary'], 'experience_lookup_happened': g['decision_core'].get('experience_lookup_happened', False), 'experience_matches_count': g['decision_core'].get('experience_matches_count', 0), 'experience_influenced_strategy': g['decision_core'].get('experience_influenced_strategy', False), 'experience_confidence_adjustment': g['decision_core'].get('experience_confidence_adjustment'), 'experience_handoff_bias': g['decision_core'].get('experience_handoff_bias'), 'experience_blocker_reason': g['decision_core'].get('experience_blocker_reason'), 'experience_signal_summary': g['decision_core'].get('experience_signal_summary'), 'reflection_ran': g['fallback_reflection']['reflection_ran'], 'reflection_summary': g['fallback_reflection']['reflection_summary'], 'selected_goal': g['decision_core'].get('selected_goal'), 'policy_feedback_loaded': bool(g['decision_core'].get('policy_feedback_loaded')), 'policy_feedback_applied': bool(g['decision_core'].get('policy_feedback_applied')), 'policy_feedback_summary': g['decision_core'].get('policy_feedback_summary', ''), 'policy_confidence_delta': g['decision_core'].get('policy_confidence_delta', 0.0), 'policy_handoff_bias': g['decision_core'].get('policy_handoff_bias', 0.0), 'policy_blocker_sensitivity': g['decision_core'].get('policy_blocker_sensitivity', 0.0), 'policy_simulation_risk_cal': g['decision_core'].get('policy_simulation_risk_cal', 0.0), 'policy_strategy_adjustments': g['decision_core'].get('policy_strategy_adjustments', {}), 'reflection_strategy_fit': g['fallback_reflection'].get('strategy_fit', 'neutral'), 'reflection_handoff_hindsight': g['fallback_reflection'].get('handoff_hindsight', 'na'), 'reflection_blocker_hindsight': g['fallback_reflection'].get('blocker_hindsight', 'na'), 'reflection_confidence_hindsight': g['fallback_reflection'].get('confidence_hindsight', 0.0), 'reflection_risk_hindsight': g['fallback_reflection'].get('risk_hindsight', 0.0), 'attached_files': g['attachment_meta'], 'attachments_summary': g['attachments_summary'], 'blocker_verdict': g['blocker_verdict'].model_dump()}
                if g['memory_used_trace']:
                    g['trace']['memory_used'] = g['memory_used_trace']
                self._augment_memory_observability(g['trace'], g['memory_used_trace'], g['ctx'].memory_context)
                trace_blocker_gate_outcome(g['trace'], gate_evaluated=True, hard_applied=False)
                merge_canonical_for_llm_path(g['trace'], decision_core=g['decision_core'], grounding_mode='fallback', memory_lookup_happened=g['memory_lookup_flag'], research_was_required=self._has_research_tool(g['tool_calls']), tool_calls=g['tool_calls'], web_verified_grounding_in_prompt=web_grounding_in_prompt(g['controlled_web']), tool_results=g['tool_results'], used_fallback=True, blocker_verdict_snapshot=g['blocker_verdict'].model_dump())
                self._attach_web_observability_trace(g['trace'], controlled_web=g['controlled_web'], tool_results=g['tool_results'], web_verified_in_prompt=web_grounding_in_prompt(g['controlled_web']))
                g['trace']['memory_substantive_in_prompt'] = g['memory_substantive_flag']
                g['trace'].update(self._final_behavior_trace_fields(g['psyche_v2_behavior_ctx']))
                g['trace'].update({'memory_v2_loaded': g['memory_v2_snapshot'].get('loaded', False), 'memory_v2_match_count': g['memory_v2_snapshot'].get('match_count', 0), 'memory_v2_reinforced_count': g['memory_v2_snapshot'].get('reinforced_count', 0), 'memory_v2_suppressed_count': g['memory_v2_snapshot'].get('suppressed_count', 0), 'memory_v2_contradictions_count': g['memory_v2_snapshot'].get('contradictions_count', 0), 'memory_v2_actionable_contradictions_count': g['memory_v2_snapshot'].get('actionable_contradictions_count', 0), 'memory_v2_transient_contradiction_count': g['memory_v2_snapshot'].get('transient_contradiction_count', 0), 'memory_v2_procedures_count': g['memory_v2_snapshot'].get('procedures_count', 0), 'memory_v2_top_reason_codes': g['memory_v2_snapshot'].get('top_reason_codes', []), 'memory_v2_retrieval_explanation': g['memory_v2_snapshot'].get('retrieval_strategy', ''), 'memory_v2_stability_tier_counts': dict(g['memory_v2_runtime_ctx'].stability_tier_counts) if g['memory_v2_runtime_ctx'] and g['memory_v2_runtime_ctx'].loaded else {}, 'memory_v2_procedure_confidence_raw': g['memory_v2_runtime_ctx'].confidence_modifier_raw if g['memory_v2_runtime_ctx'] and g['memory_v2_runtime_ctx'].loaded else 0.0, 'memory_v2_context_injected': bool(g['memory_v2_runtime_ctx'] and g['memory_v2_runtime_ctx'].loaded), 'memory_v2_context_item_count': len(g['memory_v2_runtime_ctx'].top_facts) + len(g['memory_v2_runtime_ctx'].top_preferences) if g['memory_v2_runtime_ctx'] else 0, 'memory_v2_procedure_bias_applied': bool(g['memory_v2_runtime_ctx'] and g['memory_v2_runtime_ctx'].loaded and (g['memory_v2_runtime_ctx'].confidence_modifier > 0.6)), 'memory_v2_contradiction_guard_applied': bool(g['memory_v2_runtime_ctx'] and g['memory_v2_runtime_ctx'].loaded and g['memory_v2_runtime_ctx'].contradiction_alerts), 'psyche_v2_loaded': g['psyche_v2_snapshot'].get('loaded', False), 'psyche_v2_mode': g['psyche_v2_snapshot'].get('mode', 'neutral'), 'psyche_v2_relation_trust': g['psyche_v2_snapshot'].get('relation_trust', 0.5), 'psyche_v2_relation_friction': g['psyche_v2_snapshot'].get('relation_friction', 0.0), 'psyche_v2_habit_biases': g['psyche_v2_snapshot'].get('habit_biases', []), 'psyche_v2_behavior_style': g['psyche_v2_snapshot'].get('behavior_policy', {}).get('directness', 0.5), 'psyche_v2_behavior_applied': bool(g['psyche_v2_behavior_ctx'] and g['psyche_v2_behavior_ctx'].loaded), 'psyche_v2_style_mode': getattr(g['psyche_v2_behavior_ctx'], 'mode', 'neutral') if g['psyche_v2_behavior_ctx'] and g['psyche_v2_behavior_ctx'].loaded else 'neutral', 'psyche_v2_pressure_applied': bool(g['psyche_v2_behavior_ctx'] and g['psyche_v2_behavior_ctx'].loaded and (getattr(g['psyche_v2_behavior_ctx'], 'pressure', 0.0) > 0.05)), 'psyche_v2_relation_tone_applied': bool(g['psyche_v2_behavior_ctx'] and g['psyche_v2_behavior_ctx'].loaded and (getattr(g['psyche_v2_behavior_ctx'], 'warmth', 0.0) > 0.6 or getattr(g['psyche_v2_behavior_ctx'], 'friction', 0.0) > 0.4)), 'final_behavior_profile': {'mode': getattr(g['psyche_v2_behavior_ctx'], 'mode', 'neutral'), 'directness': g['psyche_v2_behavior_ctx'].directness_bias, 'caution': g['psyche_v2_behavior_ctx'].caution_bias, 'tool_bias': g['psyche_v2_behavior_ctx'].tool_bias, 'web_bias': g['psyche_v2_behavior_ctx'].web_bias, 'reassurance': g['psyche_v2_behavior_ctx'].reassurance_bias} if g['psyche_v2_behavior_ctx'] and g['psyche_v2_behavior_ctx'].loaded else {}, 'memory_v2_writeback_attempted': False, 'memory_v2_writeback_succeeded': False, 'memory_v2_new_items_count': 0, 'memory_v2_new_lessons_count': 0, 'psyche_v2_writeback_attempted': False, 'psyche_v2_writeback_succeeded': False, 'psyche_v2_event_applied': None, 'response_outcome_quality': 'fallback'})
                g['trace']['memory_stm_brief_included'] = g['include_stm_in_memory_brief']
                g['trace']['context_history_messages_attached'] = len(g['hist_for_prompt'])
                g['trace']['vault_user_message_redacted'] = g['vault_user_redacted']
                g['trace'].update(g['hist_smart_trim'])
                augment_trace_context_truth(g['trace'], mem_truth=g['mem_truth'], controlled_web=g['controlled_web'], decision_core=g['decision_core'])
                # Cognitive / pragmatics observability + write-back also on provider_fallback
                try:
                    from aihub.turn.pragmatics import pragmatics_trace_fields, PragmaticAnalysis
                    from aihub.turn.cognitive_integration import (
                        CognitiveInfluencePack,
                        calibrate_from_outcome,
                        cognitive_trace_fields,
                        update_conversation_after_turn,
                        update_user_model_from_turn,
                    )
                    g['_pa_fb'] = g.get('pragmatics') or (g['ctx'].system_context or {}).get('pragmatics_obj')
                    if g['_pa_fb'] is None and isinstance((g['ctx'].system_context or {}).get('pragmatics'), dict):
                        g['_pa_fb'] = PragmaticAnalysis.model_validate(g['ctx'].system_context['pragmatics'])
                    if g['_pa_fb'] is not None:
                        g['trace'].update(pragmatics_trace_fields(g['_pa_fb']))
                    else:
                        g['trace']['pragmatics_analysis_happened'] = False
                        g['trace']['pragmatics_degraded'] = True
                    g['_cog_fb'] = g.get('cognitive') or (g['ctx'].system_context or {}).get('cognitive_obj')
                    if g['_cog_fb'] is None and isinstance((g['ctx'].system_context or {}).get('cognitive'), dict):
                        g['_cog_fb'] = CognitiveInfluencePack.model_validate(g['ctx'].system_context['cognitive'])
                    if g['_cog_fb'] is not None:
                        g['trace'].update(cognitive_trace_fields(g['_cog_fb']))
                        update_conversation_after_turn(
                            user_id=g['turn'].user_id,
                            session_id=g['turn'].session_id,
                            message=g['turn'].message or '',
                            response_text=g['fallback_text'] or '',
                            pragmatics=g['_pa_fb'],
                            pack=g['_cog_fb'],
                            ok=False,
                        )
                        g['_um_fb'] = update_user_model_from_turn(
                            user_id=g['turn'].user_id,
                            message=g['turn'].message or '',
                            response_text=g['fallback_text'] or '',
                            pragmatics=g['_pa_fb'],
                            critic_score=None,
                            revision_happened=False,
                            pack=g['_cog_fb'],
                        )
                        g['_cal_fb'] = calibrate_from_outcome(
                            user_id=g['turn'].user_id,
                            decision_core=g['decision_core'],
                            ok=False,
                            critic_score=None,
                            revision_happened=False,
                            web_used=bool(g['controlled_web'].get('triggered') and g['controlled_web'].get('ok')),
                            web_required=str(g['decision_core'].get('web_decision') or 'off') == 'required',
                            tool_successes=len([r for r in g['tool_results'] if r.ok]),
                            tool_failures=len([r for r in g['tool_results'] if not r.ok]),
                            correction_this_turn=bool(getattr(g['_pa_fb'], 'speech_act', '') == 'correction') if g['_pa_fb'] else False,
                        )
                        g['trace']['cognitive_writeback_happened'] = True
                        g['trace']['user_model_sample_count'] = g['_um_fb'].sample_count
                        g['trace']['user_model_length'] = g['_um_fb'].preferred_answer_length
                        g['trace']['user_model_humour'] = g['_um_fb'].preferred_humour
                        g['trace']['user_model_tech_depth'] = g['_um_fb'].preferred_technical_depth
                        g['trace']['user_model_confidence'] = g['_um_fb'].confidence
                        g['trace']['calibration_signals'] = list((g['_cal_fb'] or {}).get('signals') or [])
                        g['trace']['conversation_turn_count'] = int(
                            getattr(getattr(g['_cog_fb'], 'conversation', None), 'turn_count', 0) or 0
                        )
                    else:
                        g['trace']['cognitive_integration_happened'] = False
                        g['trace']['cognitive_degraded'] = True
                except Exception as _cog_fb_err:
                    logger.debug('fallback cognitive merge skipped: %s', _cog_fb_err, exc_info=True)
                try:
                    from aihub.adaptive_learning import process_turn_learning, learning_trace_fields
                    g['_tid_learn_fb'] = str(
                        getattr(getattr(self, '_active_turn_ctx', None), 'turn_id', None)
                        or getattr(g.get('ctx'), 'turn_id', None)
                        or (g.get('trace') or {}).get('turn_id')
                        or getattr(g['turn'], 'turn_id', None)
                        or ''
                    )
                    if not g['_tid_learn_fb']:
                        import uuid as _uuid_learn_fb
                        g['_tid_learn_fb'] = str(_uuid_learn_fb.uuid4())
                    g['_learn_fb'] = process_turn_learning(
                        turn_id=g['_tid_learn_fb'],
                        user_id=g['turn'].user_id,
                        session_id=g['turn'].session_id,
                        message=g['turn'].message or '',
                        response_text=g['fallback_text'] or '',
                        trace=g['trace'] if isinstance(g.get('trace'), dict) else {},
                        decision_core=g['decision_core'],
                        ok=False,
                        errors=list(g.get('errors') or []),
                        replay_mode=False,
                    )
                    g['trace'].update(learning_trace_fields(g['_learn_fb']))
                    if getattr(g['_learn_fb'], 'long_horizon_task_id', None):
                        g['trace']['long_horizon_task_id'] = g['_learn_fb'].long_horizon_task_id
                except Exception as _learn_fb_err:
                    logger.debug('fallback learning skipped: %s', _learn_fb_err, exc_info=True)
                    g['trace']['learning_degraded'] = True
                try:
                    from aihub.world_knowledge import process_turn_knowledge, knowledge_trace_fields
                    g['_tid_wk_fb'] = str(
                        getattr(getattr(self, '_active_turn_ctx', None), 'turn_id', None)
                        or getattr(g.get('ctx'), 'turn_id', None)
                        or (g.get('trace') or {}).get('turn_id')
                        or g.get('_tid_learn_fb')
                        or ''
                    )
                    g['_wk_fb'] = process_turn_knowledge(
                        turn_id=g['_tid_wk_fb'],
                        user_id=g['turn'].user_id,
                        session_id=g['turn'].session_id,
                        message=g['turn'].message or '',
                        response_text=g['fallback_text'] or '',
                        trace=g['trace'] if isinstance(g.get('trace'), dict) else {},
                        decision_core=g.get('decision_core') or {},
                        replay_mode=False,
                    )
                    g['trace'].update(knowledge_trace_fields(g['_wk_fb']))
                    if (g.get('decision_core') or {}).get('knowledge_context_loaded'):
                        g['trace']['knowledge_context_loaded'] = True
                        g['trace']['knowledge_entities_count'] = g['decision_core'].get('knowledge_entities_count')
                        g['trace']['knowledge_claims_count'] = g['decision_core'].get('knowledge_claims_count')
                        g['trace']['verification_required'] = g['decision_core'].get('verification_required')
                        g['trace']['graph_influenced_strategy'] = bool(g['decision_core'].get('graph_influenced_strategy'))
                        g['trace']['graph_influenced_planner'] = bool(g['decision_core'].get('graph_influenced_planner'))
                except Exception as _wk_fb_err:
                    logger.debug('fallback world knowledge skipped: %s', _wk_fb_err, exc_info=True)
                    g['trace']['knowledge_learning_degraded'] = True
                merge_provider_trace_from_builder(g['trace'], getattr(self, '_active_trace_builder', None))
                apply_provider_failure_response_trace_honesty(g['trace'])
                self._write_back_experience(turn=g['turn'], response_text=g['fallback_text'], grounding_mode='fallback', tool_calls=g['tool_calls'], tool_results=g['tool_results'], trace=g['trace'], errors=g['errors'], psyche_snapshot=g['psyche_snapshot'], decision_core=g['decision_core'])
                if str(getattr(g['turn'], 'runtime_mode', '') or '').lower() == 'audit':
                    g['trace']['psyche_snapshot_happened'] = False
                    g['trace']['experience_write_back_attempted'] = False
                    g['trace']['experience_write_back_succeeded'] = False
                self._run_runtime_experience_feedback(g['turn'].user_id, g['trace'])
                _cr_hook('append_event', append_event)(g['turn'].user_id, 'chat.turn', {'ok': False, 'provider': g['_fb_provider'], 'model': g['_fb_model'], 'errors': g['errors'], 'trace': g['trace']})
                g['result'] = ChatTurnResult(ok=False, response_text=g['fallback_text'], model=g['_fb_model'], provider=g['_fb_provider'], tool_calls=g['tool_calls'], tool_results=g['tool_results'], selected_mode=g['ctx'].mode, usage=self._sum_usage(g['provider_usages']), trace=g['trace'], errors=g['errors'], debug={'context': g['ctx'].model_dump()} if g['turn'].include_debug else None, attachments_summary=g['attachments_summary'])
                _TRACE_CACHE[g['turn'].user_id].append(g['result'].trace)
                g['__result__'] = g['result']
                return
            g['final_model'] = g['model_response'].model
            g['final_provider'] = g['model_response'].provider
            g['provider_usages'].append(g['model_response'].usage)
            g['usage_summary'] = self._sum_usage(g['provider_usages'])
            tb = getattr(self, '_active_trace_builder', None)
            if tb is not None and isinstance(getattr(tb, '_data', None), dict):
                for pk in (
                    'provider_primary', 'provider_reserve', 'provider_candidates',
                    'provider_attempt_count', 'provider_attempts', 'provider_failover_happened',
                    'provider_selected_final', 'provider_final_model', 'provider_final_ok',
                    'provider_total_duration_ms',
                ):
                    if pk in tb._data:
                        g.setdefault('_provider_trace', {})[pk] = tb._data[pk]
            if g['model_response'].tool_calls and g['iteration'] < max(1, int(CHAT_MAX_TOOL_ITERATIONS)):
                g['messages'].append(ChatMessage(role='assistant', content=g['model_response'].content, tool_calls=g['model_response'].tool_calls))
                g['exec_ctx'] = ToolExecutionContext(user_id=g['turn'].user_id, session_id=g['turn'].session_id, mode=g['ctx'].mode, include_debug=g['turn'].include_debug, policy_overrides=dict(g['turn'].tool_policy_overrides or {}))
                if stream_session_active():
                    await emit_status('tools', label_pl='Wykonuję kroki…')
                for g['call'] in g['model_response'].tool_calls:
                    g['tool_calls'].append(g['call'])
                    g['tlabel'] = self._sse_tool_display_name(g['call'].name)
                    if stream_session_active():
                        await emit_tool_event(g['tlabel'], 'start')
                    g['res'] = await self._tool_router.execute(g['call'], g['exec_ctx'])
                    if stream_session_active():
                        await emit_tool_event(g['tlabel'], 'done')
                    g['tool_results'].append(g['res'])
                    g['tool_payload'] = {'ok': g['res'].ok, 'output': g['res'].output, 'error': g['res'].error}
                    g['messages'].append(ChatMessage(role='tool', name=g['call'].name, tool_call_id=g['call'].tool_call_id, content=json.dumps(g['tool_payload'], ensure_ascii=False)))
                continue
            g['response_text'] = g['model_response'].content or ''
            break

    async def _stage_provider_post(self, g):
        if not g['response_text'] and g['tool_results']:
            g['ok_results'] = [g['r'] for g['r'] in g['tool_results'] if g['r'].ok]
            if g['ok_results']:
                g['response_text'] = self._build_controlled_web_synthesis(controlled_web=g['controlled_web'], tool_results=g['tool_results']) or 'Narzędzia poszły, wyniki są — powiedz, jak je ułożyć w odpowiedź.'
            else:
                g['response_text'] = 'Narzędzia w tej turze się potknęły — doprecyzuj, co dokładnie odpalić, albo spróbuj jeszcze raz bez dramatu.'
        g['grounding_mode'] = self._classify_grounding_mode(used_fallback=False, tool_calls=g['tool_calls'], tool_results=g['tool_results'])

    async def _stage_shape_deliberation(self, g):
        g['deliberation_metadata']: dict[str, Any] = {}
        if stream_session_active():
            await emit_status('finalizing', label_pl='Kończę odpowiedź…')
        try:
            g['original_msgs'] = [{'role': g['m'].role, 'content': g['m'].content, 'name': g['m'].name, 'tool_call_id': g['m'].tool_call_id} for g['m'] in g['messages']]
            g['deliberated_text'], g['deliberation_metadata'] = await _cr_hook('ResponseVariantsEngine', ResponseVariantsEngine).run_deliberation(decision_core=g['decision_core'], blocker_verdict=g['blocker_verdict'], original_response=g['response_text'], original_messages=g['original_msgs'], provider_call_fn=self._provider_call, deliberation_history=g['decision_core'].get('deliberation_history'))
            if g['deliberation_metadata'].get('response_variants_triggered'):
                g['response_text'] = g['deliberated_text']
                logger.info('Deliberation replaced response_text: winner=%s confidence=%.2f', g['deliberation_metadata'].get('response_variants_winner_type', '?'), g['deliberation_metadata'].get('response_variants_confidence', 0.0))
        except Exception:
            logger.warning('Deliberation engine failed — using original response', exc_info=True)
            g['deliberation_metadata'] = {'response_variants_triggered': False, 'response_variants_count': 0, 'response_variants_reason_codes': [], 'response_variants_error': True}
        g['anti_hallucination_trace']: dict[str, Any] = {}
        g['response_text'] = self._shape_response_text(turn=g['turn'], ctx=g['ctx'], response_text=g['response_text'], grounding_mode=g['grounding_mode'], used_fallback=False, memory_v2_context=g['memory_v2_runtime_ctx'], psyche_v2_context=g['psyche_v2_behavior_ctx'], anti_hallucination_trace=g['anti_hallucination_trace'])
        for g['_dk'] in ('response_variants_triggered', 'response_variants_confidence', 'response_variants_risk', 'response_variants_synthesis_used', 'response_variants_winner_type'):
            if g['_dk'] in g['deliberation_metadata']:
                g['decision_core'][g['_dk']] = g['deliberation_metadata'][g['_dk']]
        g['decision_core']['deliberation_outcome_quality'] = self._compute_deliberation_outcome_quality(g['deliberation_metadata'])
        g['post_reflection'] = self._post_exec_reflection(user_id=g['turn'].user_id, message=g['turn'].message, response_text=g['response_text'], tool_calls=g['tool_calls'], tool_results=g['tool_results'], decision_core=g['decision_core'], blocker_verdict=g['blocker_verdict'], handoff_happened=False)
        # Response critic V2 — at most one revision, no re-run of side-effect tools
        g['response_revision_happened'] = False
        if not getattr(g['turn'], 'skip_response_critic', False):
            try:
                from aihub.turn.pragmatics import PragmaticAnalysis
                from aihub.turn.cognitive_integration import critique_response_v2, CognitiveInfluencePack
                g['_pa'] = g.get('pragmatics') or (g['ctx'].system_context or {}).get('pragmatics_obj')
                if g['_pa'] is None and isinstance((g['ctx'].system_context or {}).get('pragmatics'), dict):
                    g['_pa'] = PragmaticAnalysis.model_validate(g['ctx'].system_context['pragmatics'])
                g['_cog'] = g.get('cognitive') or (g['ctx'].system_context or {}).get('cognitive_obj')
                if g['_cog'] is None and isinstance((g['ctx'].system_context or {}).get('cognitive'), dict):
                    g['_cog'] = CognitiveInfluencePack.model_validate(g['ctx'].system_context['cognitive'])
                if g['_pa'] is not None or g['_cog'] is not None:
                    g['critic'] = critique_response_v2(
                        response_text=g['response_text'],
                        pragmatics=g['_pa'],
                        pack=g['_cog'],
                        memory_used=bool(g.get('memory_substantive_flag') or (g.get('memory_v2_runtime_ctx') and getattr(g['memory_v2_runtime_ctx'], 'loaded', False))),
                        psyche_used=bool((g.get('psyche_v2_behavior_ctx') and getattr(g['psyche_v2_behavior_ctx'], 'loaded', False)) or (g.get('psyche_brief') and 'BRAK' not in str(g.get('psyche_brief') or '')[:20])),
                        planner_recommended=bool(g['decision_core'].get('planner_recommended') or g['decision_core'].get('escalation_use_reasoning')),
                        web_used=bool(g['controlled_web'].get('triggered') and g['controlled_web'].get('ok')),
                        web_was_required=str(g['decision_core'].get('web_decision') or 'off') == 'required',
                    )
                    if g['_pa'] is not None:
                        g['_pa'].critic = g['critic']
                    if (not g['critic'].passed) and g['critic'].revision_instruction and g.get('messages'):
                        g['messages'].append(ChatMessage(role='user', content=('[Korekta odpowiedzi — nie zmieniaj tematu użytkownika. Instrukcja:] ' + g['critic'].revision_instruction + '\n\nOdpowiedź do poprawy:\n' + (g['response_text'] or '')[:2500])))
                        try:
                            g['rev'] = await self._provider_call(messages=g['messages'], tools=[])
                            if g['rev'] and (g['rev'].content or '').strip():
                                g['response_text'] = g['rev'].content
                                g['response_revision_happened'] = True
                                g['provider_usages'].append(g['rev'].usage)
                                g['provider_call_count'] = int(g.get('provider_call_count') or 0) + 1
                        except Exception as rev_exc:
                            g['rev_exc'] = rev_exc
                            logger.debug('response critic revision skipped: %s', rev_exc)
                    if g['_pa'] is not None:
                        g['ctx'].system_context['pragmatics'] = g['_pa'].model_dump()
                        g['ctx'].system_context['pragmatics_obj'] = g['_pa']
                        g['pragmatics'] = g['_pa']
                    if g['_cog'] is not None:
                        g['ctx'].system_context['cognitive'] = g['_cog'].model_dump()
                        g['ctx'].system_context['cognitive_obj'] = g['_cog']
                        g['cognitive'] = g['_cog']
            except Exception as critic_exc:
                g['critic_exc'] = critic_exc
                logger.debug('response critic failed: %s', critic_exc, exc_info=True)
        g['response_text'] = self._shape_response_text(turn=g['turn'], ctx=g['ctx'], response_text=g['response_text'], grounding_mode=g['grounding_mode'], used_fallback=False, memory_v2_context=g['memory_v2_runtime_ctx'], psyche_v2_context=g['psyche_v2_behavior_ctx'], anti_hallucination_trace=g['anti_hallucination_trace'])
        try:
            from aihub.world_knowledge import apply_action_claim_guard
            g['response_text'], g['_acg'] = apply_action_claim_guard(
                response_text=g['response_text'] or '',
                tool_results=g.get('tool_results') or [],
                validation_succeeded=bool((g.get('decision_core') or {}).get('validation_succeeded')),
                execution_effects=list((g.get('decision_core') or {}).get('execution_effects') or []),
                trace=g['trace'] if isinstance(g.get('trace'), dict) else None,
            )
            if isinstance(g.get('trace'), dict) and isinstance(g.get('_acg'), dict):
                g['trace'].update({k: v for k, v in g['_acg'].items() if k.startswith('action_claim')})
        except Exception:
            logger.debug('action claim guard skipped', exc_info=True)
        for g['_dk'] in ('response_variants_triggered', 'response_variants_confidence', 'response_variants_risk', 'response_variants_synthesis_used', 'response_variants_winner_type'):
            if g['_dk'] in g['deliberation_metadata']:
                g['decision_core'][g['_dk']] = g['deliberation_metadata'][g['_dk']]
        g['decision_core']['deliberation_outcome_quality'] = self._compute_deliberation_outcome_quality(g['deliberation_metadata'])
        g['post_reflection'] = self._post_exec_reflection(user_id=g['turn'].user_id, message=g['turn'].message, response_text=g['response_text'], tool_calls=g['tool_calls'], tool_results=g['tool_results'], decision_core=g['decision_core'], blocker_verdict=g['blocker_verdict'], handoff_happened=False)

    async def _stage_build_success_trace(self, g):
        g['duration_ms'] = (time.monotonic() - g['started']) * 1000.0
        g['research_required'] = self._has_research_tool(g['tool_calls'])
        g['usage_summary'] = self._sum_usage(g['provider_usages'])
        g['memory_v2_context_injected'] = bool(g['memory_v2_runtime_ctx'] and g['memory_v2_runtime_ctx'].loaded)
        g['memory_v2_context_item_count'] = len(g['memory_v2_runtime_ctx'].top_facts) + len(g['memory_v2_runtime_ctx'].top_preferences) if g['memory_v2_runtime_ctx'] else 0
        g['memory_v2_procedure_bias_applied'] = bool(g['memory_v2_runtime_ctx'] and g['memory_v2_runtime_ctx'].loaded and (g['memory_v2_runtime_ctx'].confidence_modifier > 0.6))
        g['memory_v2_contradiction_guard_applied'] = bool(g['memory_v2_runtime_ctx'] and g['memory_v2_runtime_ctx'].loaded and g['memory_v2_runtime_ctx'].contradiction_alerts and g['psyche_v2_behavior_ctx'] and (g['psyche_v2_behavior_ctx'].caution_bias > 0.5))
        g['psyche_v2_behavior_applied'] = bool(g['psyche_v2_behavior_ctx'] and g['psyche_v2_behavior_ctx'].loaded)
        g['psyche_v2_style_mode'] = g['psyche_v2_behavior_ctx'].mode if g['psyche_v2_behavior_ctx'] else 'neutral'
        g['psyche_v2_pressure_applied'] = bool(g['psyche_v2_behavior_ctx'] and g['psyche_v2_behavior_ctx'].loaded and (g['psyche_v2_behavior_ctx'].pressure > 0.5))
        g['psyche_v2_relation_tone_applied'] = bool(g['psyche_v2_behavior_ctx'] and g['psyche_v2_behavior_ctx'].loaded and (g['psyche_v2_behavior_ctx'].friction > 0.5 or g['psyche_v2_behavior_ctx'].warmth > 0.7))
        g['final_behavior_profile'] = self._neutral_final_behavior_profile(mode=g['psyche_v2_style_mode'])
        if g['psyche_v2_behavior_ctx'] and g['psyche_v2_behavior_ctx'].loaded:
            g['final_behavior_profile'] = {'mode': g['psyche_v2_style_mode'], 'directness': g['psyche_v2_behavior_ctx'].directness_bias, 'verbosity': g['psyche_v2_behavior_ctx'].verbosity_bias, 'caution': g['psyche_v2_behavior_ctx'].caution_bias, 'pressure': g['psyche_v2_behavior_ctx'].pressure, 'trust': g['psyche_v2_behavior_ctx'].trust, 'friction': g['psyche_v2_behavior_ctx'].friction, 'warmth': g['psyche_v2_behavior_ctx'].warmth, 'autonomy': g['psyche_v2_behavior_ctx'].autonomy_bias, 'structuredness': g['psyche_v2_behavior_ctx'].structuredness_bias, 'tool_bias': g['psyche_v2_behavior_ctx'].tool_bias, 'web_bias': g['psyche_v2_behavior_ctx'].web_bias, 'reassurance': g['psyche_v2_behavior_ctx'].reassurance_bias}
        g['trace'] = {'provider_calls': g['provider_call_count'], 'tool_iterations': min(g['provider_call_count'], max(1, int(CHAT_MAX_TOOL_ITERATIONS))), 'tool_calls_requested': len(g['tool_calls']), 'tool_calls_executed': len(g['tool_results']), 'tool_calls_successful': len([g['r'] for g['r'] in g['tool_results'] if g['r'].ok]), 'tool_failures': len([g['r'] for g['r'] in g['tool_results'] if not g['r'].ok]), 'used_tools': len(g['tool_results']) > 0, 'used_fallback': False, **self._correction_trace_fields(g['ctx']), 'anti_hallucination_clamp_applied': bool(g['anti_hallucination_trace'].get('applied')), 'anti_hallucination_clamp_reason': g['anti_hallucination_trace'].get('reason'), 'response_grounding_mode': g['grounding_mode'], 'chat_thread_first_turn': g['first_turn_in_thread'], 'chat_history_message_count': len(g['turn'].history or []), **build_history_trace(g['turn']), 'duration_ms': g['duration_ms'], 'provider': g['final_provider'], 'model': g['final_model'], 'usage_reporting_mode': g['usage_summary'].reporting_mode, 'usage_total_tokens': g['usage_summary'].total_tokens, 'selected_strategy': g['decision_core']['selected_strategy'], **self._decision_core_trace_escalation(g['decision_core']), 'reason_codes': g['decision_core']['reason_codes'], 'strategy_confidence': g['decision_core']['strategy_confidence'], 'degraded': g['decision_core']['strategy_degraded'], 'memory_lookup_happened': g['memory_lookup_flag'], 'memory_results_count': memory_results_count_for_trace(g['ctx'].memory_context), 'psyche_snapshot_happened': False, 'research_was_required': g['research_required'], 'agentic_executed': False, 'tool_calls_count': len(g['tool_calls']), 'experience_write_back_attempted': False, 'experience_write_back_succeeded': False, 'controlled_web_decision': g['decision_core'].get('web_decision', 'off'), 'controlled_web_decision_reason': g['decision_core'].get('web_decision_reason', 'not_evaluated'), 'controlled_web_triggered': bool(g['controlled_web'].get('triggered')), 'controlled_web_reason': g['controlled_web'].get('reason'), 'controlled_web_tool': g['controlled_web'].get('tool_name'), 'controlled_web_ok': g['controlled_web'].get('ok'), 'controlled_web_has_results': g['controlled_web'].get('has_results'), 'controlled_web_provider_info': g['controlled_web'].get('provider_info'), 'controlled_web_query': g['controlled_web'].get('query'), 'controlled_web_source_count': g['controlled_web'].get('source_count', 0), 'controlled_web_freshness_needed': g['controlled_web'].get('freshness_needed', False), **self._web_stage_trace_fields(g['decision_core'], g['controlled_web'], explicit_fail_applied=False), 'consistency_check_ran': g['decision_core']['consistency_check_ran'], 'consistency_classification': g['decision_core']['consistency_classification'], 'contradictions_found': g['decision_core']['contradictions_found'], 'policy_hints_loaded': g['decision_core']['policy_hints_loaded'], 'policy_profile_name': g['decision_core']['policy_profile_name'], 'simulation_ran': g['decision_core']['simulation_ran'], 'simulation_best_action': g['decision_core']['simulation_best_action'], 'simulation_variants_count': g['decision_core']['simulation_variants_count'], 'simulation_risk_summary': g['decision_core']['simulation_risk_summary'], 'experience_lookup_happened': g['decision_core'].get('experience_lookup_happened', False), 'experience_matches_count': g['decision_core'].get('experience_matches_count', 0), 'experience_influenced_strategy': g['decision_core'].get('experience_influenced_strategy', False), 'experience_confidence_adjustment': g['decision_core'].get('experience_confidence_adjustment'), 'experience_handoff_bias': g['decision_core'].get('experience_handoff_bias'), 'experience_blocker_reason': g['decision_core'].get('experience_blocker_reason'), 'experience_signal_summary': g['decision_core'].get('experience_signal_summary'), 'reflection_ran': g['post_reflection']['reflection_ran'], 'reflection_summary': g['post_reflection']['reflection_summary'], 'selected_goal': g['decision_core'].get('selected_goal'), 'policy_feedback_loaded': bool(g['decision_core'].get('policy_feedback_loaded')), 'policy_feedback_applied': bool(g['decision_core'].get('policy_feedback_applied')), 'policy_feedback_summary': g['decision_core'].get('policy_feedback_summary', ''), 'policy_confidence_delta': g['decision_core'].get('policy_confidence_delta', 0.0), 'policy_handoff_bias': g['decision_core'].get('policy_handoff_bias', 0.0), 'policy_blocker_sensitivity': g['decision_core'].get('policy_blocker_sensitivity', 0.0), 'policy_simulation_risk_cal': g['decision_core'].get('policy_simulation_risk_cal', 0.0), 'policy_strategy_adjustments': g['decision_core'].get('policy_strategy_adjustments', {}), 'reflection_strategy_fit': g['post_reflection'].get('strategy_fit', 'neutral'), 'reflection_handoff_hindsight': g['post_reflection'].get('handoff_hindsight', 'na'), 'reflection_blocker_hindsight': g['post_reflection'].get('blocker_hindsight', 'na'), 'memory_v2_loaded': g['memory_v2_snapshot'].get('loaded', False), 'memory_v2_match_count': g['memory_v2_snapshot'].get('match_count', 0), 'memory_v2_reinforced_count': g['memory_v2_snapshot'].get('reinforced_count', 0), 'memory_v2_suppressed_count': g['memory_v2_snapshot'].get('suppressed_count', 0), 'memory_v2_contradictions_count': g['memory_v2_snapshot'].get('contradictions_count', 0), 'memory_v2_actionable_contradictions_count': g['memory_v2_snapshot'].get('actionable_contradictions_count', 0), 'memory_v2_transient_contradiction_count': g['memory_v2_snapshot'].get('transient_contradiction_count', 0), 'memory_v2_stability_tier_counts': dict(g['memory_v2_runtime_ctx'].stability_tier_counts) if g['memory_v2_runtime_ctx'] and g['memory_v2_runtime_ctx'].loaded else {}, 'memory_v2_procedure_confidence_raw': g['memory_v2_runtime_ctx'].confidence_modifier_raw if g['memory_v2_runtime_ctx'] and g['memory_v2_runtime_ctx'].loaded else 0.0, 'self_consistency_decision': g['psyche_v2_behavior_ctx'].consistency_decision if g['psyche_v2_behavior_ctx'] and g['psyche_v2_behavior_ctx'].loaded else 'allow', 'self_consistency_reasons': list(g['psyche_v2_behavior_ctx'].consistency_reasons) if g['psyche_v2_behavior_ctx'] and g['psyche_v2_behavior_ctx'].loaded else [], 'memory_v2_procedures_count': g['memory_v2_snapshot'].get('procedures_count', 0), 'memory_v2_top_reason_codes': g['memory_v2_snapshot'].get('top_reason_codes', []), 'memory_v2_retrieval_explanation': g['memory_v2_snapshot'].get('retrieval_strategy', ''), 'psyche_v2_loaded': g['psyche_v2_snapshot'].get('loaded', False), 'psyche_v2_mode': g['psyche_v2_snapshot'].get('mode', 'neutral'), 'psyche_v2_relation_trust': g['psyche_v2_snapshot'].get('relation_trust', 0.5), 'psyche_v2_relation_friction': g['psyche_v2_snapshot'].get('relation_friction', 0.0), 'psyche_v2_habit_biases': g['psyche_v2_snapshot'].get('habit_biases', []), 'psyche_v2_behavior_style': g['psyche_v2_snapshot'].get('behavior_policy', {}).get('directness', 0.5), 'identity_bridge_loaded': g['identity_bridge_snapshot'] is not None, 'memory_influenced_strategy': g['decision_core'].get('memory_influenced_strategy_chat', False), 'psyche_influenced_strategy': g['decision_core'].get('psyche_influenced_strategy_chat', False), 'memory_v2_context_injected': g['memory_v2_context_injected'], 'memory_v2_context_item_count': g['memory_v2_context_item_count'], 'memory_v2_procedure_bias_applied': g['memory_v2_procedure_bias_applied'], 'memory_v2_contradiction_guard_applied': g['memory_v2_contradiction_guard_applied'], 'psyche_v2_behavior_applied': g['psyche_v2_behavior_applied'], 'psyche_v2_style_mode': g['psyche_v2_style_mode'], 'psyche_v2_pressure_applied': g['psyche_v2_pressure_applied'], 'psyche_v2_relation_tone_applied': g['psyche_v2_relation_tone_applied'], 'final_behavior_profile': g['final_behavior_profile'], 'reflection_confidence_hindsight': g['post_reflection'].get('confidence_hindsight', 0.0), 'reflection_risk_hindsight': g['post_reflection'].get('risk_hindsight', 0.0), 'reflection_deliberation_hindsight': g['post_reflection'].get('deliberation_hindsight', {}), 'attached_files': g['attachment_meta'], 'attachments_summary': g['attachments_summary'], 'blocker_verdict': g['blocker_verdict'].model_dump(), 'response_variants_triggered': g['deliberation_metadata'].get('response_variants_triggered', False), 'response_variants_count': g['deliberation_metadata'].get('response_variants_count', 0), 'response_variants_reason_codes': g['deliberation_metadata'].get('response_variants_reason_codes', []), 'response_variants_winner': g['deliberation_metadata'].get('response_variants_winner'), 'response_variants_winner_type': g['deliberation_metadata'].get('response_variants_winner_type'), 'response_variants_synthesis_used': g['deliberation_metadata'].get('response_variants_synthesis_used', []), 'response_variants_dropped': g['deliberation_metadata'].get('response_variants_dropped', []), 'response_variants_confidence': g['deliberation_metadata'].get('response_variants_confidence'), 'response_variants_risk': g['deliberation_metadata'].get('response_variants_risk'), 'response_variants_summary': g['deliberation_metadata'].get('response_variants_summary'), 'response_variants_duration_ms': g['deliberation_metadata'].get('response_variants_duration_ms'), 'response_variants_scores': g['deliberation_metadata'].get('response_variants_scores', []), 'response_variants_error': g['deliberation_metadata'].get('response_variants_error', False)}
        if isinstance(g.get('_provider_trace'), dict):
            g['trace'].update(g['_provider_trace'])
        if g['memory_used_trace']:
            g['trace']['memory_used'] = g['memory_used_trace']
        self._augment_memory_observability(g['trace'], g['memory_used_trace'], g['ctx'].memory_context)
        trace_blocker_gate_outcome(g['trace'], gate_evaluated=True, hard_applied=False)
        merge_canonical_for_llm_path(g['trace'], decision_core=g['decision_core'], grounding_mode=g['grounding_mode'], memory_lookup_happened=g['memory_lookup_flag'], research_was_required=g['research_required'], tool_calls=g['tool_calls'], web_verified_grounding_in_prompt=web_grounding_in_prompt(g['controlled_web']), tool_results=g['tool_results'], used_fallback=False, blocker_verdict_snapshot=g['blocker_verdict'].model_dump())
        self._attach_web_observability_trace(g['trace'], controlled_web=g['controlled_web'], tool_results=g['tool_results'], web_verified_in_prompt=web_grounding_in_prompt(g['controlled_web']))
        g['trace']['memory_substantive_in_prompt'] = g['memory_substantive_flag']
        g['trace']['memory_stm_brief_included'] = g['include_stm_in_memory_brief']
        g['trace']['context_history_messages_attached'] = len(g['hist_for_prompt'])
        g['trace']['vault_user_message_redacted'] = g['vault_user_redacted']
        g['trace'].update(g['hist_smart_trim'])
        augment_trace_context_truth(g['trace'], mem_truth=g['mem_truth'], controlled_web=g['controlled_web'], decision_core=g['decision_core'])
        try:
            from aihub.turn.pragmatics import pragmatics_trace_fields, PragmaticAnalysis
            from aihub.turn.cognitive_integration import cognitive_trace_fields, CognitiveInfluencePack
            g['_pa_t'] = g.get('pragmatics') or (g['ctx'].system_context or {}).get('pragmatics_obj')
            if g['_pa_t'] is None and isinstance((g['ctx'].system_context or {}).get('pragmatics'), dict):
                g['_pa_t'] = PragmaticAnalysis.model_validate(g['ctx'].system_context['pragmatics'])
            if g['_pa_t'] is not None:
                g['trace'].update(pragmatics_trace_fields(g['_pa_t']))
                g['trace']['response_revision_happened'] = bool(g.get('response_revision_happened'))
                if g.get('critic') is not None:
                    g['trace']['response_critic_score'] = getattr(g['critic'], 'score', None)
                    g['trace']['response_revision_reason_codes'] = list(getattr(g['critic'], 'reason_codes', []) or [])
            else:
                g['trace']['pragmatics_analysis_happened'] = False
                g['trace']['pragmatics_degraded'] = True
            g['_cog_t'] = g.get('cognitive') or (g['ctx'].system_context or {}).get('cognitive_obj')
            if g['_cog_t'] is None and isinstance((g['ctx'].system_context or {}).get('cognitive'), dict):
                g['_cog_t'] = CognitiveInfluencePack.model_validate(g['ctx'].system_context['cognitive'])
            if g['_cog_t'] is not None:
                g['trace'].update(cognitive_trace_fields(g['_cog_t']))
        except Exception:
            g['trace']['pragmatics_analysis_happened'] = False
            g['trace']['pragmatics_degraded'] = True
            g['trace']['cognitive_integration_happened'] = False
            g['trace']['cognitive_degraded'] = True

    async def _stage_writeback(self, g):
        g['trace']['memory_v2_writeback_attempted'] = False
        g['trace']['memory_v2_writeback_succeeded'] = False
        g['trace']['memory_v2_new_items_count'] = 0
        g['trace']['memory_v2_new_lessons_count'] = 0
        g['trace']['psyche_v2_writeback_attempted'] = False
        g['trace']['psyche_v2_writeback_succeeded'] = False
        g['trace']['psyche_v2_event_applied'] = None
        g['trace']['response_outcome_quality'] = 'success'
        if str(getattr(g['turn'], 'runtime_mode', '') or '').lower() != 'audit':
            try:
                from aihub.memory_core import get_memory_core
                g['_psy_svc'] = get_psyche_core().v2_service
                g['degraded'] = bool(g['decision_core'].get('strategy_degraded', False))
                g['fallback'] = False
                if len(g['errors']) > 0:
                    g['trace']['response_outcome_quality'] = 'blocked' if any((g['e'].get('blocker', False) for g['e'] in g['errors'])) else 'fallback'
                    g['fallback'] = True
                elif g['degraded']:
                    g['trace']['response_outcome_quality'] = 'degraded'
                elif not g['response_text'] or len(g['response_text'].strip()) < 10:
                    g['trace']['response_outcome_quality'] = 'fallback'
                    g['fallback'] = True
                g['turn_id_for_wb'] = str(getattr(getattr(self, '_active_turn_ctx', None), 'turn_id', None) or getattr(g['turn'], 'turn_id', None) or uuid.uuid4())
                g['memory_wb'] = get_memory_core().record_chat_outcome(user_id=g['turn'].user_id, turn_id=g['turn_id_for_wb'], query_text=g['turn'].message or '', response_text=g['response_text'], strategy=g['decision_core']['selected_strategy'], grounding_mode=g['grounding_mode'], tool_calls_count=len(g['tool_calls']), tool_successes=len([g['r'] for g['r'] in g['tool_results'] if g['r'].ok]), tool_failures=len([g['r'] for g['r'] in g['tool_results'] if not g['r'].ok]), contradictions_present=g['memory_v2_snapshot'].get('contradictions_count', 0), memory_matches=g['memory_v2_snapshot'].get('match_count', 0), degraded=g['degraded'], fallback=g['fallback'])
                g['trace']['memory_v2_writeback_attempted'] = g['memory_wb'].get('attempted', False)
                g['trace']['memory_v2_writeback_succeeded'] = g['memory_wb'].get('succeeded', False)
                g['trace']['memory_v2_new_items_count'] = g['memory_wb'].get('new_items_count', 0)
                g['trace']['memory_v2_new_lessons_count'] = g['memory_wb'].get('new_lessons_count', 0)
                g['outcome_kind'] = g['trace']['response_outcome_quality']
                g['psyche_wb'] = g['_psy_svc'].apply_outcome_event(user_id=g['turn'].user_id, outcome_kind=g['outcome_kind'] if g['outcome_kind'] != 'blocked' else 'failure', source_ref=g['turn_id_for_wb'], context={'contradictions_present': g['memory_v2_snapshot'].get('contradictions_count', 0), 'grounding_mode': g['grounding_mode'], 'tool_calls_count': len(g['tool_calls'])})
                g['trace']['psyche_v2_writeback_attempted'] = g['psyche_wb'].get('attempted', False)
                g['trace']['psyche_v2_writeback_succeeded'] = g['psyche_wb'].get('succeeded', False)
                g['trace']['psyche_v2_event_applied'] = g['psyche_wb'].get('event_applied')
                logger.info(f"V2 chat write-back: memory={g['memory_wb'].get('succeeded')} psyche={g['psyche_wb'].get('succeeded')} user={g['turn'].user_id}")
                # Cognitive write-backs: conversation state, user model, self-calibration
                try:
                    from aihub.turn.cognitive_integration import (
                        update_conversation_after_turn,
                        update_user_model_from_turn,
                        calibrate_from_outcome,
                        CognitiveInfluencePack,
                    )
                    g['_cog_wb'] = g.get('cognitive') or (g['ctx'].system_context or {}).get('cognitive_obj')
                    if g['_cog_wb'] is None and isinstance((g['ctx'].system_context or {}).get('cognitive'), dict):
                        g['_cog_wb'] = CognitiveInfluencePack.model_validate(g['ctx'].system_context['cognitive'])
                    g['_pa_wb'] = g.get('pragmatics') or (g['ctx'].system_context or {}).get('pragmatics_obj')
                    g['_cs'] = update_conversation_after_turn(
                        user_id=g['turn'].user_id,
                        session_id=g['turn'].session_id,
                        message=g['turn'].message or '',
                        response_text=g['response_text'] or '',
                        pragmatics=g['_pa_wb'],
                        pack=g['_cog_wb'],
                        ok=len(g['errors']) == 0 and not g['fallback'],
                    )
                    g['_um'] = update_user_model_from_turn(
                        user_id=g['turn'].user_id,
                        message=g['turn'].message or '',
                        response_text=g['response_text'] or '',
                        pragmatics=g['_pa_wb'],
                        critic_score=(g['trace'].get('response_critic_score') if isinstance(g.get('trace'), dict) else None) or (getattr(g.get('critic'), 'score', None)),
                        revision_happened=bool(g.get('response_revision_happened')),
                        pack=g['_cog_wb'],
                    )
                    g['_cal'] = calibrate_from_outcome(
                        user_id=g['turn'].user_id,
                        decision_core=g['decision_core'],
                        ok=len(g['errors']) == 0 and not g['fallback'],
                        critic_score=(g['trace'].get('response_critic_score') if isinstance(g.get('trace'), dict) else None) or (getattr(g.get('critic'), 'score', None)),
                        revision_happened=bool(g.get('response_revision_happened')),
                        web_used=bool(g['controlled_web'].get('triggered') and g['controlled_web'].get('ok')),
                        web_required=str(g['decision_core'].get('web_decision') or 'off') == 'required',
                        tool_successes=len([g['r'] for g['r'] in g['tool_results'] if g['r'].ok]),
                        tool_failures=len([g['r'] for g['r'] in g['tool_results'] if not g['r'].ok]),
                        correction_this_turn=bool(g['_pa_wb'] and getattr(g['_pa_wb'], 'speech_act', '') == 'correction'),
                    )
                    g['trace']['cognitive_writeback_happened'] = True
                    g['trace']['conversation_turn_count'] = g['_cs'].turn_count
                    g['trace']['user_model_sample_count'] = g['_um'].sample_count
                    g['trace']['user_model_length'] = g['_um'].preferred_answer_length
                    g['trace']['user_model_humour'] = g['_um'].preferred_humour
                    g['trace']['user_model_tech_depth'] = g['_um'].preferred_technical_depth
                    g['trace']['user_model_confidence'] = g['_um'].confidence
                    g['trace']['calibration_signals'] = list((g['_cal'] or {}).get('signals') or [])
                    # Persist last reflection summary for next-turn cognitive pack
                    try:
                        if g.get('post_reflection') and g['post_reflection'].get('reflection_summary'):
                            from aihub.db import append_event as _ae
                            _ae(g['turn'].user_id, 'cognitive.reflection_prior', {
                                'session_id': g['turn'].session_id,
                                'summary': str(g['post_reflection'].get('reflection_summary') or '')[:400],
                                'durable': False,
                            })
                    except Exception:
                        logger.debug('reflection prior persist skipped', exc_info=True)
                except Exception as cog_wb_err:
                    g['cog_wb_err'] = cog_wb_err
                    logger.debug('cognitive write-back skipped: %s', cog_wb_err, exc_info=True)
                    g['trace']['cognitive_writeback_happened'] = False
                # Adaptive learning pipeline (outcome → causal → lessons → metrics → self/user model)
                try:
                    from aihub.adaptive_learning import process_turn_learning, learning_trace_fields
                    g['_tid_learn'] = str(
                        getattr(getattr(self, '_active_turn_ctx', None), 'turn_id', None)
                        or getattr(g.get('ctx'), 'turn_id', None)
                        or (g.get('trace') or {}).get('turn_id')
                        or g.get('turn_id_for_wb')
                        or getattr(g['turn'], 'turn_id', None)
                        or ''
                    )
                    if not g['_tid_learn']:
                        import uuid as _uuid_learn
                        g['_tid_learn'] = str(_uuid_learn.uuid4())
                    g['_learn'] = process_turn_learning(
                        turn_id=g['_tid_learn'],
                        user_id=g['turn'].user_id,
                        session_id=g['turn'].session_id,
                        message=g['turn'].message or '',
                        response_text=g['response_text'] or '',
                        trace=g['trace'] if isinstance(g.get('trace'), dict) else {},
                        decision_core=g['decision_core'],
                        ok=len(g['errors']) == 0 and not g.get('fallback'),
                        errors=list(g.get('errors') or []),
                        replay_mode=False,
                    )
                    g['trace'].update(learning_trace_fields(g['_learn']))
                    if getattr(g['_learn'], 'long_horizon_task_id', None):
                        g['trace']['long_horizon_task_id'] = g['_learn'].long_horizon_task_id
                    g['trace']['strategy_learning_applied'] = bool(
                        g['decision_core'].get('self_model_influenced_strategy')
                        or g['decision_core'].get('learning_strategy_bias')
                    )
                    g['trace']['planner_learning_applied'] = any(
                        str(c).upper().startswith('LEARN_') and 'PLAN' in str(c).upper()
                        for c in (g['decision_core'].get('reason_codes') or [])
                    ) or bool(g['decision_core'].get('learning_planner_bias'))
                    g['trace']['provider_learning_applied'] = bool(g['decision_core'].get('provider_learning_preference'))
                    g['trace']['tool_learning_applied'] = 'LEARN_TOOL_ORDER_METRICS' in list(g['decision_core'].get('reason_codes') or [])
                    g['trace']['research_learning_applied'] = bool(g['trace'].get('research_query_variants'))
                    g['trace']['self_model_influenced_strategy'] = bool(g['decision_core'].get('self_model_influenced_strategy'))
                    # Honest cross-module influence (strategy → response when LLM completed)
                    for g['_ik'] in (
                        'simulation_affected_strategy',
                        'simulation_affected_response',
                        'policy_feedback_affected_strategy',
                        'policy_feedback_affected_response',
                        'cognitive_integration_happened',
                        'cognitive_integration_affected_strategy',
                        'cognitive_integration_affected_response',
                    ):
                        if g['_ik'] in g['decision_core']:
                            g['trace'][g['_ik']] = bool(g['decision_core'].get(g['_ik']))
                    g['trace']['confidence_raw'] = g['decision_core'].get('strategy_confidence_raw')
                    g['trace']['confidence_calibrated'] = g['decision_core'].get('strategy_confidence')
                    g['trace']['confidence_calibration_delta'] = g['decision_core'].get('confidence_calibration_delta')
                    if g['decision_core'].get('long_horizon_task_id'):
                        g['trace']['long_horizon_task_id'] = g['decision_core'].get('long_horizon_task_id')
                except Exception as learn_err:
                    logger.debug('adaptive learning write-back skipped: %s', learn_err, exc_info=True)
                    g['trace']['learning_degraded'] = True
                    g['trace']['outcome_evaluation_happened'] = False
                # Procedural extraction (value was previously API-only / never scheduled)
                try:
                    msg_l = (g['turn'].message or '').lower()
                    procedural_cue = any(
                        k in msg_l
                        for k in (
                            'zrób tak',
                            'zawsze',
                            'procedure',
                            'krok po kroku',
                            'workflow',
                            'sposób:',
                            'od teraz',
                        )
                    )
                    if procedural_cue or (
                        g['memory_v2_snapshot'].get('procedures_count', 0) == 0
                        and g['memory_v2_snapshot'].get('match_count', 0) >= 3
                    ):
                        g['_procs'] = get_memory_core().v2_extract_procedures(g['turn'].user_id)
                        g['trace']['procedural_extraction_ran'] = True
                        g['trace']['procedural_extraction_count'] = len(g['_procs'] or [])
                    else:
                        g['trace']['procedural_extraction_ran'] = False
                except Exception as proc_err:
                    logger.debug('procedural extraction skipped: %s', proc_err, exc_info=True)
                    g['trace']['procedural_extraction_ran'] = False
                # World knowledge write-back (claims/evidence/entities) + trace
                try:
                    from aihub.world_knowledge import process_turn_knowledge, knowledge_trace_fields
                    g['_tid_wk'] = str(
                        getattr(getattr(self, '_active_turn_ctx', None), 'turn_id', None)
                        or getattr(g.get('ctx'), 'turn_id', None)
                        or (g.get('trace') or {}).get('turn_id')
                        or g.get('_tid_learn')
                        or ''
                    )
                    g['_wk'] = process_turn_knowledge(
                        turn_id=g['_tid_wk'],
                        user_id=g['turn'].user_id,
                        session_id=g['turn'].session_id,
                        message=g['turn'].message or '',
                        response_text=g['response_text'] or '',
                        trace=g['trace'] if isinstance(g.get('trace'), dict) else {},
                        decision_core=g.get('decision_core') or {},
                        replay_mode=False,
                    )
                    g['trace'].update(knowledge_trace_fields(g['_wk']))
                    if g['decision_core'].get('graph_influenced_strategy'):
                        g['trace']['graph_influenced_strategy'] = True
                    if g['decision_core'].get('graph_influenced_planner'):
                        g['trace']['graph_influenced_planner'] = True
                    if g['decision_core'].get('execution_graph_id'):
                        g['trace']['execution_graph_id'] = g['decision_core'].get('execution_graph_id')
                    # Response influence: claims were injected into prompt (knowledge_decision present).
                    _kd = (g.get('ctx') and getattr(g['ctx'], 'system_context', None) or {})
                    _claims = ((g['decision_core'].get('knowledge_context') or {}).get('claims') or [])
                    g['trace']['graph_influenced_response'] = bool(
                        g['decision_core'].get('graph_influenced_strategy')
                        or (isinstance(_kd, dict) and _kd.get('knowledge_decision') and _claims)
                    )
                except Exception as wk_err:
                    logger.debug('world knowledge write-back skipped: %s', wk_err, exc_info=True)
                    g['trace']['knowledge_learning_degraded'] = True
            except Exception as v2_wb_error:
                g['v2_wb_error'] = v2_wb_error
                logger.warning(f'V2 chat write-back failed: {v2_wb_error}', exc_info=True)
                g['trace']['memory_v2_writeback_attempted'] = True
                g['trace']['psyche_v2_writeback_attempted'] = True
        self._write_back_experience(turn=g['turn'], response_text=g['response_text'], grounding_mode=g['grounding_mode'], tool_calls=g['tool_calls'], tool_results=g['tool_results'], trace=g['trace'], errors=g['errors'], psyche_snapshot=g['psyche_snapshot'], decision_core=g['decision_core'])
        if str(getattr(g['turn'], 'runtime_mode', '') or '').lower() == 'audit':
            g['trace']['psyche_snapshot_happened'] = False
            g['trace']['experience_write_back_attempted'] = False
            g['trace']['experience_write_back_succeeded'] = False
        self._run_runtime_experience_feedback(g['turn'].user_id, g['trace'])
        _cr_hook('append_event', append_event)(g['turn'].user_id, 'chat.turn', {'ok': len(g['errors']) == 0, 'provider': g['final_provider'], 'model': g['final_model'], 'trace': g['trace'], 'tool_calls': [g['tc'].model_dump() for g['tc'] in g['tool_calls']], 'tool_results': [g['tr'].model_dump() for g['tr'] in g['tool_results']]})
        g['result'] = ChatTurnResult(ok=len(g['errors']) == 0, response_text=g['response_text'], model=g['final_model'], provider=g['final_provider'], tool_calls=g['tool_calls'], tool_results=g['tool_results'], selected_mode=g['ctx'].mode, usage=self._sum_usage(g['provider_usages']), trace=g['trace'], errors=g['errors'], debug={'context': g['ctx'].model_dump()} if g['turn'].include_debug else None, attachments_summary=g['attachments_summary'])
        _TRACE_CACHE[g['turn'].user_id].append(g['result'].trace)
        g['__result__'] = g['result']
        return

