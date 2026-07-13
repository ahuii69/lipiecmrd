"""Extracted body of DecisionMixin._pre_exec_decision_core."""
from __future__ import annotations

import aihub.turn._ops_ns as _ops_ns
globals().update({k: v for k, v in vars(_ops_ns).items() if not k.startswith('__')})
TurnOps = None  # late-bound

def run_pre_exec_decision_core(self, *, turn: ChatTurnInput, ctx: ChatTurnContext, psyche_snapshot: dict[str, Any], memory_v2_runtime_ctx: Any=None, psyche_v2_behavior_ctx: Any=None) -> dict[str, Any]:
    """Run strategy selection, simulation, policy build and consistency check
    BEFORE the provider call. Outputs drive tool filtering, system prompt
    injection and the full trace."""
    result: dict[str, Any] = {'selected_strategy': 'instant', 'reason_codes': [], 'strategy_confidence': None, 'strategy_degraded': False, 'selected_goal': None, 'simulation_ran': False, 'simulation_best_action': None, 'simulation_variants_count': 0, 'simulation_risk_summary': None, 'policy_hints_loaded': False, 'policy_profile_name': None, 'policy_hints': [], 'consistency_check_ran': False, 'consistency_classification': None, 'contradictions_found': 0, 'strategy_hints': '', 'experience_lookup_happened': False, 'experience_matches_count': 0, 'experience_influenced_strategy': False, 'experience_confidence_adjustment': None, 'experience_handoff_bias': None, 'experience_blocker_reason': None, 'experience_blocker_severity': None, 'experience_recurring_failure_detected': False, 'experience_recurring_failure_types': [], 'experience_signal_summary': 'not_evaluated', 'experience_action_bias': {}, 'web_decision': 'off', 'web_decision_reason': 'not_evaluated', 'selector_output_snapshot': {}, 'strategy_short_explanation': '', 'strategy_selected': {}, 'execution_mode': 'direct', 'escalation_path': {}, 'escalation_final_mode': 'direct', 'escalation_use_reasoning': False, 'escalation_use_tools': False}
    try:
        from aihub.vault.service import classify_vault_intent
        result['vault_intent'] = classify_vault_intent(turn.message or '')
    except Exception:
        result['vault_intent'] = None
    try:
        from aihub.goal_engine import get_goal_engine
        from aihub.strategy_selector import select_strategy
        try:
            _active_goals = get_goal_engine().get_active_goals(turn.user_id)
            if _active_goals:
                _max_urgency = max((g.urgency for g in _active_goals))
                _active_goals_summary: dict | None = {'active_count': len(_active_goals), 'max_urgency': _max_urgency}
                _top = max(_active_goals, key=lambda g: g.urgency)
                result['selected_goal'] = {'goal_id': _top.goal_id, 'title': _top.title, 'urgency': _top.urgency}
            else:
                _active_goals_summary = None
        except Exception:
            logger.debug('Decision core: active goals lookup failed', exc_info=True)
            _active_goals_summary = None
        selection = select_strategy(user_id=turn.user_id, user_text=turn.message or '', mode=ctx.mode, active_goals_summary=_active_goals_summary, history=list(turn.history or []))
        result['selected_strategy'] = selection.selected_strategy
        result['reason_codes'] = list(selection.reason_codes)
        result['strategy_confidence'] = selection.confidence
        result['strategy_degraded'] = selection.degraded
        result['selector_output_snapshot'] = dict(selection.selector_output)
        result['strategy_short_explanation'] = selection.short_explanation or ''
        result['web_decision'] = selection.web_decision
        result['web_decision_reason'] = selection.web_decision_reason
    except Exception:
        logger.debug('Decision core: strategy selection failed', exc_info=True)
        result['reason_codes'] = ['SELECTOR_TIMEOUT_FALLBACK']
        result['strategy_degraded'] = True
    try:
        experience_signal = self._lookup_experience_signal(user_id=turn.user_id, message=turn.message or '', selected_strategy=result['selected_strategy'])
        result['experience_lookup_happened'] = bool(experience_signal.get('lookup_happened', False))
        result['experience_matches_count'] = int(experience_signal.get('matches_count', 0) or 0)
        result['experience_confidence_adjustment'] = experience_signal.get('confidence_adjustment')
        result['experience_handoff_bias'] = experience_signal.get('handoff_bias')
        result['experience_blocker_reason'] = experience_signal.get('blocker_reason')
        result['experience_blocker_severity'] = experience_signal.get('blocker_severity')
        result['experience_recurring_failure_detected'] = bool(experience_signal.get('recurring_failure_detected', False))
        result['experience_recurring_failure_types'] = list(experience_signal.get('recurring_failure_types') or [])
        result['experience_signal_summary'] = str(experience_signal.get('experience_signal_summary') or 'not_evaluated')
        result['experience_action_bias'] = dict(experience_signal.get('action_bias') or {})
        result['deliberation_history'] = experience_signal.get('deliberation_history') or {}
        recommended = experience_signal.get('recommended_strategy')
        if isinstance(recommended, str) and recommended and (recommended != result['selected_strategy']):
            result['selected_strategy'] = recommended
            result['experience_influenced_strategy'] = True
            result['reason_codes'].append('EXPERIENCE_STRATEGY_BIAS')
        conf_adjust = experience_signal.get('confidence_adjustment')
        if isinstance(conf_adjust, (int, float)):
            base_conf = float(result.get('strategy_confidence') or 0.7)
            result['strategy_confidence'] = round(max(0.3, min(0.95, base_conf + float(conf_adjust))), 3)
            if abs(float(conf_adjust)) >= 0.03:
                result['reason_codes'].append('EXPERIENCE_CONFIDENCE_ADJUST')
        blocker_reason = experience_signal.get('blocker_reason')
        blocker_severity = float(experience_signal.get('blocker_severity') or 0.0)
        if blocker_reason and (not skip_experience_blocker_escalation(turn.message or '')):
            result['reason_codes'].append('EXPERIENCE_CAUTION')
            caution = f'[Experience caution: {blocker_reason}]'
            result['strategy_hints'] = (result['strategy_hints'] + ' ' + caution).strip() if result['strategy_hints'] else caution
            if result['selected_strategy'] == 'instant' and blocker_severity >= 0.6:
                result['selected_strategy'] = 'contextual'
                result['experience_influenced_strategy'] = True
                result['reason_codes'].append('EXPERIENCE_BLOCKER_CONTEXTUAL_UPGRADE')
    except Exception:
        logger.debug('Decision core: experience signal failed', exc_info=True)
    try:
        from aihub.runtime_memory_bridge import build_memory_v2_runtime_snapshot
        from aihub.runtime_psyche_bridge import build_psyche_v2_runtime_snapshot
        _mctx = memory_v2_runtime_ctx
        _pctx = psyche_v2_behavior_ctx
        if _mctx is not None and getattr(_mctx, 'loaded', False):
            memory_v2_actionable_contradictions = len(_mctx.contradiction_alerts)
            memory_v2_contradictions_count = memory_v2_actionable_contradictions + len(_mctx.transient_contradiction_hints)
            memory_v2_match_count = len(_mctx.top_facts) + len(_mctx.top_preferences)
        else:
            memory_v2_snapshot = build_memory_v2_runtime_snapshot(turn.user_id, turn.message or '')
            memory_v2_contradictions_count = memory_v2_snapshot.get('contradictions_count', 0)
            memory_v2_actionable_contradictions = memory_v2_snapshot.get('actionable_contradictions_count', memory_v2_contradictions_count)
            memory_v2_match_count = memory_v2_snapshot.get('match_count', 0)
        if _pctx is not None and getattr(_pctx, 'loaded', False):
            psyche_v2_mode = _pctx.mode
            psyche_v2_relation_trust = float(_pctx.trust)
        else:
            psyche_v2_snapshot = build_psyche_v2_runtime_snapshot(turn.user_id)
            psyche_v2_mode = psyche_v2_snapshot.get('mode', 'neutral')
            psyche_v2_relation_trust = psyche_v2_snapshot.get('relation_trust', 0.5)
        memory_influenced_strategy = False
        psyche_influenced_strategy = False
        if memory_v2_actionable_contradictions > 0 and result['selected_strategy'] == 'instant':
            result['selected_strategy'] = 'contextual'
            result['reason_codes'].append('MEMORY_V2_CONTRADICTIONS')
            memory_influenced_strategy = True
            logger.info(f'V2: contradictions → contextual (user={turn.user_id})')
        if memory_v2_match_count > 0:
            base_conf = float(result.get('strategy_confidence') or 0.7)
            result['strategy_confidence'] = min(0.95, base_conf + 0.1)
            result['reason_codes'].append('MEMORY_V2_CONTEXT_BOOST')
        if psyche_v2_mode == 'exploratory' and result['selected_strategy'] == 'instant':
            result['selected_strategy'] = 'contextual'
            result['reason_codes'].append('PSYCHE_V2_EXPLORATORY')
            psyche_influenced_strategy = True
            logger.info(f'V2: exploratory mode → contextual (user={turn.user_id})')
        if psyche_v2_mode == 'cautious':
            base_conf = float(result.get('strategy_confidence') or 0.7)
            result['strategy_confidence'] = max(0.3, base_conf - 0.15)
            result['reason_codes'].append('PSYCHE_V2_CAUTIOUS')
        if psyche_v2_relation_trust < 0.3:
            base_conf = float(result.get('strategy_confidence') or 0.7)
            result['strategy_confidence'] = max(0.3, base_conf - 0.1)
            result['reason_codes'].append('PSYCHE_V2_LOW_TRUST')
        if _pctx is not None and getattr(_pctx, 'loaded', False):
            cd = getattr(_pctx, 'consistency_decision', 'allow')
            if cd in ('dampen', 'suppress'):
                fc = float(result.get('strategy_confidence') or 0.7)
                drop = 0.065 if cd == 'suppress' else 0.038
                result['strategy_confidence'] = max(0.33, fc - drop)
                result['reason_codes'].append(f'SELF_CONSISTENCY_CONF_{str(cd).upper()}')
        result['memory_influenced_strategy_chat'] = memory_influenced_strategy
        result['psyche_influenced_strategy_chat'] = psyche_influenced_strategy
    except Exception as v2_error:
        logger.debug(f'Decision core: V2 influence failed: {v2_error}', exc_info=True)
    try:
        from aihub.policy_engine import build_policy_profile, compute_policy_feedback
        profile = build_policy_profile(turn.user_id, window=50)
        result['policy_hints_loaded'] = True
        result['policy_profile_name'] = f'rel={profile.reliability_index:.2f}_refs={profile.total_reflections}'
        result['policy_hints'] = profile.hints
        actionable = [h for h in profile.hints[:5] if h.signal in ('boost', 'penalize', 'avoid')]
        if actionable:
            hints_text = '; '.join((f'{h.action_type}={h.signal}' for h in actionable))
            result['strategy_hints'] = f'[Policy z historii: {hints_text}. Reliability={profile.reliability_index:.2f}]'
        feedback = compute_policy_feedback(profile)
        result['policy_feedback'] = feedback
        result['policy_feedback_applied'] = feedback.applied
        result['policy_feedback_loaded'] = True
        result['policy_confidence_delta'] = feedback.confidence_delta
        result['policy_feedback_summary'] = feedback.summary or ''
        if feedback.applied:
            if abs(feedback.confidence_delta) >= 0.005:
                base_conf = float(result.get('strategy_confidence') or 0.7)
                new_conf = round(max(0.2, min(0.95, base_conf + feedback.confidence_delta)), 3)
                if new_conf != base_conf:
                    result['strategy_confidence'] = new_conf
                    result['reason_codes'].append('POLICY_FEEDBACK_CONFIDENCE')
            if abs(feedback.handoff_bias) >= 0.01:
                existing_bias = float(result.get('experience_handoff_bias') or 0.0)
                result['policy_handoff_bias'] = round(max(-0.5, min(0.5, existing_bias + feedback.handoff_bias)), 4)
                result['reason_codes'].append('POLICY_FEEDBACK_HANDOFF')
            else:
                result['policy_handoff_bias'] = float(result.get('experience_handoff_bias') or 0.0)
            if abs(feedback.blocker_sensitivity) >= 0.01:
                result['policy_blocker_sensitivity'] = feedback.blocker_sensitivity
                result['reason_codes'].append('POLICY_FEEDBACK_BLOCKER')
            else:
                result['policy_blocker_sensitivity'] = 0.0
            result['policy_simulation_risk_cal'] = feedback.simulation_risk_calibration
            if feedback.strategy_adjustments:
                result['policy_strategy_adjustments'] = dict(feedback.strategy_adjustments)
                result['reason_codes'].append('POLICY_FEEDBACK_STRATEGY')
                _S2A_FB = {'instant': 'reason', 'contextual': 'memory_search', 'research': 'research', 'agentic': 'action'}
                _A2S_FB = {v: k for k, v in _S2A_FB.items()}
                cur_action = _S2A_FB.get(result['selected_strategy'], 'reason')
                cur_delta = feedback.strategy_adjustments.get(cur_action, 0.0)
                if cur_delta <= -0.15:
                    best_alt = max(((act, d) for act, d in feedback.strategy_adjustments.items() if act != cur_action and d > 0), key=lambda x: x[1], default=(None, 0.0))
                    if best_alt[0] and best_alt[0] in _A2S_FB:
                        result['selected_strategy'] = _A2S_FB[best_alt[0]]
                        result['reason_codes'].append(f'POLICY_STRATEGY_SHIFT:{cur_action}->{best_alt[0]}')
            else:
                result['policy_strategy_adjustments'] = {}
        else:
            result['policy_feedback'] = feedback
            result['policy_feedback_applied'] = False
            result['policy_confidence_delta'] = 0.0
            result['policy_feedback_summary'] = ''
            result['policy_handoff_bias'] = float(result.get('experience_handoff_bias') or 0.0)
            result['policy_blocker_sensitivity'] = 0.0
            result['policy_simulation_risk_cal'] = 0.0
            result['policy_strategy_adjustments'] = {}
    except Exception:
        logger.debug('Decision core: policy profile failed', exc_info=True)
    _STRATEGY_TO_ACTION: dict[str, str] = {'instant': 'reason', 'contextual': 'memory_search', 'research': 'research', 'agentic': 'action'}
    try:
        from aihub.simulation_engine import simulate_action
        strategy = result['selected_strategy']
        action_type = _STRATEGY_TO_ACTION.get(strategy, 'reason')
        _psyche_compact: dict[str, Any] = {}
        if psyche_snapshot:
            _psyche_compact = {'energy': float(psyche_snapshot.get('energy', 0.7)), 'focus': float(psyche_snapshot.get('focus', 0.65)), 'mood': float(psyche_snapshot.get('mood', 0.5))}
        sim_context = {'policy_hints': [{'action_type': h.action_type, 'signal': h.signal, 'weight': h.weight} for h in result['policy_hints'][:5]], 'web_triggered': result['web_decision'] != 'off', 'mode': ctx.mode, 'psyche_state': _psyche_compact, 'experience_signal': {'action_bias': result.get('experience_action_bias', {}), 'blocker_reason': result.get('experience_blocker_reason'), 'summary': result.get('experience_signal_summary')}, 'risk_calibration': float(result.get('policy_simulation_risk_cal') or 0.0)}
        sim_result = simulate_action(turn.user_id, action_type, {'message': (turn.message or '')[:200]}, sim_context, max_variants=4)
        result['simulation_ran'] = True
        result['simulation_variants_count'] = sim_result.variants_evaluated
        if sim_result.best_variant:
            bv = sim_result.best_variant
            _risk_cal = float(result.get('policy_simulation_risk_cal') or 0.0)
            calibrated_risk = max(0.0, min(1.0, bv.risk + _risk_cal))
            result['simulation_best_action'] = bv.action_type
            result['simulation_risk_summary'] = f'risk={calibrated_risk:.2f} conf={bv.confidence:.2f} util={bv.utility:.2f}'
            _ACTION_TO_STRATEGY: dict[str, str] = {'memory_search': 'contextual', 'research': 'research', 'action': 'agentic', 'reason': 'instant', 'web_request': 'research'}
            sim_suggested = _ACTION_TO_STRATEGY.get(bv.action_type)
            _current = result['selected_strategy']
            if sim_suggested and sim_suggested != _current and (bv.composite_score >= 0.72) and (bv.confidence >= 0.6) and (not result.get('strategy_degraded')):
                result['selected_strategy'] = sim_suggested
                result['reason_codes'].append('SIMULATION_OVERRIDE')
                result['strategy_confidence'] = round(bv.composite_score, 3)
    except Exception:
        logger.debug('Decision core: simulation failed', exc_info=True)
    try:
        from aihub.consistency_engine import check_consistency
        verdict = check_consistency(turn.user_id, turn.message or '')
        result['consistency_check_ran'] = True
        result['consistency_classification'] = verdict.classification
        if verdict.classification == 'conflict':
            result['contradictions_found'] = 1
            result['reason_codes'].append('CONSISTENCY_CONFLICT')
            _conf = result.get('strategy_confidence') or 0.7
            result['strategy_confidence'] = round(max(0.35, _conf * 0.8), 3)
            _strat = result['selected_strategy']
            if _strat == 'instant':
                result['selected_strategy'] = 'contextual'
                result['reason_codes'].append('CONSISTENCY_FORCED_CONTEXTUAL')
            note = '[Spójność: potencjalna sprzeczność — strategia upgraded, confidence −20%]'
            result['strategy_hints'] = (result['strategy_hints'] + ' ' + note).strip() if result['strategy_hints'] else note
        elif verdict.classification in ('revision', 'uncertain'):
            result['reason_codes'].append(f'CONSISTENCY_{verdict.classification.upper()}')
            _conf = result.get('strategy_confidence') or 0.7
            result['strategy_confidence'] = round(max(0.4, _conf * 0.93), 3)
    except Exception:
        logger.debug('Decision core: consistency check failed', exc_info=True)
    self._local_non_research_guardrails(turn, result)
    self._finalize_escalation(result)
    result['user_turn_text'] = turn.message or ''
    return result
