"""Extracted body of ExecutionMixin._execute_agent_handoff."""
from __future__ import annotations

import aihub.turn._ops_ns as _ops_ns
globals().update({k: v for k, v in vars(_ops_ns).items() if not k.startswith('__')})
TurnOps = None  # late-bound

async def run_execute_agent_handoff(self, *, turn: ChatTurnInput, decision_core: dict[str, Any], handoff_reason: str, started: float, psyche_snapshot: dict[str, Any], memory_used_trace: list[dict[str, Any]] | None=None, memory_lookup_flag: bool=False, blocker_verdict: BlockerVerdict | None=None, memory_context: dict[str, Any] | None=None, ctx: ChatTurnContext | None=None) -> ChatTurnResult:
    """Execute controlled handoff to agent runtime and normalize to ChatTurnResult."""
    errors: list[dict[str, Any]] = []
    try:
        if stream_session_active():
            await emit_status('tools', label_pl='Analizuję…')
        controller = _cr_hook('get_executive_controller', get_executive_controller)()
        fstr, freason = map_chat_execution_mode_to_force_strategy(decision_core)
        cycle = await controller.run_cycle({'text': turn.message, 'max_steps': 8, 'timeout_seconds': 20.0, 'force_strategy': fstr, 'force_strategy_reason': f'{freason};chat_runtime:agent_handoff'}, mode='run', user_id=turn.user_id)
        agent_response = _cr_hook('build_agent_cycle_response', build_agent_cycle_response)(cycle, include_debug=turn.include_debug)
    except Exception as exc:
        logger.error('Agent handoff failed user=%s error=%s', turn.user_id, exc)
        errors.append({'type': 'agent_handoff_error', 'error': str(exc), 'handoff_reason': handoff_reason})
        handoff_err_trace = {'provider_calls': 0, 'tool_iterations': 0, 'used_tools': False, 'used_fallback': False, 'response_grounding_mode': 'agent_handoff_error', 'duration_ms': (time.monotonic() - started) * 1000.0, 'provider': 'executive_controller', 'model': 'planner+reasoning', 'agent_handoff_triggered': True, 'agent_handoff_reason': handoff_reason, 'agent_handoff_error': str(exc), 'effective_runtime_path': 'agent_handoff_error', **TurnOps._decision_core_trace_escalation(decision_core), 'experience_lookup_happened': decision_core.get('experience_lookup_happened', False), 'experience_matches_count': decision_core.get('experience_matches_count', 0), 'experience_influenced_strategy': decision_core.get('experience_influenced_strategy', False), 'experience_confidence_adjustment': decision_core.get('experience_confidence_adjustment'), 'experience_handoff_bias': decision_core.get('experience_handoff_bias'), 'experience_blocker_reason': decision_core.get('experience_blocker_reason'), 'experience_signal_summary': decision_core.get('experience_signal_summary'), 'policy_feedback_loaded': bool(decision_core.get('policy_feedback_loaded')), 'policy_feedback_applied': bool(decision_core.get('policy_feedback_applied')), 'policy_feedback_summary': decision_core.get('policy_feedback_summary', ''), 'policy_confidence_delta': decision_core.get('policy_confidence_delta', 0.0), 'policy_handoff_bias': decision_core.get('policy_handoff_bias', 0.0), 'policy_blocker_sensitivity': decision_core.get('policy_blocker_sensitivity', 0.0), 'policy_simulation_risk_cal': decision_core.get('policy_simulation_risk_cal', 0.0), 'policy_strategy_adjustments': decision_core.get('policy_strategy_adjustments', {}), 'selected_strategy': decision_core['selected_strategy'], 'reason_codes': list(decision_core.get('reason_codes') or []), 'strategy_confidence': decision_core.get('strategy_confidence'), 'degraded': True, 'memory_lookup_happened': memory_lookup_flag, 'psyche_snapshot_happened': False, 'research_was_required': str(decision_core.get('web_decision') or 'off') == 'required', 'agentic_executed': True, 'tool_calls_count': 0, 'experience_write_back_attempted': False, 'experience_write_back_succeeded': False, **self._correction_trace_fields(ctx)}
        if memory_used_trace:
            handoff_err_trace['memory_used'] = memory_used_trace
        self._augment_memory_observability(handoff_err_trace, memory_used_trace, memory_context)
        handoff_err_trace['chat_handoff_evaluated'] = True
        handoff_err_trace['chat_handoff_executed'] = False
        handoff_err_trace['chat_handoff_skip_reason'] = 'agent_handoff_error'
        trace_blocker_gate_outcome(handoff_err_trace, gate_evaluated=True, hard_applied=False)
        merge_canonical_decision_trace(handoff_err_trace, selected_route=ROUTE_AGENT_HANDOFF_ERROR, route_reason='agent_handoff_infrastructure_error', decision_intent='plan', deterministic_hit=False, vault_used=False, memory_retrieval_used=bool(memory_used_trace), web_required=str(decision_core.get('web_decision') or 'off') == 'required', planner_used=False, blocker_hard=False)
        _handoff_err_msg = 'Plan/agent się wywalił po mojej stronie (tak, wiem, klasyk) — daj mu drugą szansę za moment albo uprość pytanie.'
        self._write_back_experience(turn=turn, response_text=_handoff_err_msg, grounding_mode='agent_handoff_error', tool_calls=[], tool_results=[], trace=handoff_err_trace, errors=errors, psyche_snapshot=psyche_snapshot, decision_core=decision_core)
        if str(getattr(turn, 'runtime_mode', '') or '').lower() == 'audit':
            handoff_err_trace['psyche_snapshot_happened'] = False
            handoff_err_trace['experience_write_back_attempted'] = False
            handoff_err_trace['experience_write_back_succeeded'] = False
        self._run_runtime_experience_feedback(turn.user_id, handoff_err_trace)
        return ChatTurnResult(ok=False, response_text=_handoff_err_msg, model='agent_runtime', provider='executive_controller', tool_calls=[], tool_results=[], selected_mode=turn.mode or CHAT_DEFAULT_MODE, usage=ProviderUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0), trace=handoff_err_trace, errors=errors, debug=None)
    exec_summary = agent_response.get('execution_summary', {})
    action_summary = exec_summary.get('action_summary', '')
    agent_errors = agent_response.get('errors', [])
    agent_trace = agent_response.get('trace', {})
    user_rt = (agent_response.get('response_text') or '').strip()
    mode = turn.mode or CHAT_DEFAULT_MODE
    if mode in ('agent', 'debug'):
        response_text = user_rt or action_summary or 'Wykonałem zadanie przez agent runtime (planner+reasoning).'
    else:
        response_text = synthesize_chat_handoff_user_text(user_message=turn.message, internal_reply=user_rt, action_summary=str(action_summary or ''), cycle=cycle, agent_ok=bool(agent_response.get('ok', False)))
    duration_ms = (time.monotonic() - started) * 1000.0
    trace = {'provider_calls': 0, 'tool_iterations': 0, 'tool_calls_requested': 0, 'tool_calls_executed': 0, 'tool_calls_successful': 0, 'tool_failures': 0, 'used_tools': False, 'used_fallback': False, 'response_grounding_mode': 'agent_handoff', 'duration_ms': duration_ms, **self._correction_trace_fields(ctx), 'provider': 'executive_controller', 'model': 'planner+reasoning', 'selected_strategy': decision_core['selected_strategy'], **self._decision_core_trace_escalation(decision_core), 'reason_codes': decision_core['reason_codes'], 'strategy_confidence': decision_core['strategy_confidence'], 'degraded': decision_core['strategy_degraded'], 'selected_goal': decision_core.get('selected_goal'), 'agent_handoff_triggered': True, 'agent_handoff_reason': handoff_reason, 'effective_runtime_path': 'agent_handoff', 'advisory_strategy': decision_core['selected_strategy'], 'planner_executed': agent_response.get('planning_used', False), 'reasoning_executed': agent_response.get('reasoning_used', False), 'agent_cycle_id': agent_trace.get('cycle_id', ''), 'agent_executed_task_ids': agent_trace.get('executed_task_ids', []), 'agent_runtime_generated_task_ids': agent_trace.get('runtime_generated_task_ids', []), 'agent_steps_executed': exec_summary.get('steps_executed', 0), 'simulation_ran': decision_core['simulation_ran'], 'simulation_best_action': decision_core['simulation_best_action'], 'simulation_variants_count': decision_core['simulation_variants_count'], 'simulation_risk_summary': decision_core['simulation_risk_summary'], 'policy_hints_loaded': decision_core['policy_hints_loaded'], 'policy_profile_name': decision_core['policy_profile_name'], 'consistency_check_ran': decision_core['consistency_check_ran'], 'consistency_classification': decision_core['consistency_classification'], 'contradictions_found': decision_core['contradictions_found'], 'experience_lookup_happened': decision_core.get('experience_lookup_happened', False), 'experience_matches_count': decision_core.get('experience_matches_count', 0), 'experience_influenced_strategy': decision_core.get('experience_influenced_strategy', False), 'experience_confidence_adjustment': decision_core.get('experience_confidence_adjustment'), 'experience_handoff_bias': decision_core.get('experience_handoff_bias'), 'experience_blocker_reason': decision_core.get('experience_blocker_reason'), 'experience_signal_summary': decision_core.get('experience_signal_summary'), 'memory_lookup_happened': memory_lookup_flag, 'psyche_snapshot_happened': False, 'research_was_required': str(decision_core.get('web_decision') or 'off') == 'required', 'experience_write_back_attempted': False, 'experience_write_back_succeeded': False, 'agentic_executed': True, 'tool_calls_count': int(exec_summary.get('steps_executed') or 0), 'controlled_web_decision': decision_core.get('web_decision', 'off'), 'controlled_web_decision_reason': decision_core.get('web_decision_reason', 'not_evaluated'), 'controlled_web_triggered': False, 'controlled_web_reason': 'agent_handoff', 'controlled_web_tool': None, 'controlled_web_ok': None, 'controlled_web_has_results': None, 'controlled_web_provider_info': None, 'controlled_web_query': None, 'controlled_web_source_count': 0, 'controlled_web_freshness_needed': self._is_freshness_needed(decision_core.get('reason_codes', [])), 'reflection_ran': False, 'reflection_summary': None, 'policy_feedback_loaded': bool(decision_core.get('policy_feedback_loaded')), 'policy_feedback_applied': bool(decision_core.get('policy_feedback_applied')), 'policy_feedback_summary': decision_core.get('policy_feedback_summary', ''), 'policy_confidence_delta': decision_core.get('policy_confidence_delta', 0.0), 'policy_handoff_bias': decision_core.get('policy_handoff_bias', 0.0), 'policy_blocker_sensitivity': decision_core.get('policy_blocker_sensitivity', 0.0), 'policy_simulation_risk_cal': decision_core.get('policy_simulation_risk_cal', 0.0), 'policy_strategy_adjustments': decision_core.get('policy_strategy_adjustments', {})}
    if isinstance(agent_response, dict):
        if 'strategy_source' in agent_response:
            trace['strategy_source'] = agent_response['strategy_source']
        if 'strategy_authority_external' in agent_response:
            trace['strategy_authority_external'] = bool(agent_response['strategy_authority_external'])
        _exec_strat = agent_response.get('strategy')
        if _exec_strat is not None and str(_exec_strat).strip():
            trace['executive_strategy'] = str(_exec_strat)
    if memory_used_trace:
        trace['memory_used'] = memory_used_trace
    self._augment_memory_observability(trace, memory_used_trace, memory_context)
    trace['chat_handoff_evaluated'] = True
    trace['chat_handoff_executed'] = True
    trace['chat_handoff_skip_reason'] = None
    trace_blocker_gate_outcome(trace, gate_evaluated=True, hard_applied=False)
    _planning_used = bool(agent_response.get('planning_used', False))
    _bv_snap = blocker_verdict.model_dump() if blocker_verdict is not None else BlockerVerdict.allow().model_dump()
    merge_canonical_executive_handoff_success(trace, decision_core=decision_core, memory_retrieval_used=bool(memory_used_trace), planning_used=_planning_used, blocker_verdict_snapshot=_bv_snap)
    trace['agent_internal_response_text'] = user_rt or None
    trace['chat_handoff_synthesized'] = mode not in ('agent', 'debug')
    for err in agent_errors:
        errors.append({'type': 'agent_cycle_error', **err})
    self._write_back_experience(turn=turn, response_text=response_text, grounding_mode='agent_handoff', tool_calls=[], tool_results=[], trace=trace, errors=errors, psyche_snapshot=psyche_snapshot, decision_core=decision_core)
    if str(getattr(turn, 'runtime_mode', '') or '').lower() == 'audit':
        trace['psyche_snapshot_happened'] = False
        trace['experience_write_back_attempted'] = False
        trace['experience_write_back_succeeded'] = False
    self._run_runtime_experience_feedback(turn.user_id, trace)
    result = ChatTurnResult(ok=agent_response.get('ok', False) and len(errors) == 0, response_text=response_text, model='planner+reasoning', provider='executive_controller', tool_calls=[], tool_results=[], selected_mode=turn.mode or CHAT_DEFAULT_MODE, usage=ProviderUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0), trace=trace, errors=errors, debug={'agent_response': agent_response} if turn.include_debug else None)
    _cr_hook('append_event', append_event)(turn.user_id, 'chat.turn', {'ok': result.ok, 'provider': 'executive_controller', 'model': 'planner+reasoning', 'trace': result.trace, 'agent_handoff': True})
    _TRACE_CACHE[turn.user_id].append(result.trace)
    return result
