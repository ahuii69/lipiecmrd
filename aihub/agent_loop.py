#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Agent Loop - Główna pętla agenta integrująca wszystkie systemy kognitywne.

Odpowiada za:
- Przetwarzanie wiadomości
- Wykonywanie decyzji
- Zarządzanie cyklem życia agenta
- Integrację wszystkich komponentów
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from aihub.agent_executor import AgentExecutor
from aihub.attention_controller import rank_messages
from aihub.cognitive_controller import DecisionRequest, get_cognitive_controller
from aihub.conflict_detector import check_conflict
from aihub.db import append_event, fetch_all, now_ts
from aihub.metrics_engine import record_error, record_latency
from aihub.psyche_core import get_psyche_core

logger = logging.getLogger(__name__)

# Initialize cognitive controller
cognitive_controller = get_cognitive_controller()
_executor = AgentExecutor()


def get_psyche_state(user_id: str) -> Dict[str, Any]:
    """Get psychological state for user."""
    try:
        psyche = get_psyche_core().ensure_user(user_id)
        return {
            "mood": psyche.get("mood", "neutral"),
            "energy": psyche.get("energy", 0.5),
            "focus": psyche.get("focus", 0.5),
        }
    except (KeyError, TypeError, AttributeError) as e:
        logger.warning("Data error getting psyche state: %s", e)
        return {"mood": "neutral", "energy": 0.5, "focus": 0.5}
    except OSError as e:
        logger.warning("I/O error getting psyche state: %s", e)
        return {"mood": "neutral", "energy": 0.5, "focus": 0.5}


def get_pending_messages(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Get pending messages from STM."""
    try:
        rows = fetch_all(
            "SELECT id, role, content, ts FROM stm_messages WHERE user_id=? ORDER BY ts DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(r) for r in rows] if rows else []
    except (KeyError, TypeError) as e:
        logger.error("Data error fetching messages: %s", e)
        return []
    except OSError as e:
        logger.error("I/O/DB error fetching messages: %s", e)
        return []


async def process_decision(
    user_id: str, decision_result: Any, _psyche_state: Any
) -> Dict[str, Any]:
    """Process decision from cognitive controller."""
    try:
        action_type = decision_result.action_type
        parameters = decision_result.parameters
        confidence = decision_result.confidence

        logger.info("Processing decision: %s (confidence=%s)", action_type, confidence)

        if action_type in {"skip", "reflect"}:
            return {
                "executed": False,
                "action_type": action_type,
                "confidence": confidence,
                "reason": decision_result.skip_reason or "no-op action",
            }

        # Create decision request for conflict checking
        conflict_check = check_conflict(
            user_id,
            [
                {
                    "type": action_type,
                    "parameters": parameters,
                }
            ],
        )

        if conflict_check.has_conflict:
            logger.warning("Conflict detected: %s", conflict_check.conflict_description)
            append_event(
                user_id,
                "decision.conflict",
                {
                    "action_type": action_type,
                    "conflict": conflict_check.conflict_description,
                    "severity": conflict_check.severity,
                },
            )
            return {
                "executed": False,
                "reason": f"Conflict: {conflict_check.conflict_description}",
            }

        # Execute action based on type
        result = await _execute_action(user_id, action_type, parameters)
        executed = bool(result.get("ok", False)) if isinstance(result, dict) else False

        return {
            "executed": executed,
            "action_type": action_type,
            "confidence": confidence,
            "result": result,
        }

    except (ValueError, TypeError, KeyError) as e:
        logger.error("Data error processing decision: %s", e, exc_info=True)
        try:
            record_error("decision_data_error", user_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Failed to record error metric")
        return {"executed": False, "error": str(e)}
    except OSError as e:
        logger.error("I/O error processing decision: %s", e, exc_info=True)
        try:
            record_error("decision_io_error", user_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Failed to record error metric")
        return {"executed": False, "error": str(e)}


async def _execute_action(
    user_id: str, action_type: str, parameters: Dict[str, Any]
) -> Dict[str, Any]:
    """Execute action via AgentExecutor."""
    try:
        return await _executor.execute(action_type, parameters, user_id)
    except (ValueError, TypeError, KeyError) as e:
        logger.error("Data error executing action %s: %s", action_type, e)
        return {"error": str(e)}
    except OSError as e:
        logger.error("I/O error executing action %s: %s", action_type, e)
        return {"error": str(e)}


async def run_cognitive_direct_cycle(user_id: str) -> Dict[str, Any]:
    """Execute single agent cycle."""
    try:
        get_psyche_core().ensure_user(user_id)
        logger.info("Starting cycle for user: %s", user_id)

        # 1. Get psyche state
        psyche_state = get_psyche_state(user_id)
        logger.debug(
            "Psyche state: mood=%s, energy=%s",
            psyche_state.get("mood"),
            psyche_state.get("energy"),
        )

        # 2. Get pending messages
        messages = get_pending_messages(user_id, limit=20)
        if not messages:
            logger.debug("No pending messages")
            return {
                "cycle": "completed",
                "messages_processed": 0,
                "decisions_made": 0,
            }

        # 3. Rank messages by attention
        ranked_messages = rank_messages(user_id, messages)
        logger.debug("Ranked %d messages", len(ranked_messages))

        # 4. Process top message
        decisions_made = 0
        for ranking in ranked_messages[:3]:  # Process top 3
            message_content = (
                ranking.message.get("content", "")
                if isinstance(ranking.message, dict)
                else str(ranking.message)
            )
            if not isinstance(message_content, str):
                message_content = str(message_content or "")
            message_content = message_content.strip()
            if not message_content:
                continue

            # Create decision request
            decision_request = DecisionRequest(
                user_id=user_id,
                message=message_content,
                context={
                    "psyche_state": {
                        "mood": psyche_state.get("mood"),
                        "energy": psyche_state.get("energy"),
                        "focus": psyche_state.get("focus"),
                    },
                    "urgency_score": ranking.urgency,
                    "relevance_score": ranking.relevance,
                },
                available_tools=["web_search", "memory_store", "file_write"],
                constraints={
                    "max_time_seconds": 30,
                    "require_user_approval": False,
                },
            )

            # Get decision from cognitive controller
            start_ts = now_ts()
            decision_result = await cognitive_controller.decide(decision_request)
            duration_ms = (now_ts() - start_ts) * 1000

            record_latency("cognitive_decision", duration_ms, success=True)

            # Process decision
            execution_result = await process_decision(
                user_id, decision_result, psyche_state
            )

            # ── ETAP 9: Post-action reflection ──
            try:
                from aihub.reflection_engine import ReflectionInput, reflect_on_action

                ref_input = ReflectionInput(
                    user_id=user_id,
                    action_type=decision_result.action_type,
                    parameters=decision_result.parameters or {},
                    confidence=decision_result.confidence,
                    execution_result=execution_result,
                    decision_reasoning=decision_result.reasoning or "",
                    context={
                        "psyche_state": {
                            "mood": psyche_state.get("mood"),
                            "energy": psyche_state.get("energy"),
                            "focus": psyche_state.get("focus"),
                        },
                    },
                )
                reflect_on_action(ref_input)
            except Exception:
                logger.debug("Reflection after decision failed", exc_info=True)

            if execution_result.get("executed"):
                decisions_made += 1

            append_event(
                user_id,
                "cycle.decision",
                {
                    "message": message_content,
                    "action": decision_result.action_type,
                    "confidence": decision_result.confidence,
                    "executed": execution_result.get("executed"),
                },
            )

        logger.info("Cycle completed: %d decisions made", decisions_made)

        return {
            "cycle": "completed",
            "messages_processed": len(ranked_messages),
            "decisions_made": decisions_made,
        }

    except (ValueError, TypeError, KeyError) as e:
        logger.error("Data error in agent cycle for %s: %s", user_id, e, exc_info=True)
        try:
            record_error("agent_cycle_data_error", user_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Failed to record error metric")
        return {"cycle": "error", "error": str(e)}
    except OSError as e:
        logger.error("I/O error in agent cycle for %s: %s", user_id, e, exc_info=True)
        try:
            record_error("agent_cycle_io_error", user_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Failed to record error metric")
        return {"cycle": "error", "error": str(e)}


async def agent_cycle(
    user_id: str,
    input_event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Canonical adapter for one loop cycle via ExecutiveController."""
    from aihub.executive_controller import get_executive_controller

    controller = get_executive_controller()
    cycle = await controller.run_cycle(
        input_event or {},
        mode="loop",
        user_id=user_id,
    )
    legacy = cycle.get("legacy_response")
    if isinstance(legacy, dict):
        return legacy
    payload = cycle.get("execution_result", {}).get("payload")
    if isinstance(payload, dict):
        return payload
    return {
        "cycle": "error",
        "error": "missing legacy response from executive controller",
    }


async def run_loop(
    _text: str,
    user_id: str = "default",
    max_iterations: int = 1,
    _dry_run: bool = False,
) -> Dict[str, Any]:
    """Legacy convenience wrapper; canonical runtime entry is ExecutiveController."""
    try:
        logger.info(
            "Starting agent loop: user=%s, iterations=%s", user_id, max_iterations
        )

        results = []

        for iteration in range(max_iterations):
            logger.debug("Iteration %d/%d", iteration + 1, max_iterations)

            cycle_result = await agent_cycle(
                user_id,
                input_event={
                    "text": _text,
                    "dry_run": _dry_run,
                    "max_iters": max_iterations,
                    "iteration": iteration + 1,
                },
            )
            results.append(cycle_result)

            if cycle_result.get("cycle") == "error":
                break

            await asyncio.sleep(0.5)  # Small delay between iterations

        return {
            "ok": True,
            "status": "completed",
            "iterations": len(results),
            "results": results,
        }

    except (ValueError, TypeError, KeyError) as e:
        logger.error("Data error in run_loop: %s", e, exc_info=True)
        return {"ok": False, "status": "error", "error": str(e)}
    except OSError as e:
        logger.error("I/O error in run_loop: %s", e, exc_info=True)
        return {"ok": False, "status": "error", "error": str(e)}


async def loop(user_id: str = "default", cycle_interval: float = 5.0) -> None:
    """Legacy continuous loop wrapper over ExecutiveController adapter path."""
    try:
        logger.info("AGENT STARTED: user=%s, interval=%ss", user_id, cycle_interval)

        cycle_count = 0

        while True:
            try:
                cycle_count += 1
                logger.debug("=== Cycle %d ===", cycle_count)

                result = await agent_cycle(
                    user_id,
                    input_event={"source": "continuous_loop"},
                )

                if result.get("cycle") != "error":
                    logger.debug("Cycle %d OK: %s", cycle_count, result)

            except (ValueError, TypeError, KeyError) as e:
                logger.error("Data error in loop iteration: %s", e, exc_info=True)
                try:
                    record_error("agent_loop_data_error", user_id)
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.debug("Failed to record error metric")

            except OSError as e:
                logger.error("I/O error in loop iteration: %s", e, exc_info=True)
                try:
                    record_error("agent_loop_io_error", user_id)
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.debug("Failed to record error metric")

            await asyncio.sleep(cycle_interval)

    except KeyboardInterrupt:
        logger.info("Agent loop interrupted by user")
    except OSError as e:
        logger.error("Fatal I/O error in agent loop: %s", e, exc_info=True)
    except (ValueError, TypeError) as e:
        logger.error("Fatal data error in agent loop: %s", e, exc_info=True)


def run_sync(user_id: str = "default", max_iterations: int = 1) -> Dict[str, Any]:
    """Legacy sync wrapper for tests/scripts (non-canonical entrypoint)."""
    return asyncio.run(run_loop("", user_id, max_iterations))


if __name__ == "__main__":
    import sys

    _main_user_id = sys.argv[1] if len(sys.argv) > 1 else "default"
    asyncio.run(loop(_main_user_id, cycle_interval=5.0))
