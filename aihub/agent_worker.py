#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from .agent_db import ensure_schema, get_agent_state
from .db import append_event
from .executive_controller import (
    EXECUTION_INTENT_WORKER_MAINTENANCE,
    get_executive_controller,
)

logger = logging.getLogger(__name__)

AGENT_INTERVAL_S = float(os.getenv("AGENT_INTERVAL_S", "3.5"))
# System/maintenance scope for background ticks — NOT a request-scoped user identity.
# Override with AIHUB_BACKGROUND_AGENT_USER_IDS for real per-user maintenance only.
AGENT_USER_ID = os.getenv("AGENT_USER_ID", "system:maintenance")
if AGENT_USER_ID.strip() == "default":
    # Avoid colliding maintenance writes with a human/shared "default" memory space.
    AGENT_USER_ID = "system:maintenance"
AGENT_AUTOSTART = os.getenv("AGENT_AUTOSTART", "1") == "1"
AGENT_MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", "3"))
AGENT_RETRY_DELAY_S = float(os.getenv("AGENT_RETRY_DELAY_S", "1.0"))
BACKGROUND_AGENT_LOOP_ENABLED = (
    os.getenv("AIHUB_BACKGROUND_AGENT_LOOP_ENABLED", "1") == "1"
)


def _iter_background_user_ids() -> list[str]:
    """Users processed each worker interval (comma-separated override, else AGENT_USER_ID)."""
    raw = os.getenv("AIHUB_BACKGROUND_AGENT_USER_IDS", "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    return [AGENT_USER_ID]


def _worker_tick_input_event() -> dict:
    ev: dict = {
        "max_stm": 200,
        "max_tasks": 6,
        "source": "agent_worker",
        "execution_intent_source": EXECUTION_INTENT_WORKER_MAINTENANCE,
    }
    if os.getenv("AIHUB_BACKGROUND_EXPLICIT_MONITOR", "0") == "1":
        ev["proactive_monitor_explicit"] = True
    return ev


class AgentWorkerError(Exception):
    """Custom exception dla agent workera."""


def _run_loop() -> None:
    """
    Główna pętla agenta - wykonuje agent_tick w interwałach.

    Obsługuje:
    - Inicjalizację schematu
    - Retry logic
    - Error recovery
    - Graceful shutdown
    """
    try:
        logger.info("Agent worker starting...")
        ensure_schema()
        logger.info("Agent schema initialized")
    except Exception as e:
        logger.error(f"Failed to initialize agent schema: {e}", exc_info=True)
        return

    consecutive_errors = 0
    max_consecutive_errors = 5

    tick_event = _worker_tick_input_event()

    while not _stop_worker.is_set():
        try:
            if not BACKGROUND_AGENT_LOOP_ENABLED:
                logger.debug(
                    "AIHUB_BACKGROUND_AGENT_LOOP_ENABLED=0 — skipping tick batch"
                )
                if _stop_worker.wait(AGENT_INTERVAL_S):
                    break
                continue

            for uid in _iter_background_user_ids():
                # Pobierz stan agenta
                try:
                    st = get_agent_state(uid)
                except Exception as e:
                    logger.error(f"Failed to get agent state: {e}")
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        logger.error(
                            f"Too many consecutive errors ({consecutive_errors}), stopping worker"
                        )
                        return
                    continue

                if not st.get("enabled", True):
                    logger.debug("Agent %s is disabled, skipping this user", uid)
                    continue

                success = False
                last_bg_tick_data: Optional[Dict[str, Any]] = None
                for attempt in range(1, AGENT_MAX_RETRIES + 1):
                    try:
                        logger.debug(
                            "Agent tick user=%s attempt %s/%s",
                            uid,
                            attempt,
                            AGENT_MAX_RETRIES,
                        )
                        controller = get_executive_controller()
                        cycle = asyncio.run(
                            controller.run_cycle(
                                dict(tick_event),
                                mode="tick",
                                user_id=uid,
                            )
                        )
                        result = (
                            cycle.get("legacy_response")
                            if isinstance(cycle, dict)
                            else None
                        )
                        if not isinstance(result, dict):
                            result = (
                                cycle.get("execution_result", {}).get("payload", {})
                                if isinstance(cycle, dict)
                                else {}
                            )

                        if (
                            isinstance(cycle, dict)
                            and cycle.get("execution_origin") == "background_agent_loop"
                        ):
                            last_bg_tick_data = {
                                "cycle_id": cycle.get("cycle_id"),
                                "tick_ok": bool(result.get("ok")),
                                "proactive_noop": cycle.get("proactive_noop"),
                                "proactive_trigger_present": cycle.get(
                                    "proactive_trigger_present"
                                ),
                                "proactive_trigger_type": cycle.get(
                                    "proactive_trigger_type"
                                ),
                                "background_result_type": cycle.get(
                                    "background_result_type"
                                ),
                                "bias_updated": cycle.get("bias_updated"),
                                "attempt": attempt,
                            }

                        if result.get("ok"):
                            processed = result.get("processed", 0)
                            enqueued = result.get("enqueued", 0)
                            logger.debug(
                                "Agent tick ok user=%s processed=%s enqueued=%s",
                                uid,
                                processed,
                                enqueued,
                            )
                            success = True
                            consecutive_errors = 0
                            break

                        logger.warning(
                            "Agent tick returned non-ok user=%s result=%s", uid, result
                        )
                        consecutive_errors += 1

                    except asyncio.TimeoutError as e:
                        logger.warning(
                            "Agent tick timeout user=%s (attempt %s): %s",
                            uid,
                            attempt,
                            e,
                        )
                        consecutive_errors += 1
                        if attempt < AGENT_MAX_RETRIES:
                            time.sleep(AGENT_RETRY_DELAY_S * attempt)

                    except Exception as e:
                        logger.error(
                            "Agent tick error user=%s (attempt %s): %s",
                            uid,
                            attempt,
                            e,
                            exc_info=True,
                        )
                        consecutive_errors += 1
                        if attempt < AGENT_MAX_RETRIES:
                            time.sleep(AGENT_RETRY_DELAY_S * attempt)

                if last_bg_tick_data is not None:
                    append_event(uid, "agent.background_loop.tick", last_bg_tick_data)

                if not success:
                    append_event(
                        uid,
                        "agent.worker.error",
                        {
                            "error": "all_ticks_failed",
                            "attempts": AGENT_MAX_RETRIES,
                        },
                    )
                    logger.warning(
                        "Agent tick failed after %s attempts user=%s",
                        AGENT_MAX_RETRIES,
                        uid,
                    )

            # Sprawdź czy nie zrobiliśmy za dużo błędów
            if consecutive_errors >= max_consecutive_errors:
                logger.error(
                    f"Too many consecutive errors ({consecutive_errors}), pausing worker"
                )
                append_event(
                    AGENT_USER_ID,
                    "agent.worker.paused",
                    {"reason": "too_many_errors", "errors": consecutive_errors},
                )
                # Pause ale nie wyłączaj
                if _stop_worker.wait(AGENT_INTERVAL_S * 10):
                    break
                consecutive_errors = 0

            if _stop_worker.wait(AGENT_INTERVAL_S):
                break

        except KeyboardInterrupt:
            logger.info("Agent worker interrupted by KeyboardInterrupt")
            break
        except Exception as e:
            logger.error(f"Unexpected error in agent worker loop: {e}", exc_info=True)
            consecutive_errors += 1
            if _stop_worker.wait(AGENT_INTERVAL_S * 2):
                break


_worker_started = False
_worker_thread: Optional[threading.Thread] = None
_stop_worker = threading.Event()


def start_worker_once() -> None:
    """
    Uruchomi worker thread jeśli nie jest już uruchomiony.

    Gwarantuje Single instance pattern.
    """
    global _worker_started, _worker_thread

    if _worker_started:
        if _worker_thread is not None and _worker_thread.is_alive():
            logger.debug("Agent worker already started")
            return
        logger.warning(
            "Agent worker marked started but thread is not alive; restarting"
        )
        _worker_started = False
        _worker_thread = None

    if not AGENT_AUTOSTART:
        logger.info("Agent autostart disabled, worker not started")
        return

    try:
        logger.info(
            "Starting agent worker (users=%s, interval=%ss)",
            ",".join(_iter_background_user_ids()),
            AGENT_INTERVAL_S,
        )
        _stop_worker.clear()
        _worker_thread = threading.Thread(
            target=_run_loop, daemon=True, name="aihub-agent-worker"
        )
        _worker_thread.start()
        _worker_started = True
        logger.info("Agent worker thread started successfully")
    except Exception as e:
        logger.error(f"Failed to start agent worker: {e}", exc_info=True)
        _worker_started = False


def stop_worker() -> None:
    """
    Zatrzymuje worker thread (jeśli jest to możliwe).
    """
    global _worker_started, _worker_thread

    _stop_worker.set()
    if _worker_thread is None:
        _worker_started = False
        logger.debug("Agent worker not running")
        return

    logger.info("Stopping agent worker...")
    _worker_thread.join(timeout=5.0)

    if _worker_thread.is_alive():
        raise RuntimeError("Agent worker did not stop within timeout")
    logger.info("Agent worker stopped successfully")
    _worker_started = False
    _worker_thread = None


def is_running() -> bool:
    """Zwraca czy worker jest aktualnie uruchomiony."""
    return _worker_started and _worker_thread is not None and _worker_thread.is_alive()
