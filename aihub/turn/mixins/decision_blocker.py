"""Extracted body of DecisionMixin._evaluate_blocker_verdict."""
from __future__ import annotations

import aihub.turn._ops_ns as _ops_ns
globals().update({k: v for k, v in vars(_ops_ns).items() if not k.startswith('__')})
TurnOps = None  # late-bound

def run_evaluate_blocker_verdict(decision_core: dict[str, Any]) -> BlockerVerdict:
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
    _user_turn_for_block = str(decision_core.get('user_turn_text') or '')
    if is_image_generation_intent(_user_turn_for_block):
        return BlockerVerdict()
    consistency_class = decision_core.get('consistency_classification') or ''
    contradictions = int(decision_core.get('contradictions_found') or 0)
    confidence = float(decision_core.get('strategy_confidence') or 0.7)
    degraded = bool(decision_core.get('strategy_degraded'))
    selected_strategy = str(decision_core.get('selected_strategy') or 'instant')
    exp_blocker = decision_core.get('experience_blocker_reason') or ''
    exp_severity = float(decision_core.get('experience_blocker_severity') or 0.0)
    exp_recurring_types: list[str] = list(decision_core.get('experience_recurring_failure_types') or [])
    exp_recurring = bool(decision_core.get('experience_recurring_failure_detected', False))
    sim_risk_raw = decision_core.get('simulation_risk_summary') or ''
    sim_ran = bool(decision_core.get('simulation_ran'))
    policy_hints: list[dict[str, Any]] = list(decision_core.get('policy_hints') or [])
    policy_profile_name = decision_core.get('policy_profile_name') or ''
    skip_exp = skip_experience_blocker_escalation(str(decision_core.get('user_turn_text') or ''))
    sim_risk = 0.0
    sim_confidence = 0.0
    if sim_ran and 'risk=' in sim_risk_raw:
        try:
            sim_risk = float(sim_risk_raw.split('risk=')[1].split()[0])
        except (ValueError, IndexError) as exc:
            logger.debug('Simulation risk parsing failed: %s', exc)
        try:
            sim_confidence = float(sim_risk_raw.split('conf=')[1].split()[0])
        except (ValueError, IndexError) as exc:
            logger.debug('Simulation risk parsing failed: %s', exc)
    _STRATEGY_TO_ACTION: dict[str, str] = {'instant': 'reason', 'contextual': 'memory_search', 'research': 'research', 'agentic': 'action'}
    current_action_type = _STRATEGY_TO_ACTION.get(selected_strategy, 'reason')
    policy_avoid_weight = 0.0
    policy_penalize_weight = 0.0
    policy_boost_weight = 0.0
    policy_avoid_reason = ''
    for hint in policy_hints:
        h_action = ''
        h_signal = ''
        h_weight = 0.0
        h_reason = ''
        if hasattr(hint, 'action_type'):
            h_action = hint.action_type
            h_signal = hint.signal
            h_weight = hint.weight
            h_reason = getattr(hint, 'reason', '')
        elif isinstance(hint, dict):
            h_action = str(hint.get('action_type') or '')
            h_signal = str(hint.get('signal') or '')
            h_weight = float(hint.get('weight') or 0.0)
            h_reason = str(hint.get('reason') or '')
        if h_action != current_action_type:
            continue
        if h_signal == 'avoid':
            policy_avoid_weight = max(policy_avoid_weight, h_weight)
            policy_avoid_reason = h_reason
        elif h_signal == 'penalize':
            policy_penalize_weight = max(policy_penalize_weight, h_weight)
        elif h_signal == 'boost':
            policy_boost_weight = max(policy_boost_weight, h_weight)
    candidates: list[tuple[int, int, BlockerVerdict]] = []
    all_signals: list[str] = []
    _blocker_sens = float(decision_core.get('policy_blocker_sensitivity') or 0.0)
    _blocker_sens = max(-0.15, min(0.15, _blocker_sens))
    _conf_hard_thresh = 0.4 + _blocker_sens
    _conf_caution_thresh = 0.45 + _blocker_sens
    _sev_hard_thresh = 0.8 - _blocker_sens
    _risk_hard_thresh = 0.8 - _blocker_sens
    _risk_caution_thresh = 0.65 - _blocker_sens
    if consistency_class == 'conflict' and contradictions >= 1 and (confidence < _conf_hard_thresh):
        sigs = ['consistency_classification', 'contradictions_found', 'strategy_confidence']
        all_signals.extend(sigs)
        candidates.append((0, 3, BlockerVerdict(blocker_active=True, blocker_type='consistency_conflict', blocker_scope='turn', blocker_severity='hard', hard=True, resolution='hard_block', reason=f'Sprzeczność w wypowiedzi (confidence={confidence:.2f}). Wymagane wyjaśnienie.', source='consistency_engine', recommended_action='Przeformułuj pytanie eliminując sprzeczne stwierdzenia.', contributing_signals=sigs, confidence=min(1.0, 1.0 - confidence), user_message='Jest sprzeczność w treści — doprecyzuj krótko, o co chodzi.', dev_message=f'consistency_conflict: class={consistency_class} contradictions={contradictions} conf={confidence:.3f}', remediation_hint='Wyjaśnij sprzeczne stwierdzenia w pytaniu.')))
    if exp_blocker and exp_severity >= _sev_hard_thresh and (not skip_exp):
        sigs = ['experience_blocker_reason', 'experience_blocker_severity']
        all_signals.extend(sigs)
        candidates.append((0, 3, BlockerVerdict(blocker_active=True, blocker_type='repeated_failure', blocker_scope='turn', blocker_severity='hard', hard=True, resolution='hard_block', reason=f'Krytyczny wzorzec porażek: {exp_blocker} (severity={exp_severity:.2f}).', source='experience_memory', recommended_action='Zmień podejście lub potwierdź kontynuację.', contributing_signals=sigs, confidence=exp_severity, user_message='Podobne tury wcześniej się wyłożyły — zmień pytanie albo potwierdź, że jedziemy dalej.', dev_message=f'repeated_failure: reason={exp_blocker} sev={exp_severity:.2f} recurring_types={exp_recurring_types}', remediation_hint='Zmień strategię lub parametry zapytania.', escalated_from_history=True, feedback_applied=True, feedback_detail=f'Recurring failures ({exp_recurring_types}) escalated to hard.')))
    if degraded and confidence < _conf_hard_thresh - 0.05:
        sigs = ['strategy_degraded', 'strategy_confidence']
        all_signals.extend(sigs)
        candidates.append((0, 3, BlockerVerdict(blocker_active=True, blocker_type='degraded_runtime', blocker_scope='turn', blocker_severity='hard', hard=True, resolution='hard_block', reason=f'Runtime zdegradowany, brak pewności (confidence={confidence:.2f}).', source='strategy_selector', recommended_action='Spróbuj za chwilę albo uprość zapytanie.', contributing_signals=sigs, confidence=min(1.0, 1.0 - confidence), user_message='Backend jest niepewny — spróbuj za chwilę albo uprość zapytanie.', dev_message=f'degraded_runtime: degraded={degraded} conf={confidence:.3f}', remediation_hint='Sprawdź logi strategy_selector; restart może pomóc.')))
    if policy_avoid_weight >= 0.7:
        sigs = ['policy_avoid', 'policy_profile']
        all_signals.extend(sigs)
        candidates.append((0, 3, BlockerVerdict(blocker_active=True, blocker_type='policy_violation_internal', blocker_scope='session', blocker_severity='hard', hard=True, resolution='hard_block', reason=f"Polityka zabrania akcji '{current_action_type}' (avoid weight={policy_avoid_weight:.2f}). {policy_avoid_reason}", source='policy_engine', recommended_action='Użyj innej strategii lub skontaktuj się z operatorem.', contributing_signals=sigs, confidence=policy_avoid_weight, user_message='To jest zablokowane ustawieniami.', dev_message=f'policy_violation: avoid_weight={policy_avoid_weight:.2f} action={current_action_type} profile={policy_profile_name}', feedback_applied=True, escalated_from_history=True, feedback_detail=f"Policy 'avoid' signal from reflection history (weight={policy_avoid_weight:.2f}).")))
    if sim_ran and sim_risk >= _risk_hard_thresh and (selected_strategy in ('agentic', 'research')):
        downgrade_to = 'contextual' if selected_strategy == 'agentic' else 'instant'
        sigs = ['simulation_risk_summary', 'selected_strategy']
        all_signals.extend(sigs)
        candidates.append((1, 2, BlockerVerdict(blocker_active=True, blocker_type='high_risk_path', blocker_scope='turn', blocker_severity='caution', hard=False, resolution='downgrade', reason=f"Symulacja: ryzyko={sim_risk:.2f} dla '{selected_strategy}'. Downgrade do '{downgrade_to}'.", source='simulation_engine', recommended_action=f'Strategia obniżona do {downgrade_to}.', contributing_signals=sigs, confidence=sim_risk, user_message='Wybieram bezpieczniejsze podejście ze względu na złożoność pytania.', dev_message=f'high_risk_path: risk={sim_risk:.2f} conf={sim_confidence:.2f} downgrade {selected_strategy}→{downgrade_to}', next_best_action=downgrade_to)))
    if confidence < _conf_caution_thresh and (not degraded) and (selected_strategy in ('agentic', 'research')):
        reroute_to = 'contextual'
        sigs = ['strategy_confidence', 'selected_strategy']
        all_signals.extend(sigs)
        candidates.append((1, 2, BlockerVerdict(blocker_active=True, blocker_type='low_confidence_decision', blocker_scope='turn', blocker_severity='caution', hard=False, resolution='reroute', reason=f"Niska pewność decyzji (confidence={confidence:.2f}) dla strategii '{selected_strategy}'. Reroute do '{reroute_to}'.", source='strategy_selector', recommended_action=f'Reroute do strategii {reroute_to}.', contributing_signals=sigs, confidence=min(1.0, 1.0 - confidence), user_message='Koryguję podejście na bardziej bezpieczne.', dev_message=f'low_confidence: conf={confidence:.3f} reroute {selected_strategy}→{reroute_to}', next_best_action=reroute_to)))
    if consistency_class == 'conflict' and contradictions >= 1 and (confidence >= 0.4):
        sigs = ['consistency_classification', 'contradictions_found']
        all_signals.extend(sigs)
        candidates.append((2, 2, BlockerVerdict(blocker_active=True, blocker_type='consistency_conflict', blocker_scope='turn', blocker_severity='caution', hard=False, resolution='caution_pass', reason=f'Potencjalna sprzeczność (contradictions={contradictions}).', source='consistency_engine', recommended_action='Rozważ wyjaśnienie sprzecznych stwierdzeń.', contributing_signals=sigs, confidence=0.5 + 0.5 * (1.0 - confidence), user_message='Możliwa sprzeczność w pytaniu.', dev_message=f'mild_consistency: class={consistency_class} contradictions={contradictions} conf={confidence:.3f}')))
    if exp_blocker and exp_severity < 0.8 and (not skip_exp):
        sigs = ['experience_blocker_reason']
        all_signals.extend(sigs)
        candidates.append((2, 2, BlockerVerdict(blocker_active=True, blocker_type='repeated_failure', blocker_scope='turn', blocker_severity='caution', hard=False, resolution='caution_pass', reason=f'Historia: {exp_blocker}.', source='experience_memory', recommended_action='Zachowaj ostrożność.', contributing_signals=sigs, confidence=max(0.3, exp_severity), user_message='Z historii podobnych tur: ostrożniej w tej turze.', dev_message=f'mild_experience: reason={exp_blocker} sev={exp_severity:.2f}', feedback_applied=bool(exp_recurring), feedback_detail=f'Recurring={exp_recurring}, types={exp_recurring_types}' if exp_recurring else '')))
    if degraded and confidence >= _conf_hard_thresh - 0.05:
        sigs = ['strategy_degraded']
        all_signals.extend(sigs)
        candidates.append((2, 2, BlockerVerdict(blocker_active=True, blocker_type='degraded_runtime', blocker_scope='turn', blocker_severity='caution', hard=False, resolution='caution_pass', reason=f'Runtime zdegradowany (confidence={confidence:.2f}).', source='strategy_selector', recommended_action='Wynik może być mniej precyzyjny.', contributing_signals=sigs, confidence=min(1.0, 1.0 - confidence), user_message='Wynik może być mniej precyzyjny niż zwykle.', dev_message=f'mild_degraded: degraded={degraded} conf={confidence:.3f}')))
    if sim_ran and _risk_caution_thresh <= sim_risk < _risk_hard_thresh:
        sigs = ['simulation_risk_summary']
        all_signals.extend(sigs)
        candidates.append((2, 1, BlockerVerdict(blocker_active=True, blocker_type='high_risk_path', blocker_scope='turn', blocker_severity='caution', hard=False, resolution='caution_pass', reason=f'Umiarkowane ryzyko symulacji (risk={sim_risk:.2f}).', source='simulation_engine', recommended_action='Rozważ uproszczenie zapytania.', contributing_signals=sigs, confidence=sim_risk, user_message='Dość złożone pytanie — odpowiadam ostrożniej.', dev_message=f'mild_sim_risk: risk={sim_risk:.2f} conf={sim_confidence:.2f}')))
    exp_matches = int(decision_core.get('experience_matches_count') or 0)
    exp_conf_adj = float(decision_core.get('experience_confidence_adjustment') or 0.0)
    if exp_matches >= 4 and abs(exp_conf_adj) < 0.02 and (not skip_exp):
        sigs = ['experience_matches_count', 'experience_confidence_adjustment']
        all_signals.extend(sigs)
        candidates.append((2, 1, BlockerVerdict(blocker_active=True, blocker_type='contradictory_memory_state', blocker_scope='turn', blocker_severity='caution', hard=False, resolution='caution_pass', reason=f'Sprzeczne doświadczenia: {exp_matches} dopasowań z mieszanymi wynikami.', source='experience_memory', recommended_action='Wyniki mogą być niejednoznaczne.', contributing_signals=sigs, confidence=0.45, user_message='Mieszane wyniki w historii podobnych tur.', dev_message=f'contradictory_memory: matches={exp_matches} conf_adj={exp_conf_adj:.3f}')))
    if policy_penalize_weight >= 0.6:
        sigs = ['policy_penalize', 'policy_profile']
        all_signals.extend(sigs)
        candidates.append((2, 1, BlockerVerdict(blocker_active=True, blocker_type='resource_exhaustion', blocker_scope='session', blocker_severity='caution', hard=False, resolution='caution_pass', reason=f"Polityka penalizuje '{current_action_type}' (weight={policy_penalize_weight:.2f}). Zachowaj ostrożność.", source='policy_engine', recommended_action='Rozważ zmianę podejścia.', contributing_signals=sigs, confidence=policy_penalize_weight, user_message='Ostrożniej przy tym typie akcji.', dev_message=f'resource_exhaustion: penalize_weight={policy_penalize_weight:.2f} action={current_action_type}', feedback_applied=True, feedback_detail=f'Policy penalize from reflections (weight={policy_penalize_weight:.2f}).')))
    if not candidates:
        return BlockerVerdict.allow()
    candidates.sort(key=lambda c: (c[0], -c[1]))
    _, _, winner = candidates[0]
    if not winner.hard and exp_recurring and (len(exp_recurring_types) >= 2) and (not skip_exp):
        winner.blocker_severity = 'hard'
        winner.hard = True
        winner.resolution = 'hard_block'
        winner.escalated_from_history = True
        winner.feedback_applied = True
        winner.feedback_detail = f"Escalated from caution to hard: {len(exp_recurring_types)} recurring failure types ({', '.join(exp_recurring_types[:3])})"
        winner.dev_message += f' [ESCALATED: recurring failures ×{len(exp_recurring_types)}]'
    elif not winner.hard and policy_avoid_weight >= 0.55 and (winner.blocker_type != 'consistency_conflict') and (not skip_exp):
        winner.blocker_severity = 'hard'
        winner.hard = True
        winner.resolution = 'hard_block'
        winner.escalated_from_history = True
        winner.feedback_applied = True
        winner.feedback_detail = f'Escalated by policy avoid signal (weight={policy_avoid_weight:.2f})'
        winner.dev_message += f' [ESCALATED: policy avoid w={policy_avoid_weight:.2f}]'
    elif winner.hard and policy_boost_weight >= 0.65 and (winner.blocker_type not in ('consistency_conflict', 'policy_violation_internal')):
        winner.blocker_severity = 'caution'
        winner.hard = False
        winner.resolution = 'caution_pass'
        winner.deescalated_from_history = True
        winner.feedback_applied = True
        winner.feedback_detail = f'De-escalated by policy boost signal (weight={policy_boost_weight:.2f})'
        winner.dev_message += f' [DE-ESCALATED: policy boost w={policy_boost_weight:.2f}]'
    unique_signals = list(dict.fromkeys(all_signals))
    winner.contributing_signals = unique_signals
    winner.signals_count = len(unique_signals)
    winner.timestamp = _time.time()
    return winner
