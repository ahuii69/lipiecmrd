#!/usr/bin/env python3
"""
Memory V2 procedural learning.

Extracts procedural patterns from experiences and builds learned workflows.
"""

import logging
import re
import time
import uuid
from typing import Any

from aihub.db import get_experiences_by_user
from aihub.memory_v2_models import MemoryV2Procedure
from aihub.memory_v2_repository import (
    get_procedures_for_user,
    insert_memory_procedure,
    update_memory_procedure,
    find_matching_procedures,
)

logger = logging.getLogger(__name__)

_USER_PROC_MARKERS = (
    "procedur",
    "zapamiętaj procedur",
    "zapamietaj procedur",
    "odpowiadaj zawsze",
    "gdy proszę",
    "gdy prosze",
    "zmień procedur",
    "zmien procedur",
    "nie stosuj już",
    "nie stosuj juz",
)
_PROC_TRIGGER_TOKENS = (
    "debug",
    "502",
    "backend",
    "serwer",
    "server",
    "nginx",
    "logi",
    "port",
)


def _norm_tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9ąćęłńóśźż_-]{3,}", (text or "").lower())
        if t not in {"oraz", "potem", "najpierw", "zawsze", "dla", "tego", "testow"}
    }


def rank_procedures_for_query(
    user_id: str, query: str, *, limit: int = 3
) -> list[MemoryV2Procedure]:
    """Prefer query-overlapping, high-confidence, recently updated procedures."""
    procs = get_procedures_for_user(user_id, limit=50)
    if not procs:
        return []
    q_tokens = _norm_tokens(query)
    scored: list[tuple[float, MemoryV2Procedure]] = []
    for p in procs:
        if float(p.confidence_score or 0) < 0.15:
            continue
        blob = f"{p.name} {p.trigger_pattern} {p.recommended_strategy}".lower()
        p_tokens = _norm_tokens(blob)
        overlap = len(q_tokens & p_tokens) if q_tokens else 0
        # User-declared procedures (evidence_count==1, explicit steps) get a boost
        # when any debug/backend token matches.
        user_declared = p.evidence_count <= 2 and any(
            k in blob for k in ("najpierw", "potem", "→", "->", "logi", "port")
        )
        trigger_hit = any(t in blob for t in _PROC_TRIGGER_TOKENS if t in (query or "").lower())
        score = (
            float(p.confidence_score or 0) * 2.0
            + overlap * 0.35
            + (0.8 if user_declared and (trigger_hit or overlap > 0) else 0.0)
            + min(0.4, max(0.0, (float(p.updated_ts or 0) / 1e12)))
        )
        if q_tokens and overlap == 0 and not trigger_hit and not user_declared:
            # Avoid dumping unrelated learned workflows into every turn.
            continue
        scored.append((score, p))
    scored.sort(key=lambda x: (x[0], float(x[1].updated_ts or 0)), reverse=True)
    if scored:
        return [p for _, p in scored[:limit]]
    # Fallback: newest high-confidence only (still bounded).
    alive = [p for p in procs if float(p.confidence_score or 0) >= 0.4]
    alive.sort(key=lambda p: float(p.updated_ts or 0), reverse=True)
    return alive[:limit]


def upsert_user_declared_procedure(user_id: str, text: str) -> MemoryV2Procedure | None:
    """Persist explicit user procedure / supersede prior matching procedure."""
    raw = (text or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if not any(m in low for m in _USER_PROC_MARKERS):
        return None
    # Questions / recall prompts must not create new procedure rows.
    if raw.endswith("?") or re.match(
        r"(?iu)^(jak|czego|czym|podaj|opisz|przypomnij|co\s+z)\b", raw
    ):
        if not any(
            m in low
            for m in (
                "zapamiętaj",
                "zapamietaj",
                "zmień procedur",
                "zmien procedur",
                "odpowiadaj zawsze",
                "gdy proszę",
                "gdy prosze",
            )
        ):
            return None

    now = time.time()
    supersede = any(
        m in low
        for m in (
            "zmień procedur",
            "zmien procedur",
            "nie stosuj już",
            "nie stosuj juz",
            "poprawk",
        )
    )
    trigger_bits = [t for t in _PROC_TRIGGER_TOKENS if t in low]
    if "profile26" in low or "profile26" in low.replace("-", ""):
        trigger_bits.append("profile26")
    if not trigger_bits:
        trigger_bits = ["procedure", "workflow"]
    trigger_pattern = " ".join(dict.fromkeys(trigger_bits))

    # Supersede older overlapping user procedures.
    if supersede or "zapamiętaj" in low or "zapamietaj" in low:
        for old in get_procedures_for_user(user_id, limit=30):
            old_blob = f"{old.trigger_pattern} {old.name}".lower()
            if any(t in old_blob for t in trigger_bits):
                if float(old.confidence_score or 0) >= 0.2:
                    old.confidence_score = 0.05
                    old.success_rate = min(old.success_rate, 0.1)
                    old.updated_ts = now
                    update_memory_procedure(old)

    name = raw[:120].rstrip(".")
    if "profile26" in low:
        name = "Profile26 debug serwera: " + raw[:90]
    proc = MemoryV2Procedure(
        id=f"proc-user-{uuid.uuid4().hex[:16]}",
        user_id=user_id,
        name=name,
        trigger_pattern=trigger_pattern,
        recommended_strategy=raw[:900],
        recommended_tools=[],
        avoid_patterns=["user_declared"],
        success_rate=0.9,
        failure_rate=0.0,
        confidence_score=0.92,
        evidence_count=1,
        last_validated_ts=now,
        created_ts=now,
        updated_ts=now,
    )
    if insert_memory_procedure(proc):
        logger.info(
            "user_declared_procedure stored id=%s trigger=%s supersede=%s",
            proc.id,
            trigger_pattern,
            supersede,
        )
        return proc
    return None


def extract_procedures_from_experiences(
    user_id: str, min_evidence: int = 3
) -> list[MemoryV2Procedure]:
    """
    Analyze recent experiences and extract procedural patterns.

    Returns list of learned procedures.
    """
    experiences = get_experiences_by_user(user_id, limit=100)

    if len(experiences) < min_evidence:
        logger.debug(f"Insufficient experiences for procedural extraction: {len(experiences)}")
        return []

    # Group by selected_strategy
    strategy_groups: dict[str, list[dict[str, Any]]] = {}
    for exp in experiences:
        strategy = exp.get("selected_strategy", "unknown")
        if strategy not in strategy_groups:
            strategy_groups[strategy] = []
        strategy_groups[strategy].append(exp)

    procedures = []
    for strategy, exps in strategy_groups.items():
        if len(exps) < min_evidence:
            continue

        procedure = _build_procedure_from_experiences(user_id, strategy, exps)
        if procedure:
            procedures.append(procedure)

    logger.info(f"Extracted {len(procedures)} procedures for user {user_id}")
    return procedures


def _build_procedure_from_experiences(
    user_id: str, strategy: str, experiences: list[dict[str, Any]]
) -> MemoryV2Procedure | None:
    """Build a procedure from grouped experiences."""
    if not experiences:
        return None

    success_count = sum(1 for exp in experiences if exp.get("success", False))
    failure_count = len(experiences) - success_count

    total = len(experiences)
    success_rate = success_count / total if total > 0 else 0.0
    failure_rate = failure_count / total if total > 0 else 0.0

    # Extract common tools
    tools_used = set()
    for exp in experiences:
        if exp.get("tools_executed", False):
            for key in ("tools", "tool_calls", "executed_tools"):
                value = exp.get(key)
                if isinstance(value, str) and value.strip():
                    tools_used.add(value.strip())
                elif isinstance(value, (list, tuple, set)):
                    tools_used.update(str(item).strip() for item in value if str(item).strip())
            metadata = exp.get("metadata") or exp.get("trace") or {}
            if isinstance(metadata, dict):
                for key in ("tools", "tool_calls", "executed_tools"):
                    value = metadata.get(key)
                    if isinstance(value, str) and value.strip():
                        tools_used.add(value.strip())
                    elif isinstance(value, (list, tuple, set)):
                        tools_used.update(str(item).strip() for item in value if str(item).strip())

    # Build trigger pattern from input summaries
    input_summaries = [exp.get("user_input_summary", "") for exp in experiences]
    trigger_pattern = _extract_common_pattern(input_summaries)

    now = time.time()
    procedure_id = f"proc-{uuid.uuid4().hex[:16]}"

    procedure = MemoryV2Procedure(
        id=procedure_id,
        user_id=user_id,
        name=f"Learned workflow: {strategy}",
        trigger_pattern=trigger_pattern,
        recommended_strategy=strategy,
        recommended_tools=list(tools_used),
        avoid_patterns=[],
        success_rate=success_rate,
        failure_rate=failure_rate,
        confidence_score=min(0.9, success_rate + (total * 0.05)),
        evidence_count=total,
        last_validated_ts=now,
        created_ts=now,
        updated_ts=now,
    )

    return procedure


def _extract_common_pattern(texts: list[str]) -> str:
    """Extract common pattern from list of texts."""
    if not texts:
        return "unknown"

    # Simple heuristic: most common words
    word_freq: dict[str, int] = {}
    for text in texts:
        words = text.lower().split()
        for word in words:
            if len(word) > 3:
                word_freq[word] = word_freq.get(word, 0) + 1

    if not word_freq:
        return "general task"

    top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:3]
    return " ".join([word for word, _ in top_words])


def update_procedure_from_feedback(
    user_id: str, strategy: str, success: bool, latency_ms: float | None = None
) -> bool:
    """
    Update procedure statistics based on new execution feedback.

    Returns True if procedure was updated.
    """
    procedures = find_matching_procedures(user_id, trigger_pattern=strategy, limit=1)
    if not procedures:
        logger.debug(f"No matching procedure found for strategy {strategy}")
        return False

    procedure = procedures[0]

    # Update rates
    total = procedure.evidence_count
    new_total = total + 1

    if success:
        new_success_count = int(procedure.success_rate * total) + 1
        procedure.success_rate = new_success_count / new_total
    else:
        new_failure_count = int(procedure.failure_rate * total) + 1
        procedure.failure_rate = new_failure_count / new_total

    procedure.evidence_count = new_total
    procedure.confidence_score = min(0.95, procedure.success_rate + (new_total * 0.03))
    procedure.last_validated_ts = time.time()
    procedure.updated_ts = time.time()

    return update_memory_procedure(procedure)
