"""Offline replay of turns without side-effect tools or production write-backs."""

from __future__ import annotations

import logging
from typing import Any

from aihub.adaptive_learning.engine import (
    apply_learning_influences_to_decision,
    process_turn_learning,
)
from aihub.adaptive_learning.store import list_recent_outcomes
from aihub.turn.pragmatics import analyze_pragmatics

log = logging.getLogger(__name__)


def replay_user_turns(
    *,
    user_id: str,
    limit: int = 100,
    mode: str = "evaluation",
) -> dict[str, Any]:
    """Replay stored turn outcomes through learning evaluators (no side effects).

    mode=evaluation: no learning write-backs (replay_mode=True).
    """
    outcomes = list_recent_outcomes(user_id=user_id, limit=limit)
    results: list[dict[str, Any]] = []
    for o in reversed(outcomes):
        message = o.message_preview or ""
        prag = analyze_pragmatics(raw_text=message, history=[], user_id=user_id)
        old_strategy = o.selected_strategy
        decision = {
            "selected_strategy": old_strategy,
            "strategy_confidence": o.confidence,
            "reason_codes": list(o.reason_codes),
            "web_decision": "required" if o.web_used else "off",
            "session_id": o.session_id,
            "cognitive_ambiguity": float(getattr(prag, "ambiguity_score", 0) or 0),
        }
        apply_learning_influences_to_decision(
            decision_core=decision,
            user_id=user_id,
            message=message,
            intent=o.primary_intent,
        )
        try:
            from aihub.world_knowledge import apply_knowledge_influences_to_decision
            from aihub.world_knowledge.engine import process_turn_knowledge

            apply_knowledge_influences_to_decision(
                decision_core=decision,
                user_id=user_id,
                message=message,
                intent=o.primary_intent,
            )
            wk = process_turn_knowledge(
                turn_id=f"replay:{o.turn_id}",
                user_id=user_id,
                session_id=o.session_id,
                message=message,
                response_text=o.response_preview or "",
                trace={"replay_mode": True, "selected_strategy": old_strategy},
                decision_core=decision,
                replay_mode=True,
            )
        except Exception as wk_exc:
            log.debug("replay knowledge skipped: %s", wk_exc)
            wk = None
        new_strategy = str(decision.get("selected_strategy") or old_strategy)
        lr = process_turn_learning(
            turn_id=f"replay:{o.turn_id}",
            user_id=user_id,
            session_id=o.session_id,
            message=message,
            response_text=o.response_preview or "",
            trace={
                "selected_strategy": old_strategy,
                "response_critic_score": o.response_critic_score,
                "duration_ms": 0,
                "replay_mode": True,
            },
            decision_core=decision,
            ok=o.overall_reward >= 0,
            replay_mode=(mode == "evaluation"),
        )
        results.append(
            {
                "turn_id": o.turn_id,
                "old_strategy": old_strategy,
                "new_strategy": new_strategy,
                "strategy_changed": new_strategy != old_strategy,
                "calibrated_confidence": decision.get("strategy_confidence"),
                "raw_confidence": decision.get("strategy_confidence_raw"),
                "learning_codes": [
                    c
                    for c in (decision.get("reason_codes") or [])
                    if str(c).startswith("LEARN_")
                ][:12],
                "knowledge_codes": [
                    c
                    for c in (decision.get("reason_codes") or [])
                    if str(c).startswith("WK_")
                ][:12],
                "knowledge_claims": (decision.get("knowledge_claims_count") or 0),
                "knowledge_writeback": bool(wk and wk.writeback_succeeded) if wk else False,
                "replay_reward": lr.outcome.overall_reward if lr.outcome else None,
                "lessons_persisted": lr.lessons_persisted,
            }
        )
    changed = sum(1 for r in results if r["strategy_changed"])
    return {
        "mode": mode,
        "user_id": user_id,
        "replayed": len(results),
        "strategy_changes": changed,
        "writebacks": 0 if mode == "evaluation" else sum(1 for _ in results),
        "side_effects_executed": False,
        "results": results,
    }
