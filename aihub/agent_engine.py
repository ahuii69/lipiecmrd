#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .agent_db import (
    STATUS_QUEUED,
    STATUS_RUNNING,
    claim_next_task,
    complete_task,
    count_tasks,
    enqueue_task,
    get_agent_state,
    update_cursor,
)
from .db import append_event, fetch_all, now_ts
from .fs_tools import write_file
from .memory_core import get_memory_core
from .psyche_core import get_psyche_core
from .system_ops import create_snapshot
from .web_tools import fetch_url

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)


def probe_reactive_tick_triggers(
    user_id: str, raw_event: Optional[Dict[str, Any]] = None
) -> Tuple[bool, str, Dict[str, Any]]:
    """Read-only proactive signals for background worker (no STM cursor / task mutations).

    Returns (should_run_heavy_reactive_path, primary_trigger_type, details).
    """
    raw = dict(raw_event or {})
    if raw.get("proactive_monitor_explicit") is True:
        return True, "explicit_monitor_flag", {}

    watch = {
        x.strip()
        for x in (os.getenv("AIHUB_BACKGROUND_PROBE_ALWAYS_RUN_USERS") or "").split(",")
        if x.strip()
    }
    uid = (user_id or "").strip()
    if uid and uid in watch:
        return True, "explicit_user_watchlist", {"watchlist": True}

    st = get_agent_state(uid or "default")
    if not st.get("enabled", True):
        return False, "agent_disabled", {"enabled": False}

    nq = count_tasks(uid, STATUS_QUEUED)
    nr = count_tasks(uid, STATUS_RUNNING)
    if nq + nr > 0:
        return True, "pending_agent_tasks", {"queued": nq, "running": nr}

    msgs = _pull_new_stm(uid, float(st.get("last_stm_ts", 0.0)), limit=200)
    if msgs:
        return True, "stm_inbound_signals", {"new_stm_count": len(msgs)}

    return False, "none", {}


def _pull_new_stm(
    user_id: str, since_ts: float, limit: int = 200
) -> List[Dict[str, Any]]:
    """Pobierz nowe wiadomości STM od danego timestampu."""
    try:
        rows = fetch_all(
            "SELECT id, role, content, meta, ts FROM stm_messages WHERE user_id=? AND ts>? ORDER BY ts ASC LIMIT ?",
            (user_id, float(since_ts), int(limit)),
        )
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "role": r["role"],
                    "content": r["content"],
                    "ts": float(r["ts"]),
                }
            )
        return out
    except Exception as e:
        logger.error(f"Error pulling STM for user {user_id}: {e}", exc_info=True)
        return []


def extract_facts_from_text(text: str) -> List[Tuple[str, List[str], Dict[str, Any]]]:
    """
    Ekstrakcja faktów z tekstu.

    Returns: [(fact, tags, meta), ...]
    """
    try:
        t = text.strip()
        tl = t.lower()
        facts: List[Tuple[str, List[str], Dict[str, Any]]] = []

        if any(k in tl for k in ["lubię", "nie lubię", "preferuję", "wolę"]):
            facts.append(
                (
                    f"Preferencja użytkownika: {t}",
                    ["user", "preference"],
                    {"source": "heuristic"},
                )
            )

        if any(k in tl for k in ["nazywam się", "mam na imię"]):
            facts.append(
                (
                    f"Imię użytkownika: {t}",
                    ["user", "identity"],
                    {"source": "heuristic"},
                )
            )

        if any(k in tl for k in ["pracuję", "moja praca", "jestem"]):
            facts.append(
                (f"Info o użytkowniku: {t}", ["user", "bio"], {"source": "heuristic"})
            )

        # Sekrety na wyraźną prośbę są obsługiwane przez user vault (/ deterministyczny routing).
        # Nie wstrzykujemy tu sztucznych „faktów” blokujących pamięć — to powodowało fałszywe odmowy.

        return facts

    except Exception as e:
        logger.error(f"Error extracting facts: {e}", exc_info=True)
        return []


def plan_from_text(user_id: str, text: str) -> List[Dict[str, Any]]:
    """Plan zadań z tekstu — ta sama klasyfikacja intencji co chat capability_escalation.

    Nie używa gołego ``if "sprawdź"`` — lokalne checki (pisownia) nie tworzą web.fetch.
    Mutacje (fs.write / snapshot) wymagają później MutationPolicy + confirmed.
    """
    try:
        from aihub.turn.capability_escalation import detect_capability_intents

        intents = detect_capability_intents(text or "")
        tasks: List[Dict[str, Any]] = []
        url_m = _URL_RE.search(text or "")
        url = url_m.group(1) if url_m else None

        # External verify / search / freshness → research or fetch URL
        # Local editorial "sprawdź pisownię" → no web task (intents.verify=False).
        if intents.get("verify") or intents.get("freshness") or intents.get("ingest"):
            if url and _is_valid_url(url):
                if intents.get("ingest"):
                    tasks.append(
                        {
                            "type": "web.fetch",
                            "priority": 10,
                            "payload": {"url": url},
                        }
                    )
                else:
                    tasks.append(
                        {
                            "type": "web.fetch",
                            "priority": 10,
                            "payload": {"url": url},
                        }
                    )
            else:
                tasks.append(
                    {
                        "type": "research.query",
                        "priority": 15,
                        "payload": {"query": (text or "").strip()},
                    }
                )

        # Explicit structured fs write syntax (legacy): "zapisz: path :: content"
        tl = (text or "").lower()
        if tl.startswith("zapisz:") and "::" in (text or ""):
            try:
                left, content = text.split("::", 1)
                path = left.split(":", 1)[1].strip()
                if _is_valid_path(path):
                    tasks.append(
                        {
                            "type": "fs.write",
                            "priority": 30,
                            "payload": {
                                "path": path,
                                "content": content.strip(),
                                "overwrite": True,
                                # MutationPolicy: never auto-confirm from parser.
                                "confirmed": False,
                            },
                        }
                    )
            except (ValueError, IndexError) as e:
                logger.debug(f"Failed to parse fs.write task: {e}")

        if intents.get("sensitive_mutation") and (
            "snapshot" in tl or "backup" in tl or "kopia" in tl
        ):
            tasks.append(
                {
                    "type": "system.snapshot",
                    "priority": 20,
                    "payload": {"reason": "agent:auto", "confirmed": False},
                }
            )
        elif "snapshot" in tl or "backup" in tl or "kopia" in tl:
            tasks.append(
                {
                    "type": "system.snapshot",
                    "priority": 20,
                    "payload": {"reason": "agent:auto", "confirmed": False},
                }
            )

        return tasks

    except Exception as e:
        logger.error(f"Error planning tasks: {e}", exc_info=True)
        return []


def _is_valid_url(url: str) -> bool:
    """Validate URL basics."""
    if not url or len(url) > 2000:
        return False
    return url.startswith(("http://", "https://"))


def _is_valid_path(path: str) -> bool:
    """Validate file path basics."""
    if not path or len(path) > 1000:
        return False
    if path.startswith("/"):
        return False  # No absolute paths
    return True


async def execute_task(user_id: str, task: Dict[str, Any]) -> None:
    """
    Wykonaj pojedyncze zadanie przez Tool Registry (ta sama powierzchnia co chat).

    Obsługuje m.in.:
    - web.fetch / web.fetch_url / web.ingest_url
    - fs.write / fs.write_file / fs.read_file
    - system.snapshot / snapshot.create
    - memory.search / memory.add_fact / image.generate
    - research.query
    """
    typ = task.get("type")
    try:
        payload = task.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}

        logger.debug(f"Executing task type={typ} for user {user_id}")

        # research.query keeps dedicated rate-limited path (not only registry).
        if str(typ or "") in ("research.query", "research"):
            await _execute_research(user_id, payload)
            return

        from aihub.tools.executive_dispatch import (
            dispatch_executive_tool,
            resolve_executive_tool_name,
        )

        tool_name = resolve_executive_tool_name(str(typ or ""))
        if not tool_name:
            logger.warning(f"Unknown task type: {typ}")
            get_memory_core().ingest_fact(
                user_id,
                f"Nieznany typ zadania: {typ}",
                tags=["agent", "task", "unknown"],
                meta={"payload": payload},
            )
            return

        args = dict(payload)
        # Snapshot / fs.write require explicit confirm via MutationPolicy.
        confirmed = bool(args.pop("_confirmed", False) or args.pop("confirmed", False))
        if tool_name == "snapshot.create" and "reason" not in args:
            args["reason"] = str(args.get("reason") or "agent_task")

        out = await dispatch_executive_tool(
            user_id=user_id,
            tool_name=tool_name,
            arguments=args,
            session_id=str(task.get("session_id") or "agent_engine"),
            mode="agent",
            confirmed=confirmed,
        )
        if not out.get("ok"):
            logger.warning(
                "Executive tool failed type=%s tool=%s err=%s",
                typ,
                tool_name,
                out.get("error"),
            )
            append_event(
                user_id,
                "agent.task.tool_failed",
                {"type": typ, "tool": tool_name, "error": out.get("error")},
            )
            return

        # Best-effort memory note for successful web/fs (legacy behavior).
        result = out.get("result") or {}
        if tool_name == "web.fetch_url" and isinstance(result, dict):
            text = str(result.get("text") or result.get("preview") or "")[:800]
            url = str(args.get("url") or result.get("url") or "")
            if url and text:
                get_memory_core().ingest_fact(
                    user_id,
                    f"Web fetch {url}: {text}",
                    tags=["web", "fetch"],
                    meta={"source": "agent_web_fetch", "status": result.get("status")},
                )
        elif tool_name == "fs.write_file" and isinstance(result, dict) and result.get("ok", True):
            path = str(args.get("path") or "")
            if path:
                get_memory_core().ingest_fact(
                    user_id,
                    f"Zapisano plik: {path}",
                    tags=["fs", "write"],
                    meta={"bytes": result.get("bytes"), "source": "agent_fs_write"},
                )

    except Exception as e:
        logger.error(f"Error executing task: {e}", exc_info=True)
        append_event(user_id, "agent.task.error", {"type": typ, "error": str(e)})


async def _execute_web_fetch(user_id: str, payload: Dict[str, Any]) -> None:
    """Execute web fetch task."""
    url = payload.get("url", "")
    if not url or not _is_valid_url(url):
        logger.warning(f"Invalid URL: {url}")
        return

    try:
        logger.debug(f"Fetching URL: {url}")
        res = await fetch_url(user_id, url)

        if res.get("ok"):
            text = (res.get("text") or "")[:800]
            get_memory_core().ingest_fact(
                user_id,
                f"Web fetch {url}: {text}",
                tags=["web", "fetch"],
                meta={
                    "status": res.get("status"),
                    "bytes": res.get("bytes"),
                    "source": "agent_web_fetch",
                },
            )
            logger.debug(f"Web fetch successful: {url}")
        else:
            logger.warning(f"Web fetch failed: {res.get('error')}")

    except Exception as e:
        logger.error(f"Error fetching URL {url}: {e}", exc_info=True)


async def _execute_fs_write(user_id: str, payload: Dict[str, Any]) -> None:
    """Execute file write task."""
    from aihub.tools.mutation_guard import block_unconfirmed_mutation

    blocked = block_unconfirmed_mutation(
        "fs.write", payload if isinstance(payload, dict) else {}
    )
    if blocked:
        logger.warning(
            "fs.write blocked without confirmation user=%s path=%s",
            user_id,
            (payload or {}).get("path"),
        )
        append_event(
            user_id,
            "fs.write.blocked",
            {"reason": blocked.get("error"), "path": (payload or {}).get("path")},
        )
        return

    path = payload.get("path", "note.txt")
    content = payload.get("content", "")
    overwrite = bool(payload.get("overwrite", True))

    if not path or not _is_valid_path(path):
        logger.warning(f"Invalid path: {path}")
        return

    try:
        logger.debug(f"Writing file: {path}")
        result = write_file(user_id, path, content, overwrite=overwrite)

        if result.get("ok"):
            get_memory_core().ingest_fact(
                user_id,
                f"Zapisano plik: {path}",
                tags=["fs", "write"],
                meta={"bytes": result.get("bytes")},
            )
            logger.debug(f"File write successful: {path}")
        else:
            logger.warning(f"File write failed: {result.get('error')}")

    except Exception as e:
        logger.error(f"Error writing file {path}: {e}", exc_info=True)


async def _execute_snapshot(user_id: str, payload: Dict[str, Any]) -> None:
    """Execute snapshot creation task."""
    from aihub.tools.mutation_guard import block_unconfirmed_mutation

    blocked = block_unconfirmed_mutation(
        "snapshot.create", payload if isinstance(payload, dict) else {}
    )
    if blocked:
        logger.warning(
            "snapshot blocked without confirmation user=%s", user_id
        )
        append_event(
            user_id,
            "snapshot.blocked",
            {"reason": blocked.get("error")},
        )
        return

    reason = payload.get("reason", "agent:auto")

    try:
        logger.debug(f"Creating snapshot for user {user_id}")
        result = create_snapshot(user_id, reason)

        if result:
            get_memory_core().ingest_fact(
                user_id,
                f"Utworzono snapshot: {result.get('snapshot_id')}",
                tags=["system", "snapshot"],
                meta={"snapshot_id": result.get("snapshot_id")},
            )
            logger.debug(f"Snapshot created: {result.get('snapshot_id')}")

    except Exception as e:
        logger.error(f"Error creating snapshot: {e}", exc_info=True)


RESEARCH_RATE_LIMIT_S = 30.0
_research_rate: Dict[str, float] = {}


async def _execute_research(user_id: str, payload: Dict[str, Any]) -> None:
    """Execute research query task with per-user rate limiting."""
    query = payload.get("query", "")
    if not query:
        logger.warning("research.query: empty query")
        return

    # Per-user rate limit
    last_ts = _research_rate.get(user_id, 0.0)
    elapsed = time.time() - last_ts
    if elapsed < RESEARCH_RATE_LIMIT_S:
        logger.warning(
            "research.rate_limited: user=%s, %.1fs since last (limit=%.0fs)",
            user_id,
            elapsed,
            RESEARCH_RATE_LIMIT_S,
        )
        append_event(
            user_id, "agent.research.rate_limited", {"query": query, "elapsed": elapsed}
        )
        return

    _research_rate[user_id] = time.time()

    try:
        from aihub.research_engine import research as do_research

        result = await do_research(user_id, query, research_type="general")
        total_facts = result.get("total_facts", 0)
        total_results = result.get("total_results", 0)
        logger.info(
            "research.query done: user=%s results=%d facts=%d",
            user_id,
            total_results,
            total_facts,
        )
        append_event(
            user_id,
            "agent.research.done",
            {"query": query, "results": total_results, "facts": total_facts},
        )
    except Exception as e:  # noqa: BLE001
        logger.error("research.query failed: %s", e, exc_info=True)
        append_event(user_id, "agent.research.error", {"query": query, "error": str(e)})


def _maybe_gc(user_id: str) -> bool:
    """Run GC if memory pressure is high (>0.7). Returns True if GC ran."""
    try:
        from aihub.db import fetch_one

        row = fetch_one(
            "SELECT COUNT(*) AS cnt FROM memory_nodes WHERE user_id=? AND deleted=0",
            (user_id,),
        )
        count = int(row["cnt"]) if row else 0
        from aihub.config import LTM_MAX_FACTS_PER_USER

        pressure = count / max(LTM_MAX_FACTS_PER_USER, 1)
        if pressure > 0.7:
            from aihub.memory_gc import collect_garbage

            collect_garbage(user_id)
            logger.info("GC triggered for user=%s pressure=%.2f", user_id, pressure)
            return True
    except Exception:  # noqa: BLE001
        logger.debug("_maybe_gc failed", exc_info=True)
    return False


async def run_reactive_tick_cycle(
    user_id: str, max_stm: int = 200, max_tasks: int = 8
) -> Dict[str, Any]:
    """
    Główny tick agenta.

    Kroki:
    1. Pobierz nowe STM messages
    2. Update psyche
    3. Ekstrakcja faktów
    4. Planowanie zadań
    5. Wykonanie zadań
    6. Logging
    """
    try:
        get_psyche_core().ensure_user(user_id)
        st = get_agent_state(user_id)

        if not st.get("enabled", True):
            logger.debug(f"Agent {user_id} is disabled")
            return {
                "ok": True,
                "enabled": False,
                "processed": 0,
                "enqueued": 0,
                "ran": 0,
            }

        # Pull new STM
        new_msgs = _pull_new_stm(user_id, st.get("last_stm_ts", 0), limit=max_stm)

        if not new_msgs:
            logger.debug(f"No new messages for user {user_id}")
            return {
                "ok": True,
                "enabled": True,
                "processed": 0,
                "enqueued": 0,
                "ran": 0,
            }

        # Attention filtering: if too many messages, rank and keep top batch
        ATTENTION_THRESHOLD = 20
        if len(new_msgs) > ATTENTION_THRESHOLD:
            try:
                from aihub.attention_controller import rank_messages

                rankings = rank_messages(user_id, new_msgs)
                new_msgs = [r.message for r in rankings[:ATTENTION_THRESHOLD]]
                logger.info(
                    "attention_filter: user=%s reduced %d→%d msgs",
                    user_id,
                    len(rankings),
                    len(new_msgs),
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "attention_filter failed, using all messages", exc_info=True
                )

        processed = 0
        enqueued = 0

        # Process each message
        for m in new_msgs:
            processed += 1
            role = m.get("role", "user")
            content = m.get("content", "")

            # Update psyche
            try:
                get_psyche_core().evolve(user_id, content, role)
            except Exception as e:
                logger.warning(f"Error evolving psyche: {e}")

            # Extract facts only from user
            if role == "user":
                for fact, tags, meta in extract_facts_from_text(content):
                    try:
                        get_memory_core().ingest_fact(
                            user_id, fact, tags=tags, meta=meta
                        )
                    except Exception as e:
                        logger.warning(f"Error adding fact: {e}")

                # Plan tasks
                for t in plan_from_text(user_id, content):
                    try:
                        enqueue_task(
                            user_id,
                            t["type"],
                            t["payload"],
                            priority=int(t["priority"]),
                        )
                        enqueued += 1
                    except Exception as e:
                        logger.warning(f"Error enqueueing task: {e}")

        # Create episodic snapshot
        try:
            joined = " | ".join(
                [f"{m['role']}:{m['content'][:240]}" for m in new_msgs][-12:]
            )
            get_memory_core().ingest_episode(
                user_id,
                f"Batch STM ({len(new_msgs)}): {joined}",
                meta={"kind": "agent_tick"},
            )
        except Exception as e:
            logger.warning(f"Error adding episode: {e}")

        # Update cursor
        try:
            update_cursor(user_id, new_msgs[-1]["ts"])
        except Exception as e:
            logger.warning(f"Error updating cursor: {e}")

        # Run tasks
        ran = 0
        for _ in range(max_tasks):
            t = None
            try:
                t = claim_next_task(user_id)
                if not t:
                    break

                await execute_task(user_id, t)
                complete_task(t["id"], True)
                ran += 1

            except Exception as e:
                logger.warning(f"Error executing/completing task: {e}")
                if t and t.get("id"):
                    try:
                        complete_task(int(t["id"]), False, str(e))
                    except Exception:
                        logger.debug("Failed to mark task as failed", exc_info=True)
                break

        # Log success
        append_event(
            user_id,
            "agent.tick",
            {"processed": processed, "enqueued": enqueued, "ran": ran},
        )

        # Memory pressure GC
        _maybe_gc(user_id)

        logger.info(
            f"Agent tick for {user_id}: processed={processed}, "
            f"enqueued={enqueued}, ran={ran}"
        )

        return {
            "ok": True,
            "enabled": True,
            "processed": processed,
            "enqueued": enqueued,
            "ran": ran,
            "ts": now_ts(),
        }

    except Exception as e:
        logger.error(f"Error in agent_tick for user {user_id}: {e}", exc_info=True)
        append_event(user_id, "agent.tick.error", {"error": str(e)})
        return {
            "ok": False,
            "error": str(e),
            "processed": 0,
            "enqueued": 0,
            "ran": 0,
            "ts": now_ts(),
        }


async def agent_tick(
    user_id: str, max_stm: int = 200, max_tasks: int = 8
) -> Dict[str, Any]:
    """Canonical adapter for tick execution via ExecutiveController.

    Backward compatibility:
    - preserves legacy response shape by returning `legacy_response` when available.
    """
    from aihub.executive_controller import get_executive_controller

    controller = get_executive_controller()
    cycle = await controller.run_cycle(
        {
            "max_stm": int(max_stm),
            "max_tasks": int(max_tasks),
        },
        mode="tick",
        user_id=user_id,
    )

    legacy = cycle.get("legacy_response")
    if isinstance(legacy, dict):
        return legacy

    payload = cycle.get("execution_result", {}).get("payload")
    if isinstance(payload, dict):
        return payload

    return {
        "ok": bool(cycle.get("ok", False)),
        "processed": 0,
        "enqueued": 0,
        "ran": 0,
        "ts": now_ts(),
        "strategy": cycle.get("strategy", ""),
    }
